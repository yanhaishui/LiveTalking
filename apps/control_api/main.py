"""LiveTalking 控制层 API。

本模块提供:
- 模型资产 CRUD（Avatar / Voice）
- 脚本与轮播 CRUD
- 直播预设 CRUD
- 直播进程启停与日志查询

说明:
- 第一版聚焦 `wav2lip + virtualcam` 链路。
- 后续可扩展任务中心、平台消息接入和智能回复编排。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlparse, unquote
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
import requests

from .config import settings
from .database import execute, init_db, now_ts, query_all, query_one
from .job_runner import job_runner
from .live_runtime import live_manager
from .speaker_dispatcher import speaker_dispatcher
from .schemas import (
    ApiMessage,
    AvatarCloneRequest,
    AvatarCreate,
    AvatarUpdate,
    JobCancelRequest,
    JobCreate,
    LiveStartRequest,
    LiveStopRequest,
    PlaylistCreate,
    PlaylistItemCreate,
    PlaylistUpdate,
    PresetCreate,
    PresetUpdate,
    ManualSpeakRequest,
    PlatformMessagesIngestRequest,
    ReplyGenerateRequest,
    ReplySpeakRequest,
    RoomMessagesIngestRequest,
    ScriptCreate,
    ScriptUpdate,
    VoiceCloneRequest,
    VoiceCreate,
    VoicePreviewRequest,
    VoiceUpdate,
)


def _uid(prefix: str) -> str:
    """生成业务主键。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


def _json_loads(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _decode_avatar(row: dict[str, object]) -> dict[str, object]:
    """将数据库记录转换为 API 响应结构。"""

    row["tags"] = _json_loads(row.get("tags"), [])
    row["meta"] = _json_loads(row.get("meta_json"), {})
    row.pop("meta_json", None)
    return row


def _decode_voice(row: dict[str, object]) -> dict[str, object]:
    profile = _json_loads(row.get("profile_json"), {})
    row["profile"] = profile
    if not row.get("preview_wav_path"):
        preview = profile.get("preview_wav_path") if isinstance(profile, dict) else None
        row["preview_wav_path"] = preview
    row.pop("profile_json", None)
    return row


def _decode_preset(row: dict[str, object]) -> dict[str, object]:
    row["extra_args"] = _json_loads(row.get("extra_args"), [])
    return row


def _rule_generate_reply_text(content: str, fallback_text: str | None = None) -> str:
    """规则模板回复。

    说明:
    - 第一版先用稳定、可解释的规则回复，便于线上兜底。
    - 后续可替换为 LLM（RAG/知识库）生成。
    """

    text = content.strip()
    if not text:
        return fallback_text or "欢迎提问，我来为你介绍直播间的产品与服务。"

    if any(k in text for k in ("价格", "多少钱", "优惠", "折扣")):
        return "这款产品现在有直播专享活动价，我可以按你的需求给你推荐具体规格和价格。"
    if any(k in text for k in ("发货", "物流", "多久到", "几天到")):
        return "我们支持常规快递发货，下单后会尽快安排出库，具体时效会根据你的地区自动计算。"
    if any(k in text for k in ("售后", "退货", "退款", "质保")):
        return "售后与退换有标准流程，出现问题可第一时间联系直播间，我们会按规则快速处理。"

    return fallback_text or f"收到你的问题：{text}。我先为你做重点介绍，如需细节可以继续追问。"


app = FastAPI(title="LiveTalking Control API", version="0.1.0")

# 允许本地管理台跨域调用控制接口。
# 当前是本地单机部署场景，直接放开以避免浏览器预检失败。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AVATAR_UPLOAD_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
_VOICE_UPLOAD_EXTS = {".wav"}
_AVATAR_UPLOAD_MAX_BYTES = 1024 * 1024 * 1024  # 1GB
_VOICE_UPLOAD_MAX_BYTES = 100 * 1024 * 1024  # 100MB


async def _store_uploaded_file(
    request: Request,
    target_dir: Path,
    allowed_exts: set[str],
    max_bytes: int,
    kind_label: str,
) -> dict[str, object]:
    """保存上传文件并返回落盘结果。

    说明:
    - 浏览器安全限制下，前端无法拿到本机绝对路径；
      因此先上传到 control_api 主机本地，再把目标路径回填到克隆任务。
    - 采用分块写入并限制大小，避免一次性读入大文件导致内存占用过高。
    """

    raw_filename = request.headers.get("x-filename", "").strip()
    filename = Path(unquote(raw_filename)).name
    if not filename:
        raise HTTPException(status_code=400, detail="缺少 x-filename 请求头")

    suffix = Path(filename).suffix.lower()
    if not suffix or suffix not in allowed_exts:
        allow_text = ", ".join(sorted(allowed_exts))
        raise HTTPException(status_code=400, detail=f"{kind_label} 文件格式不支持，仅支持: {allow_text}")

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    saved_name = f"{stamp}_{uuid.uuid4().hex[:10]}{suffix}"
    target_path = target_dir / saved_name

    total = 0
    try:
        with target_path.open("wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=f"{kind_label} 文件过大，最大 {max_bytes // 1024 // 1024}MB")
                f.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    if total <= 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{kind_label} 文件为空")

    return {
        "filename": filename,
        "saved_name": saved_name,
        "path": str(target_path.resolve()),
        "size_bytes": total,
    }


def _check_tcp_port(host: str, port: int, timeout_sec: float = 0.8) -> bool:
    """检测 TCP 端口可达性。"""

    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            return True
    except Exception:
        return False


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库。"""

    init_db()
    job_runner.start()
    speaker_dispatcher.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    """应用关闭时停止后台线程。"""

    speaker_dispatcher.stop()


@app.get("/api/v1/health")
def health() -> dict[str, object]:
    """健康检查接口。"""

    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "db": str(settings.db_path),
    }


@app.get("/api/v1/capabilities")
def capabilities() -> dict[str, object]:
    """返回当前环境能力信息。"""

    avatars_dir = settings.project_root / "data" / "avatars"
    avatars_count = 0
    if avatars_dir.exists():
        avatars_count = len([p for p in avatars_dir.iterdir() if p.is_dir()])

    has_virtualcam = False
    try:
        import pyvirtualcam  # type: ignore  # noqa: F401

        has_virtualcam = True
    except Exception:
        has_virtualcam = False

    return {
        "project_root": str(settings.project_root),
        "app_entry_exists": settings.app_entry.exists(),
        "python_exec": settings.python_exec,
        "virtualcam_supported": has_virtualcam,
        "avatars_count": avatars_count,
        "live_running": live_manager.is_running(),
        "speaker_dispatcher": speaker_dispatcher.status(),
    }


@app.get("/api/v1/system/checks")
def system_checks(
    tts_server: str = Query(default="http://127.0.0.1:9000"),
    listen_port: int = Query(default=8010, ge=1, le=65535),
) -> dict[str, object]:
    """系统体检（可用于开播前自检）。"""

    checks: list[dict[str, object]] = []

    def add_check(
        key: str,
        label: str,
        status: str,
        detail: str,
        suggestion: str = "",
        meta: dict[str, object] | None = None,
    ) -> None:
        checks.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "detail": detail,
                "suggestion": suggestion,
                "meta": meta or {},
            }
        )

    wav2lip_model = settings.project_root / "models" / "wav2lip.pth"
    add_check(
        key="model_wav2lip",
        label="wav2lip 模型文件",
        status="ok" if wav2lip_model.exists() else "error",
        detail=str(wav2lip_model),
        suggestion="" if wav2lip_model.exists() else "请将 wav2lip 权重文件放到 models/wav2lip.pth",
    )

    avatars_dir = settings.data_dir / "avatars"
    avatar_count = 0
    if avatars_dir.exists():
        avatar_count = len([p for p in avatars_dir.iterdir() if p.is_dir()])
    add_check(
        key="avatar_assets",
        label="数字人形象资产",
        status="ok" if avatar_count > 0 else "warn",
        detail=f"目录: {avatars_dir}，数量: {avatar_count}",
        suggestion="" if avatar_count > 0 else "请先准备至少 1 个 Avatar 资产",
        meta={"count": avatar_count},
    )

    uploads_dir = settings.data_dir / "uploads"
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(uploads_dir, os.W_OK)
    except Exception:
        writable = False
    add_check(
        key="uploads_dir",
        label="上传目录可写",
        status="ok" if writable else "error",
        detail=str(uploads_dir),
        suggestion="" if writable else "请检查 data/uploads 目录权限",
    )

    # XTTS 连通性检查
    parsed = urlparse(tts_server)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    tcp_ok = _check_tcp_port(host, int(port))
    if not tcp_ok:
        add_check(
            key="xtts_port",
            label="XTTS 端口连通",
            status="error",
            detail=f"{host}:{port} 不可达",
            suggestion="请先启动 XTTS 服务，或修正 TTS_SERVER 地址",
        )
    else:
        add_check(
            key="xtts_port",
            label="XTTS 端口连通",
            status="ok",
            detail=f"{host}:{port} 可达",
        )

        try:
            url = tts_server.rstrip("/") + "/languages"
            resp = requests.get(url, timeout=2.5)
            if resp.status_code >= 400:
                add_check(
                    key="xtts_http",
                    label="XTTS HTTP 接口",
                    status="warn",
                    detail=f"{url} 返回 HTTP {resp.status_code}",
                    suggestion="服务可达但接口异常，请确认 XTTS 服务版本和路由",
                )
            else:
                add_check(
                    key="xtts_http",
                    label="XTTS HTTP 接口",
                    status="ok",
                    detail=f"{url} 正常",
                )
        except Exception as exc:  # noqa: BLE001
            add_check(
                key="xtts_http",
                label="XTTS HTTP 接口",
                status="warn",
                detail=f"请求失败: {exc}",
                suggestion="请检查 XTTS 服务日志",
            )

    # 直播 listen 端口是否已被占用（占用时通常会导致开播失败）
    port_busy = _check_tcp_port("127.0.0.1", listen_port)
    add_check(
        key="listen_port",
        label="直播监听端口",
        status="warn" if port_busy else "ok",
        detail=f"127.0.0.1:{listen_port} {'已占用' if port_busy else '可用'}",
        suggestion="请改用其他端口，或停止占用该端口的进程" if port_busy else "",
    )

    summary = {
        "ok": len([c for c in checks if c["status"] == "ok"]),
        "warn": len([c for c in checks if c["status"] == "warn"]),
        "error": len([c for c in checks if c["status"] == "error"]),
    }

    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "summary": summary,
    }


@app.post("/api/v1/uploads/avatar-video")
async def upload_avatar_video(request: Request) -> dict[str, object]:
    """上传形象克隆视频素材。"""

    target_dir = settings.data_dir / "uploads" / "avatars"
    result = await _store_uploaded_file(
        request=request,
        target_dir=target_dir,
        allowed_exts=_AVATAR_UPLOAD_EXTS,
        max_bytes=_AVATAR_UPLOAD_MAX_BYTES,
        kind_label="视频",
    )
    return result


@app.post("/api/v1/uploads/voice-wav")
async def upload_voice_wav(request: Request) -> dict[str, object]:
    """上传声音克隆参考音频。"""

    target_dir = settings.data_dir / "uploads" / "voices"
    result = await _store_uploaded_file(
        request=request,
        target_dir=target_dir,
        allowed_exts=_VOICE_UPLOAD_EXTS,
        max_bytes=_VOICE_UPLOAD_MAX_BYTES,
        kind_label="音频",
    )
    return result


# =========================
# Avatar CRUD
# =========================


@app.post("/api/v1/avatars", response_model=ApiMessage)
def create_avatar(payload: AvatarCreate) -> ApiMessage:
    avatar_id = _uid("avatar")
    ts = now_ts()
    execute(
        """
        INSERT INTO avatars (id, name, avatar_path, cover_image, status, tags, meta_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ready', ?, ?, ?, ?)
        """,
        (
            avatar_id,
            payload.name,
            payload.avatar_path,
            payload.cover_image,
            _json_dumps(payload.tags),
            _json_dumps(payload.meta),
            ts,
            ts,
        ),
    )
    return ApiMessage(message=avatar_id)


@app.post("/api/v1/avatars:clone", response_model=ApiMessage)
def clone_avatar(payload: AvatarCloneRequest) -> ApiMessage:
    """提交 wav2lip 视频克隆任务。"""

    job_id = job_runner.create_job("avatar.clone.wav2lip", payload.model_dump())
    return ApiMessage(message=job_id)


@app.get("/api/v1/avatars")
def list_avatars() -> list[dict[str, object]]:
    rows = query_all("SELECT * FROM avatars ORDER BY created_at DESC")
    return [_decode_avatar(r) for r in rows]


@app.get("/api/v1/avatars/{avatar_id}")
def get_avatar(avatar_id: str) -> dict[str, object]:
    row = query_one("SELECT * FROM avatars WHERE id = ?", (avatar_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Avatar 不存在")
    return _decode_avatar(row)


@app.patch("/api/v1/avatars/{avatar_id}", response_model=ApiMessage)
def update_avatar(avatar_id: str, payload: AvatarUpdate) -> ApiMessage:
    current = query_one("SELECT * FROM avatars WHERE id = ?", (avatar_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Avatar 不存在")

    updated = {
        "name": payload.name if payload.name is not None else current["name"],
        "cover_image": payload.cover_image if payload.cover_image is not None else current["cover_image"],
        "status": payload.status if payload.status is not None else current["status"],
        "tags": _json_dumps(payload.tags) if payload.tags is not None else current["tags"],
        "meta_json": _json_dumps(payload.meta) if payload.meta is not None else current["meta_json"],
        "updated_at": now_ts(),
    }

    execute(
        """
        UPDATE avatars
        SET name = ?, cover_image = ?, status = ?, tags = ?, meta_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            updated["name"],
            updated["cover_image"],
            updated["status"],
            updated["tags"],
            updated["meta_json"],
            updated["updated_at"],
            avatar_id,
        ),
    )
    return ApiMessage(message="ok")


@app.delete("/api/v1/avatars/{avatar_id}", response_model=ApiMessage)
def delete_avatar(avatar_id: str) -> ApiMessage:
    # 删除前做引用检查，防止预设引用失效。
    ref = query_one("SELECT id FROM live_presets WHERE avatar_id = ? LIMIT 1", (avatar_id,))
    if ref:
        raise HTTPException(status_code=409, detail="该 Avatar 正被直播预设引用，不能删除")

    count = execute("DELETE FROM avatars WHERE id = ?", (avatar_id,))
    if count == 0:
        raise HTTPException(status_code=404, detail="Avatar 不存在")
    return ApiMessage(message="ok")


# =========================
# Voice CRUD
# =========================


@app.post("/api/v1/voices", response_model=ApiMessage)
def create_voice(payload: VoiceCreate) -> ApiMessage:
    voice_id = _uid("voice")
    ts = now_ts()
    profile_json = _json_dumps(payload.profile)
    preview_wav_path = None
    if isinstance(payload.profile, dict):
        preview_wav_path = payload.profile.get("preview_wav_path")

    execute(
        """
        INSERT INTO voices (id, name, engine, ref_wav_path, profile_json, preview_wav_path, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)
        """,
        (
            voice_id,
            payload.name,
            payload.engine,
            payload.ref_wav_path,
            profile_json,
            preview_wav_path,
            ts,
            ts,
        ),
    )
    return ApiMessage(message=voice_id)


@app.post("/api/v1/voices:clone", response_model=ApiMessage)
def clone_voice(payload: VoiceCloneRequest) -> ApiMessage:
    """提交声音克隆任务。"""

    job_id = job_runner.create_job("voice.clone.xtts", payload.model_dump())
    return ApiMessage(message=job_id)


@app.get("/api/v1/voices")
def list_voices() -> list[dict[str, object]]:
    rows = query_all("SELECT * FROM voices ORDER BY created_at DESC")
    return [_decode_voice(r) for r in rows]


@app.get("/api/v1/voices/{voice_id}")
def get_voice(voice_id: str) -> dict[str, object]:
    row = query_one("SELECT * FROM voices WHERE id = ?", (voice_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Voice 不存在")
    return _decode_voice(row)


@app.patch("/api/v1/voices/{voice_id}", response_model=ApiMessage)
def update_voice(voice_id: str, payload: VoiceUpdate) -> ApiMessage:
    current = query_one("SELECT * FROM voices WHERE id = ?", (voice_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Voice 不存在")

    next_profile = payload.profile if payload.profile is not None else _json_loads(current.get("profile_json"), {})
    next_preview = current.get("preview_wav_path")
    if isinstance(next_profile, dict) and next_profile.get("preview_wav_path"):
        next_preview = next_profile.get("preview_wav_path")

    execute(
        """
        UPDATE voices
        SET name = ?, engine = ?, ref_wav_path = ?, profile_json = ?, preview_wav_path = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name if payload.name is not None else current["name"],
            payload.engine if payload.engine is not None else current["engine"],
            payload.ref_wav_path if payload.ref_wav_path is not None else current["ref_wav_path"],
            _json_dumps(next_profile),
            next_preview,
            payload.status if payload.status is not None else current["status"],
            now_ts(),
            voice_id,
        ),
    )
    return ApiMessage(message="ok")


@app.delete("/api/v1/voices/{voice_id}", response_model=ApiMessage)
def delete_voice(voice_id: str) -> ApiMessage:
    ref = query_one("SELECT id FROM live_presets WHERE voice_id = ? LIMIT 1", (voice_id,))
    if ref:
        raise HTTPException(status_code=409, detail="该 Voice 正被直播预设引用，不能删除")

    count = execute("DELETE FROM voices WHERE id = ?", (voice_id,))
    if count == 0:
        raise HTTPException(status_code=404, detail="Voice 不存在")
    return ApiMessage(message="ok")


@app.post("/api/v1/voices/{voice_id}:preview")
def preview_voice(voice_id: str, payload: VoicePreviewRequest) -> dict[str, object]:
    """为指定声音资产生成试听音频。"""

    if not query_one("SELECT id FROM voices WHERE id = ?", (voice_id,)):
        raise HTTPException(status_code=404, detail="Voice 不存在")

    params = {
        "temperature": payload.temperature,
        "speed": payload.speed,
        "top_k": payload.top_k,
        "top_p": payload.top_p,
        "repetition_penalty": payload.repetition_penalty,
    }
    params = {k: v for k, v in params.items() if v is not None}

    try:
        result = job_runner.preview_voice(
            voice_id=voice_id,
            tts_server=payload.tts_server,
            text=payload.text,
            language=payload.language,
            stream_chunk_size=payload.stream_chunk_size,
            xtts_params=params,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


# =========================
# Script CRUD
# =========================


@app.post("/api/v1/scripts", response_model=ApiMessage)
def create_script(payload: ScriptCreate) -> ApiMessage:
    script_id = _uid("script")
    ts = now_ts()
    execute(
        """
        INSERT INTO scripts (id, title, content, category, priority, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            script_id,
            payload.title,
            payload.content,
            payload.category,
            payload.priority,
            1 if payload.enabled else 0,
            ts,
            ts,
        ),
    )
    return ApiMessage(message=script_id)


@app.get("/api/v1/scripts")
def list_scripts() -> list[dict[str, object]]:
    return query_all("SELECT * FROM scripts ORDER BY created_at DESC")


@app.patch("/api/v1/scripts/{script_id}", response_model=ApiMessage)
def update_script(script_id: str, payload: ScriptUpdate) -> ApiMessage:
    current = query_one("SELECT * FROM scripts WHERE id = ?", (script_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Script 不存在")

    execute(
        """
        UPDATE scripts
        SET title = ?, content = ?, category = ?, priority = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.title if payload.title is not None else current["title"],
            payload.content if payload.content is not None else current["content"],
            payload.category if payload.category is not None else current["category"],
            payload.priority if payload.priority is not None else current["priority"],
            (1 if payload.enabled else 0) if payload.enabled is not None else current["enabled"],
            now_ts(),
            script_id,
        ),
    )
    return ApiMessage(message="ok")


@app.delete("/api/v1/scripts/{script_id}", response_model=ApiMessage)
def delete_script(script_id: str) -> ApiMessage:
    ref = query_one("SELECT id FROM playlist_items WHERE script_id = ? LIMIT 1", (script_id,))
    if ref:
        raise HTTPException(status_code=409, detail="该 Script 正被轮播计划引用，不能删除")

    count = execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    if count == 0:
        raise HTTPException(status_code=404, detail="Script 不存在")
    return ApiMessage(message="ok")


# =========================
# Playlist CRUD
# =========================


@app.post("/api/v1/playlists", response_model=ApiMessage)
def create_playlist(payload: PlaylistCreate) -> ApiMessage:
    playlist_id = _uid("playlist")
    ts = now_ts()
    execute(
        """
        INSERT INTO playlists (id, name, mode, interval_sec, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            playlist_id,
            payload.name,
            payload.mode,
            payload.interval_sec,
            1 if payload.enabled else 0,
            ts,
            ts,
        ),
    )
    return ApiMessage(message=playlist_id)


@app.get("/api/v1/playlists")
def list_playlists() -> list[dict[str, object]]:
    rows = query_all("SELECT * FROM playlists ORDER BY created_at DESC")
    for row in rows:
        row["items"] = query_all(
            """
            SELECT pi.*, s.title AS script_title
            FROM playlist_items pi
            JOIN scripts s ON s.id = pi.script_id
            WHERE pi.playlist_id = ?
            ORDER BY pi.sort_order ASC, pi.created_at ASC
            """,
            (row["id"],),
        )
    return rows


@app.patch("/api/v1/playlists/{playlist_id}", response_model=ApiMessage)
def update_playlist(playlist_id: str, payload: PlaylistUpdate) -> ApiMessage:
    current = query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Playlist 不存在")

    execute(
        """
        UPDATE playlists
        SET name = ?, mode = ?, interval_sec = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name if payload.name is not None else current["name"],
            payload.mode if payload.mode is not None else current["mode"],
            payload.interval_sec if payload.interval_sec is not None else current["interval_sec"],
            (1 if payload.enabled else 0) if payload.enabled is not None else current["enabled"],
            now_ts(),
            playlist_id,
        ),
    )
    return ApiMessage(message="ok")


@app.delete("/api/v1/playlists/{playlist_id}", response_model=ApiMessage)
def delete_playlist(playlist_id: str) -> ApiMessage:
    execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    count = execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    if count == 0:
        raise HTTPException(status_code=404, detail="Playlist 不存在")
    return ApiMessage(message="ok")


@app.post("/api/v1/playlists/{playlist_id}/items", response_model=ApiMessage)
def add_playlist_item(playlist_id: str, payload: PlaylistItemCreate) -> ApiMessage:
    if not query_one("SELECT id FROM playlists WHERE id = ?", (playlist_id,)):
        raise HTTPException(status_code=404, detail="Playlist 不存在")
    if not query_one("SELECT id FROM scripts WHERE id = ?", (payload.script_id,)):
        raise HTTPException(status_code=404, detail="Script 不存在")

    item_id = _uid("pli")
    ts = now_ts()
    execute(
        """
        INSERT INTO playlist_items (id, playlist_id, script_id, sort_order, weight, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            playlist_id,
            payload.script_id,
            payload.sort_order,
            payload.weight,
            ts,
            ts,
        ),
    )
    return ApiMessage(message=item_id)


@app.delete("/api/v1/playlists/{playlist_id}/items/{item_id}", response_model=ApiMessage)
def delete_playlist_item(playlist_id: str, item_id: str) -> ApiMessage:
    count = execute("DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?", (item_id, playlist_id))
    if count == 0:
        raise HTTPException(status_code=404, detail="Playlist Item 不存在")
    return ApiMessage(message="ok")


# =========================
# Preset CRUD
# =========================


@app.post("/api/v1/live/presets", response_model=ApiMessage)
def create_preset(payload: PresetCreate) -> ApiMessage:
    if not query_one("SELECT id FROM avatars WHERE id = ?", (payload.avatar_id,)):
        raise HTTPException(status_code=404, detail="Avatar 不存在")
    if payload.voice_id and not query_one("SELECT id FROM voices WHERE id = ?", (payload.voice_id,)):
        raise HTTPException(status_code=404, detail="Voice 不存在")

    preset_id = _uid("preset")
    ts = now_ts()
    execute(
        """
        INSERT INTO live_presets
        (id, name, avatar_id, voice_id, model, transport, listen_port, tts, tts_server, ref_file, ref_text, extra_args, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            preset_id,
            payload.name,
            payload.avatar_id,
            payload.voice_id,
            payload.model,
            payload.transport,
            payload.listen_port,
            payload.tts,
            payload.tts_server,
            payload.ref_file,
            payload.ref_text,
            _json_dumps(payload.extra_args),
            ts,
            ts,
        ),
    )
    return ApiMessage(message=preset_id)


@app.get("/api/v1/live/presets")
def list_presets() -> list[dict[str, object]]:
    rows = query_all("SELECT * FROM live_presets ORDER BY created_at DESC")
    return [_decode_preset(r) for r in rows]


@app.patch("/api/v1/live/presets/{preset_id}", response_model=ApiMessage)
def update_preset(preset_id: str, payload: PresetUpdate) -> ApiMessage:
    current = query_one("SELECT * FROM live_presets WHERE id = ?", (preset_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Preset 不存在")

    avatar_id = payload.avatar_id if payload.avatar_id is not None else current["avatar_id"]
    voice_id = payload.voice_id if payload.voice_id is not None else current["voice_id"]
    if avatar_id and not query_one("SELECT id FROM avatars WHERE id = ?", (avatar_id,)):
        raise HTTPException(status_code=404, detail="Avatar 不存在")
    if voice_id and not query_one("SELECT id FROM voices WHERE id = ?", (voice_id,)):
        raise HTTPException(status_code=404, detail="Voice 不存在")

    execute(
        """
        UPDATE live_presets
        SET name = ?, avatar_id = ?, voice_id = ?, model = ?, transport = ?, listen_port = ?,
            tts = ?, tts_server = ?, ref_file = ?, ref_text = ?, extra_args = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name if payload.name is not None else current["name"],
            avatar_id,
            voice_id,
            payload.model if payload.model is not None else current["model"],
            payload.transport if payload.transport is not None else current["transport"],
            payload.listen_port if payload.listen_port is not None else current["listen_port"],
            payload.tts if payload.tts is not None else current["tts"],
            payload.tts_server if payload.tts_server is not None else current["tts_server"],
            payload.ref_file if payload.ref_file is not None else current["ref_file"],
            payload.ref_text if payload.ref_text is not None else current["ref_text"],
            _json_dumps(payload.extra_args) if payload.extra_args is not None else current["extra_args"],
            now_ts(),
            preset_id,
        ),
    )
    return ApiMessage(message="ok")


@app.delete("/api/v1/live/presets/{preset_id}", response_model=ApiMessage)
def delete_preset(preset_id: str) -> ApiMessage:
    count = execute("DELETE FROM live_presets WHERE id = ?", (preset_id,))
    if count == 0:
        raise HTTPException(status_code=404, detail="Preset 不存在")
    return ApiMessage(message="ok")


# =========================
# Live session control
# =========================


def _load_start_options(req: LiveStartRequest) -> dict[str, object]:
    """加载启动参数。

    规则:
    - 如果传入 preset_id，则从预设读取参数；
    - 否则使用请求体直接参数。
    """

    if req.preset_id:
        preset = query_one("SELECT * FROM live_presets WHERE id = ?", (req.preset_id,))
        if not preset:
            raise HTTPException(status_code=404, detail="Preset 不存在")
        return {
            "preset_id": req.preset_id,
            "avatar_id": preset["avatar_id"],
            "model": preset["model"],
            "transport": preset["transport"],
            "listen_port": preset["listen_port"],
            "tts": preset.get("tts"),
            "tts_server": preset.get("tts_server"),
            "ref_file": preset.get("ref_file"),
            "ref_text": preset.get("ref_text"),
            "extra_args": _json_loads(preset.get("extra_args"), []),
        }

    if not req.avatar_id:
        raise HTTPException(status_code=400, detail="未提供 avatar_id")

    return {
        "preset_id": None,
        "avatar_id": req.avatar_id,
        "model": req.model,
        "transport": req.transport,
        "listen_port": req.listen_port,
        "tts": req.tts,
        "tts_server": req.tts_server,
        "ref_file": req.ref_file,
        "ref_text": req.ref_text,
        "extra_args": req.extra_args,
    }


def _build_command(opts: dict[str, object]) -> list[str]:
    """构建 `app.py` 启动命令。"""

    cmd = [
        settings.python_exec,
        str(settings.app_entry),
        "--transport",
        str(opts["transport"]),
        "--model",
        str(opts["model"]),
        "--avatar_id",
        str(opts["avatar_id"]),
        "--listenport",
        str(opts["listen_port"]),
    ]

    if opts.get("tts"):
        cmd.extend(["--tts", str(opts["tts"])])
    if opts.get("tts_server"):
        cmd.extend(["--TTS_SERVER", str(opts["tts_server"])])
    if opts.get("ref_file"):
        cmd.extend(["--REF_FILE", str(opts["ref_file"])])
    if opts.get("ref_text"):
        cmd.extend(["--REF_TEXT", str(opts["ref_text"])])

    extra_args = opts.get("extra_args") or []
    normalized_extra_args = [str(x) for x in extra_args if x is not None]
    i = 0
    while i < len(normalized_extra_args):
        token = str(normalized_extra_args[i]).strip()
        if not token:
            i += 1
            continue

        # TTS_RATE may be negative like "-25%"; use --key=value to prevent argparse
        # from treating it as another option.
        if token == "--TTS_RATE":
            if i + 1 >= len(normalized_extra_args):
                i += 1
                continue
            value = str(normalized_extra_args[i + 1]).strip()
            if value:
                cmd.append(f"--TTS_RATE={value}")
            i += 2
            continue

        if token == "--push_url":
            if i + 1 >= len(normalized_extra_args):
                i += 1
                continue
            value = str(normalized_extra_args[i + 1]).strip()
            if value:
                cmd.extend(["--push_url", value])
            i += 2
            continue

        cmd.append(token)
        i += 1

    return cmd


def _on_live_exit(session_id: str, return_code: int) -> None:
    """直播进程退出回调，更新会话状态。"""

    status = "stopped" if return_code == 0 else "failed"
    execute(
        """
        UPDATE live_sessions
        SET status = ?, ended_at = ?, error = ?
        WHERE id = ?
        """,
        (
            status,
            now_ts(),
            None if return_code == 0 else f"process exited with code {return_code}",
            session_id,
        ),
    )


@app.post("/api/v1/live/sessions:start")
def start_live(req: LiveStartRequest) -> dict[str, object]:
    if live_manager.is_running():
        raise HTTPException(status_code=409, detail="已有直播在运行，请先停止")

    opts = _load_start_options(req)

    avatar_id = str(opts["avatar_id"])
    avatar = query_one("SELECT * FROM avatars WHERE id = ?", (avatar_id,))
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar 不存在")

    command = _build_command(opts)

    run_info = live_manager.start(
        command=command,
        cwd=settings.project_root,
        on_exit=_on_live_exit,
    )

    execute(
        """
        INSERT INTO live_sessions (id, preset_id, status, pid, cmdline, log_path, started_at)
        VALUES (?, ?, 'running', ?, ?, ?, ?)
        """,
        (
            run_info.session_id,
            opts.get("preset_id"),
            run_info.pid,
            _json_dumps(run_info.cmdline),
            run_info.log_path,
            run_info.started_at,
        ),
    )

    return {
        "session_id": run_info.session_id,
        "pid": run_info.pid,
        "cmdline": run_info.cmdline,
        "log_path": run_info.log_path,
        "started_at": run_info.started_at,
    }


@app.post("/api/v1/live/sessions/{session_id}:stop")
def stop_live(session_id: str, req: LiveStopRequest) -> dict[str, object]:
    current = live_manager.current()
    if not current["running"]:
        raise HTTPException(status_code=404, detail="当前没有运行中的直播")

    if current["session_id"] != session_id:
        raise HTTPException(status_code=409, detail="session_id 与当前运行实例不一致")

    result = live_manager.stop(force=req.force)
    return result


@app.get("/api/v1/live/sessions/current")
def current_live() -> dict[str, object]:
    return live_manager.current()


@app.get("/api/v1/live/sessions/{session_id}/logs")
def live_logs(session_id: str, limit: int = Query(default=200, ge=1, le=5000)) -> dict[str, object]:
    current = live_manager.current()
    if current["session_id"] == session_id:
        return {
            "session_id": session_id,
            "source": "memory",
            "lines": live_manager.tail_logs(limit),
        }

    # 历史会话从日志文件读取。
    row = query_one("SELECT log_path FROM live_sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")

    log_path = row.get("log_path")
    if not log_path:
        return {"session_id": session_id, "source": "file", "lines": []}

    path = Path(str(log_path))
    if not path.exists():
        return {"session_id": session_id, "source": "file", "lines": []}

    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    return {
        "session_id": session_id,
        "source": "file",
        "lines": [line.rstrip("\n") for line in lines[-limit:]],
    }


# =========================
# 消息与智能回复
# =========================


@app.post("/api/v1/room/messages:ingest", response_model=ApiMessage)
def ingest_room_messages(payload: RoomMessagesIngestRequest) -> ApiMessage:
    """写入直播间消息（支持批量）。"""

    if not payload.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    for msg in payload.messages:
        msg_id = _uid("msg")
        execute(
            """
            INSERT INTO room_messages
            (id, platform, room_id, source_msg_id, user_id, user_name, content, msg_time, priority, source_payload_json, handled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                msg_id,
                msg.platform,
                msg.room_id,
                msg.source_msg_id,
                msg.user_id,
                msg.user_name,
                msg.content,
                msg.msg_time,
                msg.priority,
                None,
                now_ts(),
            ),
        )
    return ApiMessage(message=f"ok:{len(payload.messages)}")


@app.get("/api/v1/room/messages")
def list_room_messages(
    handled: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, object]]:
    """查询直播间消息。"""

    sql = "SELECT * FROM room_messages"
    params: list[object] = []
    if handled is not None:
        sql += " WHERE handled = ?"
        params.append(1 if handled else 0)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query_all(sql, tuple(params))


@app.post("/api/v1/platform/messages:ingest")
def ingest_platform_messages(payload: PlatformMessagesIngestRequest) -> dict[str, object]:
    """接入真实平台弹幕/评论消息（标准化入口）。"""

    if not payload.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    inserted = 0
    skipped = 0
    replies_created = 0
    replies_enqueued = 0

    for msg in payload.messages:
        exists = query_one(
            """
            SELECT id
            FROM room_messages
            WHERE platform = ? AND room_id = ? AND source_msg_id = ?
            LIMIT 1
            """,
            (msg.platform, msg.room_id, msg.source_msg_id),
        )
        if exists:
            skipped += 1
            continue

        message_id = _uid("msg")
        execute(
            """
            INSERT INTO room_messages
            (id, platform, room_id, source_msg_id, user_id, user_name, content, msg_time, priority, source_payload_json, handled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                message_id,
                msg.platform,
                msg.room_id,
                msg.source_msg_id,
                msg.user_id,
                msg.user_name,
                msg.content,
                msg.msg_time,
                msg.priority,
                _json_dumps(msg.model_dump()),
                now_ts(),
            ),
        )
        inserted += 1

        if not msg.auto_generate_reply:
            continue

        if msg.strategy != "rule":
            continue

        answer = _rule_generate_reply_text(msg.content, msg.fallback_text)
        reply_id = _uid("reply")
        current = live_manager.current()
        session_id = current.get("session_id") if current.get("running") else None
        reply_priority = max(0, min(100, int(msg.priority)))

        status = "ready"

        execute(
            """
            INSERT INTO replies (id, session_id, message_id, answer, source, priority, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (reply_id, session_id, message_id, answer, msg.strategy, reply_priority, status, now_ts()),
        )
        replies_created += 1

        if msg.auto_speak:
            auto_interrupt = bool(msg.interrupt) if msg.interrupt is not None else reply_priority >= 70
            task_id = speaker_dispatcher.enqueue_reply(
                reply_id=reply_id,
                text=answer,
                interrupt=auto_interrupt,
                priority=reply_priority,
            )
            is_running = bool(current.get("running"))
            next_status = "queued_to_runtime" if is_running else "pending_live"
            execute("UPDATE replies SET status = ? WHERE id = ?", (next_status, reply_id))
            if task_id:
                replies_enqueued += 1

    return {
        "ok": True,
        "inserted": inserted,
        "skipped_duplicate": skipped,
        "replies_created": replies_created,
        "replies_enqueued": replies_enqueued,
    }


@app.post("/api/v1/replies:generate")
def generate_reply(payload: ReplyGenerateRequest) -> dict[str, object]:
    """根据消息生成回复文案。"""

    message = query_one("SELECT * FROM room_messages WHERE id = ?", (payload.message_id,))
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")

    if payload.strategy != "rule":
        raise HTTPException(status_code=400, detail="当前仅支持 rule 生成策略")

    answer = _rule_generate_reply_text(str(message.get("content") or ""), payload.fallback_text)
    reply_id = _uid("reply")
    priority = max(0, min(100, int(message.get("priority") or 80)))

    current = live_manager.current()
    session_id = current.get("session_id") if current.get("running") else None

    execute(
        """
        INSERT INTO replies (id, session_id, message_id, answer, source, priority, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)
        """,
        (reply_id, session_id, payload.message_id, answer, payload.strategy, priority, now_ts()),
    )

    return {
        "reply_id": reply_id,
        "message_id": payload.message_id,
        "answer": answer,
        "priority": priority,
        "status": "ready",
    }


@app.post("/api/v1/replies/{reply_id}:speak")
def speak_reply(reply_id: str, payload: ReplySpeakRequest) -> dict[str, object]:
    """将回复下发到播报编排队列。"""

    reply = query_one("SELECT * FROM replies WHERE id = ?", (reply_id,))
    if not reply:
        raise HTTPException(status_code=404, detail="回复不存在")

    answer = str(reply.get("answer") or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="回复内容为空，无法播报")

    task_id = speaker_dispatcher.enqueue_reply(
        reply_id,
        answer,
        interrupt=payload.interrupt,
        priority=payload.priority,
    )

    current = live_manager.current()
    is_running = bool(current.get("running"))
    status = "queued_to_runtime" if is_running else "pending_live"

    execute("UPDATE replies SET status = ?, priority = ? WHERE id = ?", (status, payload.priority, reply_id))

    message_id = str(reply.get("message_id") or "")
    if payload.auto_mark_handled and message_id:
        execute("UPDATE room_messages SET handled = 1 WHERE id = ?", (message_id,))

    return {
        "reply_id": reply_id,
        "task_id": task_id,
        "status": status,
        "priority": payload.priority,
        "live_running": is_running,
        "note": "已进入播报调度器队列。",
    }


# =========================
# 统一播报调度
# =========================


@app.get("/api/v1/live/speaker/status")
def speaker_status() -> dict[str, object]:
    """查询播报调度器状态。"""

    return speaker_dispatcher.status()


@app.post("/api/v1/live/speaker/say")
def speaker_say(payload: ManualSpeakRequest) -> dict[str, object]:
    """手工追加一条播报任务。"""

    result = speaker_dispatcher.enqueue_manual_detail(
        payload.text,
        interrupt=payload.interrupt,
        priority=payload.priority,
    )
    task_id = str(result.get("task_id") or "")
    if not task_id:
        raise HTTPException(status_code=400, detail="文本为空或无法分段")

    return {
        "task_id": task_id,
        "status": "queued",
        "priority": payload.priority,
        "segment_count": int(result.get("segment_count") or 1),
        "char_count": int(result.get("char_count") or 0),
        "segment_max_chars": int(result.get("segment_max_chars") or 0),
        "text_trimmed": bool(result.get("text_trimmed")),
    }


# =========================
# 任务中心
# =========================


@app.post("/api/v1/jobs", response_model=ApiMessage)
def create_job(payload: JobCreate) -> ApiMessage:
    """创建通用任务。"""

    job_id = job_runner.create_job(payload.job_type, payload.payload)
    return ApiMessage(message=job_id)


@app.get("/api/v1/jobs")
def list_jobs() -> list[dict[str, object]]:
    """任务列表。"""

    return query_all("SELECT * FROM jobs ORDER BY created_at DESC")


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    row["payload"] = _json_loads(row.get("payload_json"), {})
    row["result"] = _json_loads(row.get("result_json"), {})
    row["logs"] = query_all(
        "SELECT level, message, created_at FROM job_logs WHERE job_id = ? ORDER BY id ASC",
        (job_id,),
    )
    row.pop("payload_json", None)
    row.pop("result_json", None)
    return row


@app.post("/api/v1/jobs/{job_id}:cancel", response_model=ApiMessage)
def cancel_job(job_id: str, _: JobCancelRequest) -> ApiMessage:
    ok = job_runner.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiMessage(message="ok")


@app.post("/api/v1/jobs/{job_id}:retry", response_model=ApiMessage)
def retry_job(job_id: str) -> ApiMessage:
    try:
        new_job_id = job_runner.retry_job(job_id)
    except RuntimeError as exc:
        detail = str(exc)
        if "不存在" in detail:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return ApiMessage(message=new_job_id)

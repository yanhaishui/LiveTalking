"""异步任务执行器。

当前实现的任务类型:
- avatar.clone.wav2lip: 调用 wav2lip/genavatar.py 生成头像素材并注册 Avatar 资产
- voice.clone.xtts: 调用 XTTS 服务 clone_speaker 接口并注册 Voice 资产

设计说明:
- 采用单工作线程串行执行，先保证稳定性与可调试性。
- 支持任务取消: 队列中任务可直接标记取消；运行中任务会尝试中断子进程。
- 支持失败恢复: 服务重启后会恢复 queued/running 任务。
- 支持自动重试: clone 类任务失败可按配置回退重试。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import queue
import shutil
import subprocess
import threading
import time
import uuid
import wave
from typing import Any

import requests

from .config import settings
from .database import execute, now_ts, query_all, query_one


@dataclass
class RuntimeState:
    """任务运行态。"""

    cancel_requested: bool = False
    running_process: subprocess.Popen[str] | None = None


class JobRunner:
    """任务执行器。"""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._runtime: dict[str, RuntimeState] = {}
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        """启动后台工作线程（幂等）。"""

        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work_loop, daemon=True, name="control-api-job-worker")
            self._worker.start()
        self._recover_incomplete_jobs()

    def create_job(self, job_type: str, payload: dict[str, Any], parent_job_id: str | None = None) -> str:
        """创建任务并进入队列。"""

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        ts = now_ts()
        retry_cfg = self._resolve_retry_config(job_type, payload)

        execute(
            """
            INSERT INTO jobs
            (id, job_type, payload_json, status, progress, retry_count, max_retries, retry_backoff_sec, parent_job_id, result_json, error, last_error_at, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', 0, 0, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                job_id,
                job_type,
                json.dumps(payload, ensure_ascii=False),
                retry_cfg["max_retries"],
                retry_cfg["retry_backoff_sec"],
                parent_job_id,
                ts,
                ts,
            ),
        )
        self.append_log(job_id, "INFO", f"任务已入队: {job_type}")
        self.append_log(
            job_id,
            "INFO",
            f"重试策略: max_retries={retry_cfg['max_retries']}, backoff={retry_cfg['retry_backoff_sec']}s",
        )

        with self._lock:
            self._runtime[job_id] = RuntimeState()

        self._q.put(job_id)
        return job_id

    def retry_job(self, source_job_id: str) -> str:
        """按历史任务配置创建重试任务。"""

        row = query_one("SELECT * FROM jobs WHERE id = ?", (source_job_id,))
        if not row:
            raise RuntimeError("任务不存在")

        status = str(row.get("status") or "")
        if status in {"queued", "running"}:
            raise RuntimeError("任务正在执行中，不能重复重试")

        payload = json.loads(str(row.get("payload_json") or "{}"))
        job_type = str(row.get("job_type") or "")
        if not job_type:
            raise RuntimeError("任务类型缺失，无法重试")

        new_job_id = self.create_job(job_type, payload, parent_job_id=source_job_id)
        self.append_log(source_job_id, "INFO", f"已创建重试任务: {new_job_id}")
        return new_job_id

    def _resolve_retry_config(self, job_type: str, payload: dict[str, Any]) -> dict[str, int]:
        """解析任务重试配置。"""

        default_max = 2 if job_type in {"avatar.clone.wav2lip", "voice.clone.xtts"} else 0
        default_backoff = 5

        max_retries = payload.get("max_retries")
        if max_retries is None:
            max_retries = default_max

        retry_backoff_sec = payload.get("retry_backoff_sec")
        if retry_backoff_sec is None:
            retry_backoff_sec = default_backoff

        try:
            max_retries_int = max(0, min(5, int(max_retries)))
        except Exception:
            max_retries_int = default_max

        try:
            retry_backoff_int = max(1, min(120, int(retry_backoff_sec)))
        except Exception:
            retry_backoff_int = default_backoff

        return {
            "max_retries": max_retries_int,
            "retry_backoff_sec": retry_backoff_int,
        }

    def _recover_incomplete_jobs(self) -> None:
        """服务重启后恢复未完成任务。

        规则:
        - `queued` 任务: 直接重新入队；
        - `running` 任务: 标记回 `queued` 后重新入队。
        """

        rows = query_all(
            """
            SELECT id, status
            FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC
            """
        )
        if not rows:
            return

        for row in rows:
            job_id = str(row["id"])
            status = str(row["status"])
            if status == "running":
                execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_ts(), job_id),
                )
                self.append_log(job_id, "WARN", "检测到服务重启，running 任务已恢复为 queued")

            with self._lock:
                self._runtime.setdefault(job_id, RuntimeState())
            self._q.put(job_id)
            self.append_log(job_id, "INFO", "任务恢复入队")

    def cancel_job(self, job_id: str) -> bool:
        """取消任务。

        返回:
        - True: 已接收取消请求
        - False: 任务不存在
        """

        row = query_one("SELECT id, status FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return False

        status = str(row.get("status"))
        if status in {"succeeded", "failed", "cancelled"}:
            return True

        with self._lock:
            state = self._runtime.get(job_id)
            if state is None:
                state = RuntimeState()
                self._runtime[job_id] = state
            state.cancel_requested = True

            # 运行中的子进程需要主动终止。
            if state.running_process and state.running_process.poll() is None:
                state.running_process.terminate()

        execute("UPDATE jobs SET status = CASE WHEN status='queued' THEN 'cancelled' ELSE status END, updated_at = ? WHERE id = ?", (now_ts(), job_id))
        self.append_log(job_id, "WARN", "收到取消请求")
        return True

    def append_log(self, job_id: str, level: str, message: str) -> None:
        """写入任务日志。"""

        execute(
            "INSERT INTO job_logs (job_id, level, message, created_at) VALUES (?, ?, ?, ?)",
            (job_id, level, message, now_ts()),
        )

    def _work_loop(self) -> None:
        while True:
            job_id = self._q.get()
            try:
                self._run_job(job_id)
            except Exception as exc:  # noqa: BLE001
                self._handle_job_failure(job_id, str(exc))

    def _run_job(self, job_id: str) -> None:
        row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return

        if row.get("status") == "cancelled":
            self.append_log(job_id, "WARN", "任务已取消，跳过执行")
            return

        payload = json.loads(str(row.get("payload_json") or "{}"))
        job_type = str(row.get("job_type"))

        execute(
            "UPDATE jobs SET status = 'running', progress = 1, error = NULL, updated_at = ? WHERE id = ?",
            (now_ts(), job_id),
        )
        self.append_log(job_id, "INFO", "任务开始执行")

        if job_type == "avatar.clone.wav2lip":
            result = self._handle_avatar_clone(job_id, payload)
        elif job_type == "voice.clone.xtts":
            result = self._handle_voice_clone(job_id, payload)
        else:
            raise RuntimeError(f"不支持的任务类型: {job_type}")

        # 执行后再次检查取消状态，避免竞态条件。
        if self._is_cancelled(job_id):
            execute("UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_ts(), job_id))
            self.append_log(job_id, "WARN", "任务已取消")
            return

        execute(
            "UPDATE jobs SET status = 'succeeded', progress = 100, result_json = ?, error = NULL, updated_at = ? WHERE id = ?",
            (json.dumps(result, ensure_ascii=False), now_ts(), job_id),
        )
        self.append_log(job_id, "INFO", "任务执行成功")

    def _handle_job_failure(self, job_id: str, error: str) -> None:
        """任务失败处理（自动重试 + 最终失败落库）。"""

        row = query_one(
            """
            SELECT id, status, retry_count, max_retries, retry_backoff_sec
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        )
        if not row:
            return

        if str(row.get("status")) == "cancelled":
            self.append_log(job_id, "WARN", "任务已取消，失败处理结束")
            return

        retry_count = int(row.get("retry_count") or 0)
        max_retries = int(row.get("max_retries") or 0)
        retry_backoff_sec = int(row.get("retry_backoff_sec") or 5)

        if retry_count < max_retries and not self._is_cancelled(job_id):
            next_retry = retry_count + 1
            execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    progress = 0,
                    retry_count = ?,
                    error = ?,
                    last_error_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (next_retry, error[:1000], now_ts(), now_ts(), job_id),
            )
            self.append_log(
                job_id,
                "WARN",
                f"任务失败，准备重试 {next_retry}/{max_retries}，backoff={retry_backoff_sec}s，error={error}",
            )
            time.sleep(retry_backoff_sec * next_retry)
            if not self._is_cancelled(job_id):
                self._q.put(job_id)
            return

        execute(
            """
            UPDATE jobs
            SET status = 'failed', error = ?, last_error_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (error[:1000], now_ts(), now_ts(), job_id),
        )
        self.append_log(job_id, "ERROR", f"任务失败: {error}")

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            state = self._runtime.get(job_id)
            return bool(state and state.cancel_requested)

    def _set_process(self, job_id: str, proc: subprocess.Popen[str] | None) -> None:
        with self._lock:
            state = self._runtime.setdefault(job_id, RuntimeState())
            state.running_process = proc

    def _run_subprocess(self, job_id: str, cmd: list[str], cwd: Path) -> None:
        """运行子进程并将输出写入 job_logs。"""

        self.append_log(job_id, "INFO", f"执行命令: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._set_process(job_id, proc)

        try:
            if proc.stdout:
                for line in proc.stdout:
                    text = line.rstrip("\n")
                    if text:
                        self.append_log(job_id, "INFO", text)

                    if self._is_cancelled(job_id) and proc.poll() is None:
                        proc.terminate()

            code = proc.wait()
            if code != 0:
                raise RuntimeError(f"命令执行失败，退出码: {code}")
        finally:
            self._set_process(job_id, None)

    def _handle_avatar_clone(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行 wav2lip 头像生成任务。"""

        name = str(payload.get("name") or "未命名Avatar")
        video_path = Path(str(payload.get("video_path") or "")).expanduser()
        if not video_path.exists():
            raise RuntimeError(f"视频文件不存在: {video_path}")

        avatar_id = str(payload.get("avatar_id") or f"avatar_{uuid.uuid4().hex[:8]}")
        img_size = int(payload.get("img_size") or 256)
        face_det_batch_size = int(payload.get("face_det_batch_size") or 16)
        pads = payload.get("pads") or [0, 10, 0, 0]
        overwrite = bool(payload.get("overwrite", False))

        dst_dir = settings.project_root / "data" / "avatars" / avatar_id
        if dst_dir.exists():
            if overwrite:
                shutil.rmtree(dst_dir)
            else:
                if bool(payload.get("allow_reuse_existing", True)):
                    self.append_log(job_id, "WARN", f"目标 avatar 已存在，复用已有目录: {avatar_id}")
                    self._upsert_avatar_asset(avatar_id=avatar_id, name=name, avatar_path=dst_dir)
                    return {
                        "avatar_id": avatar_id,
                        "avatar_path": str(dst_dir),
                        "name": name,
                        "reused": True,
                    }
                raise RuntimeError(f"目标 avatar 已存在: {avatar_id}")

        cmd = [
            settings.python_exec,
            str(settings.project_root / "wav2lip" / "genavatar.py"),
            "--video_path",
            str(video_path),
            "--img_size",
            str(img_size),
            "--avatar_id",
            avatar_id,
            "--face_det_batch_size",
            str(face_det_batch_size),
            "--pads",
            *[str(x) for x in pads],
        ]

        self._run_subprocess(job_id, cmd, cwd=settings.project_root)

        src_dir = settings.project_root / "wav2lip" / "results" / "avatars" / avatar_id
        if not src_dir.exists():
            raise RuntimeError(f"未找到生成结果目录: {src_dir}")

        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst_dir)

        self._upsert_avatar_asset(avatar_id=avatar_id, name=name, avatar_path=dst_dir)

        return {
            "avatar_id": avatar_id,
            "avatar_path": str(dst_dir),
            "name": name,
        }

    def _upsert_avatar_asset(self, avatar_id: str, name: str, avatar_path: Path) -> None:
        """将 Avatar 产物写入资产表。"""

        ts = now_ts()
        existing = query_one("SELECT id FROM avatars WHERE id = ?", (avatar_id,))
        if existing:
            execute(
                """
                UPDATE avatars
                SET name = ?, avatar_path = ?, status = 'ready', updated_at = ?
                WHERE id = ?
                """,
                (name, str(avatar_path), ts, avatar_id),
            )
            return

        execute(
            """
            INSERT INTO avatars (id, name, avatar_path, cover_image, status, tags, meta_json, created_at, updated_at)
            VALUES (?, ?, ?, NULL, 'ready', '[]', '{}', ?, ?)
            """,
            (avatar_id, name, str(avatar_path), ts, ts),
        )

    def _handle_voice_clone(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行 XTTS 声音克隆任务。"""

        name = str(payload.get("name") or "未命名Voice")
        engine = str(payload.get("engine") or "xtts")
        if engine != "xtts":
            raise RuntimeError("当前仅支持 xtts 声音克隆任务")

        ref_wav_path = Path(str(payload.get("ref_wav_path") or "")).expanduser()
        if not ref_wav_path.exists():
            raise RuntimeError(f"参考音频不存在: {ref_wav_path}")

        tts_server = str(payload.get("tts_server") or "http://127.0.0.1:9000").rstrip("/")
        voice_id = str(payload.get("voice_id") or f"voice_{uuid.uuid4().hex[:8]}")

        self.append_log(job_id, "INFO", f"调用 XTTS 克隆接口: {tts_server}/clone_speaker")
        with ref_wav_path.open("rb") as f:
            resp = requests.post(
                f"{tts_server}/clone_speaker",
                files={"wav_file": (ref_wav_path.name, f, "audio/wav")},
                timeout=180,
            )

        if resp.status_code >= 400:
            raise RuntimeError(f"XTTS 克隆失败: HTTP {resp.status_code}, {resp.text[:500]}")

        try:
            speaker_profile = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"XTTS 返回非 JSON 数据: {exc}") from exc

        xtts_params = {
            "temperature": payload.get("temperature"),
            "speed": payload.get("speed"),
            "top_k": payload.get("top_k"),
            "top_p": payload.get("top_p"),
            "repetition_penalty": payload.get("repetition_penalty"),
        }
        # 去掉空值，避免把无效参数传给 XTTS。
        xtts_params = {k: v for k, v in xtts_params.items() if v is not None}

        preview_wav_path: str | None = None
        if bool(payload.get("generate_preview", True)):
            preview_wav_path = self._generate_xtts_preview(
                job_id=job_id,
                voice_id=voice_id,
                tts_server=tts_server,
                speaker_profile=speaker_profile,
                text=str(payload.get("preview_text") or "你好，欢迎来到直播间。"),
                language=str(payload.get("preview_language") or "zh-cn"),
                stream_chunk_size=int(payload.get("preview_stream_chunk_size") or 20),
                xtts_params=xtts_params,
            )

        profile_data = {
            "speaker": speaker_profile,
            "params": xtts_params,
        }
        if preview_wav_path:
            profile_data["preview_wav_path"] = preview_wav_path

        ts = now_ts()
        existing = query_one("SELECT id FROM voices WHERE id = ?", (voice_id,))
        if existing:
            execute(
                """
                UPDATE voices
                SET name = ?, engine = ?, ref_wav_path = ?, profile_json = ?, preview_wav_path = ?, status = 'ready', updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    engine,
                    str(ref_wav_path),
                    json.dumps(profile_data, ensure_ascii=False),
                    preview_wav_path,
                    ts,
                    voice_id,
                ),
            )
        else:
            execute(
                """
                INSERT INTO voices (id, name, engine, ref_wav_path, profile_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    voice_id,
                    name,
                    engine,
                    str(ref_wav_path),
                    json.dumps(profile_data, ensure_ascii=False),
                    ts,
                    ts,
                ),
            )
            if preview_wav_path:
                execute(
                    "UPDATE voices SET preview_wav_path = ? WHERE id = ?",
                    (preview_wav_path, voice_id),
                )

        return {
            "voice_id": voice_id,
            "engine": engine,
            "name": name,
            "ref_wav_path": str(ref_wav_path),
            "preview_wav_path": preview_wav_path,
            "xtts_params": xtts_params,
        }

    def _generate_xtts_preview(
        self,
        job_id: str | None,
        voice_id: str,
        tts_server: str,
        speaker_profile: dict[str, Any],
        text: str,
        language: str,
        stream_chunk_size: int,
        xtts_params: dict[str, Any],
    ) -> str:
        """调用 XTTS 生成试听音频并保存为 wav。"""

        request_payload = dict(speaker_profile)
        request_payload["text"] = text
        request_payload["language"] = language
        request_payload["stream_chunk_size"] = str(stream_chunk_size)
        request_payload.update(xtts_params)

        if job_id:
            self.append_log(job_id, "INFO", f"调用 XTTS 试听接口: {tts_server}/tts_stream")
        resp = requests.post(
            f"{tts_server}/tts_stream",
            json=request_payload,
            stream=True,
            timeout=(8, 180),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"XTTS 试听失败: HTTP {resp.status_code}, {resp.text[:500]}")

        pcm_bytes = b"".join(chunk for chunk in resp.iter_content(chunk_size=None) if chunk)
        if not pcm_bytes:
            raise RuntimeError("XTTS 试听失败: 返回空音频流")

        preview_dir = settings.project_root / "data" / "voices" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / f"{voice_id}_{job_id}.wav"

        # XTTS /tts_stream 返回 24kHz / 16bit / mono PCM，封装为标准 WAV 便于播放。
        with wave.open(str(preview_path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(24000)
            f.writeframes(pcm_bytes)

        if job_id:
            self.append_log(job_id, "INFO", f"试听音频已生成: {preview_path}")
        return str(preview_path)

    def preview_voice(
        self,
        voice_id: str,
        tts_server: str,
        text: str,
        language: str,
        stream_chunk_size: int,
        xtts_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为已存在声音资产生成试听音频。"""

        voice = query_one("SELECT * FROM voices WHERE id = ?", (voice_id,))
        if not voice:
            raise RuntimeError("Voice 不存在")

        profile_raw = voice.get("profile_json")
        try:
            profile_data = json.loads(str(profile_raw or "{}"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Voice profile_json 非法: {exc}") from exc

        speaker_profile = profile_data.get("speaker")
        if not isinstance(speaker_profile, dict) or not speaker_profile:
            if isinstance(profile_data, dict) and profile_data:
                speaker_profile = profile_data
            else:
                raise RuntimeError("Voice 缺少可用 speaker profile，请先重新克隆")

        xtts_params = dict(xtts_params or {})
        xtts_params = {k: v for k, v in xtts_params.items() if v is not None}

        preview_path = self._generate_xtts_preview(
            job_id=None,
            voice_id=voice_id,
            tts_server=tts_server.rstrip("/"),
            speaker_profile=speaker_profile,
            text=text,
            language=language,
            stream_chunk_size=stream_chunk_size,
            xtts_params=xtts_params,
        )

        profile_data["params"] = {
            **(profile_data.get("params") or {}),
            **xtts_params,
        }
        profile_data["preview_wav_path"] = preview_path

        execute(
            """
            UPDATE voices
            SET profile_json = ?, preview_wav_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(profile_data, ensure_ascii=False), preview_path, now_ts(), voice_id),
        )
        return {
            "voice_id": voice_id,
            "preview_wav_path": preview_path,
            "tts_server": tts_server.rstrip("/"),
            "text": text,
            "language": language,
            "stream_chunk_size": stream_chunk_size,
            "xtts_params": xtts_params,
        }


job_runner = JobRunner()

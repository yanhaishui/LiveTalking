"""播报调度器。

职责:
- 统一处理“脚本轮播”和“智能回复插播”两类播报任务；
- 通过 `app.py` 暴露的 `/human` 与 `/is_speaking` 接口驱动实时播报；
- 与 `replies` 表状态联动，形成可追踪的消息闭环。

设计边界:
- 第一版默认面向 `virtualcam`（sessionid=0）链路；
- `webrtc` 由于 sessionid 动态生成，当前仅给出状态提示，不自动播报。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import random
import threading
import time
from typing import Any
import uuid

import requests

from .config import settings
from .database import execute, now_ts, query_all
from .live_runtime import live_manager


@dataclass
class SpeakTask:
    """播报任务。"""

    task_id: str
    source: str
    text: str
    priority: int = 50
    interrupt: bool = False
    sequence: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_ts)


class SpeakerDispatcher:
    """播报调度器。

    调度优先级:
    1. 智能回复任务（`replies:speak` 入队）
    2. 轮播脚本任务（仅在空闲且到达间隔时触发）
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queue: list[SpeakTask] = []
        self._sequence = 0
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._segment_max_chars = self._read_env_int(
            "LT_SPEAKER_SEGMENT_MAX_CHARS",
            default=70,
            min_value=40,
            max_value=2000,
        )
        self._max_text_chars = self._read_env_int(
            "LT_SPEAKER_MAX_TEXT_CHARS",
            default=200000,
            min_value=1000,
            max_value=2000000,
        )
        self._dispatch_guard_sec = self._read_env_float(
            "LT_SPEAKER_DISPATCH_GUARD_SEC",
            default=1.2,
            min_value=0.0,
            max_value=20.0,
        )
        self._dispatch_guard_per_char = self._read_env_float(
            "LT_SPEAKER_DISPATCH_GUARD_PER_CHAR",
            default=0.05,
            min_value=0.0,
            max_value=1.0,
        )
        self._dispatch_guard_musetalk_sec = self._read_env_float(
            "LT_SPEAKER_MUSETALK_GUARD_SEC",
            default=4.0,
            min_value=0.0,
            max_value=60.0,
        )
        self._dispatch_guard_musetalk_per_char = self._read_env_float(
            "LT_SPEAKER_MUSETALK_GUARD_PER_CHAR",
            default=0.16,
            min_value=0.0,
            max_value=2.0,
        )
        self._prefetch_while_speaking_mode = (
            os.getenv("LT_SPEAKER_PREFETCH_WHILE_SPEAKING", "auto").strip().lower()
        )
        if self._prefetch_while_speaking_mode not in {
            "auto",
            "on",
            "off",
            "1",
            "0",
            "true",
            "false",
            "yes",
            "no",
        }:
            self._prefetch_while_speaking_mode = "auto"
        self._next_dispatch_ts = 0.0

        # 轮播调度状态。
        self._playlist_cursor: dict[str, int] = {}
        self._playlist_last_emit_ts: dict[str, float] = {}
        self._playlist_schedule_cursor: int = 0

        # 运行状态快照，便于 API 查询诊断。
        self._started = False
        self._last_event: str | None = None
        self._last_error: str | None = None
        self._last_dispatch_at: str | None = None

    def start(self) -> None:
        """启动调度线程（幂等）。"""

        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._loop,
                daemon=True,
                name="control-api-speaker-dispatcher",
            )
            self._worker.start()
            self._started = True

        self._restore_pending_replies()

    def stop(self) -> None:
        """停止调度线程。"""

        self._stop_event.set()

    def status(self) -> dict[str, Any]:
        """返回调度器状态。"""

        live = live_manager.current()
        endpoint = self._resolve_live_endpoint(live)

        with self._lock:
            queue_size = len(self._queue)
            worker_alive = bool(self._worker and self._worker.is_alive())
            pending_reply_count = sum(1 for t in self._queue if t.source == "reply")
            pending_script_count = sum(1 for t in self._queue if t.source == "playlist")

        return {
            "started": self._started,
            "worker_alive": worker_alive,
            "queue_size": queue_size,
            "pending_reply_count": pending_reply_count,
            "pending_script_count": pending_script_count,
            "last_event": self._last_event,
            "last_error": self._last_error,
            "last_dispatch_at": self._last_dispatch_at,
            "segment_config": {
                "segment_max_chars": self._segment_max_chars,
                "max_text_chars": self._max_text_chars,
                "dispatch_guard_sec": self._dispatch_guard_sec,
                "dispatch_guard_per_char": self._dispatch_guard_per_char,
                "dispatch_guard_musetalk_sec": self._dispatch_guard_musetalk_sec,
                "dispatch_guard_musetalk_per_char": self._dispatch_guard_musetalk_per_char,
                "prefetch_while_speaking": self._prefetch_while_speaking_mode,
            },
            "live": {
                "running": live.get("running"),
                "session_id": live.get("session_id"),
                "transport": endpoint.get("transport"),
                "listen_port": endpoint.get("listen_port"),
                "model": endpoint.get("model"),
                "tts": endpoint.get("tts"),
                "tts_rate": endpoint.get("tts_rate"),
                "supported": endpoint.get("supported"),
                "reason": endpoint.get("reason"),
            },
        }

    def enqueue_reply(self, reply_id: str, text: str, interrupt: bool = True, priority: int = 80) -> str:
        """加入一条回复播报任务。"""

        result = self._enqueue_text_task(
            task_prefix="speak",
            source="reply",
            text=text,
            priority=priority,
            interrupt=interrupt,
            meta={"reply_id": reply_id},
        )
        return str(result.get("task_id") or "")

    def enqueue_manual(self, text: str, interrupt: bool = False, priority: int = 60) -> str:
        """加入一条手工播报任务（用于联调/运维）。"""

        detail = self.enqueue_manual_detail(text=text, interrupt=interrupt, priority=priority)
        return str(detail.get("task_id") or "")

    def enqueue_manual_detail(self, text: str, interrupt: bool = False, priority: int = 60) -> dict[str, Any]:
        """加入一条手工播报任务，并返回分段信息。"""

        return self._enqueue_text_task(
            task_prefix="manual",
            source="manual",
            text=text,
            priority=priority,
            interrupt=interrupt,
            meta={},
        )

    def _read_env_int(self, key: str, default: int, min_value: int, max_value: int) -> int:
        raw = os.getenv(key, str(default)).strip()
        try:
            value = int(raw)
        except ValueError:
            value = default
        return max(min_value, min(max_value, value))

    def _read_env_float(self, key: str, default: float, min_value: float, max_value: float) -> float:
        raw = os.getenv(key, str(default)).strip()
        try:
            value = float(raw)
        except ValueError:
            value = float(default)
        return max(min_value, min(max_value, value))

    def _edge_rate_factor(self, rate_text: str | None) -> float:
        rate = str(rate_text or "").strip()
        if not rate.endswith("%"):
            return 1.0
        try:
            num = float(rate[:-1].strip())
        except ValueError:
            return 1.0
        factor = 1.0 + (num / 100.0)
        return max(0.35, min(2.0, factor))

    def _compute_dispatch_guard(self, task: SpeakTask, endpoint: dict[str, Any]) -> float:
        model_name = str(endpoint.get("model") or "").lower()
        tts_name = str(endpoint.get("tts") or "").lower()
        text_len = len(str(task.text or "").strip())
        if text_len <= 0:
            return 0.0

        if model_name == "musetalk":
            guard = self._dispatch_guard_musetalk_sec + text_len * self._dispatch_guard_musetalk_per_char
        else:
            guard = self._dispatch_guard_sec + text_len * self._dispatch_guard_per_char

        if tts_name == "edgetts":
            guard = guard / self._edge_rate_factor(str(endpoint.get("tts_rate") or ""))
        return max(0.0, min(90.0, guard))

    def _split_text_for_speech(self, text: str, max_chars: int) -> list[str]:
        if max_chars <= 0 or len(text) <= max_chars:
            return [text] if text else []

        punct = set("。！？!?；;，,\n")
        chunks: list[str] = []
        buf: list[str] = []
        last_break_idx = -1
        soft_min_chars = max(20, max_chars // 3)

        for ch in text:
            buf.append(ch)
            if ch in punct:
                last_break_idx = len(buf)

            if len(buf) < max_chars:
                continue

            split_at = last_break_idx if last_break_idx >= soft_min_chars else max_chars
            chunk = "".join(buf[:split_at]).strip()
            if chunk:
                chunks.append(chunk)
            buf = buf[split_at:]
            last_break_idx = -1
            for idx, rem_ch in enumerate(buf, start=1):
                if rem_ch in punct:
                    last_break_idx = idx

        tail = "".join(buf).strip()
        if tail:
            chunks.append(tail)

        return chunks

    def _build_segmented_task(
        self,
        *,
        task_prefix: str,
        source: str,
        text: str,
        priority: int,
        interrupt: bool,
        meta: dict[str, Any] | None = None,
    ) -> tuple[SpeakTask | None, int, int]:
        merged_meta = dict(meta or {})
        normalized = str(text or "").strip()
        if not normalized:
            return None, 0, 0

        source_chars = len(normalized)
        if source_chars > self._max_text_chars:
            merged_meta["source_trimmed"] = True
            normalized = normalized[: self._max_text_chars].rstrip()
            source_chars = len(normalized)

        segments = self._split_text_for_speech(normalized, self._segment_max_chars)
        if not segments:
            return None, 0, source_chars

        segment_total = len(segments)
        merged_meta.update(
            {
                "source_chars": source_chars,
                "segment_total": segment_total,
                "segment_index": 1,
                "segment_group_id": f"{source}_{uuid.uuid4().hex[:10]}",
            }
        )
        if segment_total > 1:
            merged_meta["pending_segments"] = segments[1:]

        task = SpeakTask(
            task_id=f"{task_prefix}_{uuid.uuid4().hex[:12]}",
            source=source,
            text=segments[0],
            priority=max(0, min(100, int(priority))),
            interrupt=interrupt,
            meta=merged_meta,
        )
        return task, segment_total, source_chars

    def _enqueue_text_task(
        self,
        *,
        task_prefix: str,
        source: str,
        text: str,
        priority: int,
        interrupt: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task, segment_total, source_chars = self._build_segmented_task(
            task_prefix=task_prefix,
            source=source,
            text=text,
            priority=priority,
            interrupt=interrupt,
            meta=meta,
        )
        if not task:
            return {"task_id": "", "segment_count": 0, "char_count": 0}

        self._append_task(task)
        return {
            "task_id": task.task_id,
            "segment_count": segment_total,
            "char_count": source_chars,
            "segment_max_chars": self._segment_max_chars,
            "text_trimmed": bool(task.meta.get("source_trimmed")),
        }

    def _task_prefix_for_source(self, source: str) -> str:
        mapping = {"reply": "speak", "manual": "manual", "playlist": "playlist"}
        return mapping.get(source, "task")

    def _schedule_followup_segment(self, task: SpeakTask) -> None:
        pending_raw = task.meta.get("pending_segments")
        if not isinstance(pending_raw, list) or not pending_raw:
            return

        next_text = ""
        while pending_raw:
            next_text = str(pending_raw.pop(0) or "").strip()
            if next_text:
                break
        if not next_text:
            task.meta["pending_segments"] = pending_raw
            return

        next_meta = dict(task.meta)
        next_meta["pending_segments"] = pending_raw
        next_meta["segment_index"] = int(next_meta.get("segment_index") or 1) + 1
        next_meta.pop("retries", None)

        next_task = SpeakTask(
            task_id=f"{self._task_prefix_for_source(task.source)}_{uuid.uuid4().hex[:12]}",
            source=task.source,
            text=next_text,
            priority=task.priority,
            interrupt=False,
            meta=next_meta,
        )
        self._append_task(next_task)

    def _append_task(self, task: SpeakTask) -> None:
        # 控制队列上限，避免上游异常导致内存膨胀。
        with self._lock:
            task.sequence = self._sequence
            self._sequence += 1

            if len(self._queue) >= 500:
                # 优先丢弃“最低优先级且最早进入”的任务，保证高优先级任务不被淹没。
                drop_idx = min(
                    range(len(self._queue)),
                    key=lambda i: (self._queue[i].priority, self._queue[i].sequence),
                )
                dropped = self._queue.pop(drop_idx)
                self._last_event = f"队列满，丢弃任务: {dropped.task_id} (p{dropped.priority})"

            insert_idx = len(self._queue)
            for idx, queued in enumerate(self._queue):
                if task.priority > queued.priority:
                    insert_idx = idx
                    break
            self._queue.insert(insert_idx, task)

    def _restore_pending_replies(self) -> None:
        """恢复中断前未完成的回复任务。"""

        rows = query_all(
            """
            SELECT id, answer, priority
            FROM replies
            WHERE status IN ('pending_live', 'queued_to_runtime')
            ORDER BY created_at ASC
            LIMIT 500
            """
        )
        for row in rows:
            self.enqueue_reply(
                str(row["id"]),
                str(row["answer"]),
                interrupt=True,
                priority=int(row.get("priority") or 80),
            )

    def _loop(self) -> None:
        """后台调度循环。"""

        while not self._stop_event.is_set():
            try:
                live = live_manager.current()
                if not live.get("running"):
                    # 未开播时只保留队列，不执行下发。
                    time.sleep(0.6)
                    continue

                endpoint = self._resolve_live_endpoint(live)
                if not endpoint["supported"]:
                    self._last_error = str(endpoint["reason"])
                    time.sleep(1.0)
                    continue

                task = self._pick_next_task(endpoint)
                if not task:
                    time.sleep(0.25)
                    continue

                try:
                    self._dispatch_task(task, endpoint)
                except Exception as dispatch_exc:  # noqa: BLE001
                    self._handle_dispatch_error(task, str(dispatch_exc))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                time.sleep(0.6)

    def _resolve_live_endpoint(self, live: dict[str, Any]) -> dict[str, Any]:
        """解析当前直播实例的控制端点。"""

        cmdline = live.get("cmdline") or []
        transport = settings.default_transport
        listen_port = settings.default_listen_port
        model_name = "wav2lip"
        tts_name = ""
        tts_rate = ""

        for i, token in enumerate(cmdline):
            token_str = str(token)
            if token_str == "--transport" and i + 1 < len(cmdline):
                transport = str(cmdline[i + 1])
            elif token_str == "--listenport" and i + 1 < len(cmdline):
                try:
                    listen_port = int(cmdline[i + 1])
                except Exception:
                    listen_port = settings.default_listen_port
            elif token_str == "--model" and i + 1 < len(cmdline):
                model_name = str(cmdline[i + 1])
            elif token_str == "--tts" and i + 1 < len(cmdline):
                tts_name = str(cmdline[i + 1])
            elif token_str.startswith("--TTS_RATE="):
                tts_rate = token_str.split("=", 1)[1].strip()
            elif token_str == "--TTS_RATE" and i + 1 < len(cmdline):
                tts_rate = str(cmdline[i + 1])

        if transport == "webrtc":
            # webrtc sessionid 由 /offer 动态生成，当前控制层没有会话映射，先不自动注入。
            return {
                "supported": False,
                "reason": "webrtc 模式 sessionid 动态，当前版本暂不支持自动播报，请使用 virtualcam。",
                "transport": transport,
                "listen_port": listen_port,
                "model": model_name,
                "tts": tts_name,
                "tts_rate": tts_rate,
            }

        return {
            "supported": True,
            "reason": None,
            "transport": transport,
            "listen_port": listen_port,
            "base_url": f"http://127.0.0.1:{listen_port}",
            "session_id": 0,
            "model": model_name,
            "tts": tts_name,
            "tts_rate": tts_rate,
        }

    def _pick_next_task(self, endpoint: dict[str, Any]) -> SpeakTask | None:
        """按优先级挑选下一条可执行任务。"""

        speaking = self._is_speaking(endpoint)
        now = time.time()

        # 先从队列拿回复/手工任务：如果正在说话，仅允许 interrupt 任务抢占。
        with self._lock:
            if self._queue:
                if speaking:
                    for idx, task in enumerate(self._queue):
                        if task.interrupt:
                            selected = task
                            del self._queue[idx]
                            return selected
                    # For segmented long-form speech, preload follow-up chunks while speaking
                    # so the next chunk can start without an obvious silence gap.
                    if self._allow_prefetch_while_speaking(endpoint) and now >= self._next_dispatch_ts:
                        for idx, task in enumerate(self._queue):
                            if task.interrupt:
                                continue
                            if self._is_followup_segment(task):
                                selected = task
                                del self._queue[idx]
                                return selected
                else:
                    if now < self._next_dispatch_ts:
                        for idx, task in enumerate(self._queue):
                            if task.interrupt:
                                selected = task
                                del self._queue[idx]
                                return selected
                        return None
                    return self._queue.pop(0)

        # 队列为空时才尝试轮播，且仅在空闲时播报。
        if speaking:
            return None
        return self._build_playlist_task()

    def _allow_prefetch_while_speaking(self, endpoint: dict[str, Any]) -> bool:
        mode = self._prefetch_while_speaking_mode
        if mode in {"1", "true", "yes", "on"}:
            return True
        if mode in {"0", "false", "no", "off"}:
            return False
        # Auto mode: enable for wav2lip/ultralight, disable for musetalk.
        model_name = str(endpoint.get("model") or "").strip().lower()
        return model_name != "musetalk"

    def _is_followup_segment(self, task: SpeakTask) -> bool:
        try:
            return int(task.meta.get("segment_index") or 1) > 1
        except Exception:
            return False

    def _build_playlist_task(self) -> SpeakTask | None:
        """从启用轮播计划中生成一条脚本播报任务。"""

        playlists = query_all(
            """
            SELECT *
            FROM playlists
            WHERE enabled = 1
            ORDER BY created_at ASC
            """
        )
        if not playlists:
            return None

        now = time.time()
        total = len(playlists)
        start_index = self._playlist_schedule_cursor % total

        # 多计划并发策略: 在“到期可播”的计划中做轮询，避免只命中第一条计划。
        playlist: dict[str, Any] | None = None
        for offset in range(total):
            candidate = playlists[(start_index + offset) % total]
            candidate_id = str(candidate["id"])
            interval_sec = max(1, int(candidate.get("interval_sec") or 30))
            last = self._playlist_last_emit_ts.get(candidate_id, 0.0)
            if now - last >= interval_sec:
                playlist = candidate
                self._playlist_schedule_cursor = (start_index + offset + 1) % total
                break

        if not playlist:
            return None

        playlist_id = str(playlist["id"])
        now = time.time()

        items = query_all(
            """
            SELECT
                pi.id AS item_id,
                pi.script_id,
                pi.sort_order,
                pi.weight,
                s.title AS script_title,
                s.content AS script_content
            FROM playlist_items pi
            JOIN scripts s ON s.id = pi.script_id
            WHERE pi.playlist_id = ? AND s.enabled = 1
            ORDER BY pi.sort_order ASC, pi.created_at ASC
            """,
            (playlist_id,),
        )
        if not items:
            return None

        mode = str(playlist.get("mode") or "sequential")
        selected = self._choose_playlist_item(playlist_id, mode, items)
        text = str(selected.get("script_content") or "").strip()
        if not text:
            return None

        task, _, _ = self._build_segmented_task(
            task_prefix="playlist",
            source="playlist",
            text=text,
            priority=10,
            interrupt=False,
            meta={
                "playlist_id": playlist_id,
                "script_id": selected.get("script_id"),
                "script_title": selected.get("script_title"),
                "mode": mode,
            },
        )
        if not task:
            return None

        self._playlist_last_emit_ts[playlist_id] = now
        return task

    def _choose_playlist_item(
        self,
        playlist_id: str,
        mode: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if mode == "random":
            weights = [max(1, int(item.get("weight") or 1)) for item in items]
            return random.choices(items, weights=weights, k=1)[0]

        cursor = self._playlist_cursor.get(playlist_id, 0)
        index = cursor % len(items)
        self._playlist_cursor[playlist_id] = cursor + 1
        return items[index]

    def _dispatch_task(self, task: SpeakTask, endpoint: dict[str, Any]) -> None:
        """执行单条播报任务。"""

        self._send_text(endpoint, task.text, task.interrupt)
        self._last_dispatch_at = now_ts()
        self._last_error = None
        segment_index = int(task.meta.get("segment_index") or 1)
        segment_total = int(task.meta.get("segment_total") or 1)
        self._last_event = (
            f"已下发任务 {task.task_id} ({task.source}, p{task.priority}, part {segment_index}/{segment_total})"
        )
        self._schedule_followup_segment(task)
        if not task.interrupt:
            guard = self._compute_dispatch_guard(task, endpoint)
            with self._lock:
                self._next_dispatch_ts = max(self._next_dispatch_ts, time.time() + guard)

        if task.source == "reply":
            reply_id = str(task.meta.get("reply_id") or "")
            if reply_id and segment_index >= segment_total:
                execute(
                    "UPDATE replies SET status = 'sent_to_live' WHERE id = ?",
                    (reply_id,),
                )
                execute(
                    """
                    INSERT INTO audit_events (actor, action, target_type, target_id, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "system",
                        "reply_dispatch",
                        "reply",
                        reply_id,
                        '{"channel":"human","status":"sent_to_live"}',
                        now_ts(),
                    ),
                )

    def _handle_dispatch_error(self, task: SpeakTask, error: str) -> None:
        """处理任务下发失败（重试 + 落库）。"""

        self._last_error = error
        retries = int(task.meta.get("retries") or 0)
        max_retries = 3
        if retries < max_retries:
            task.meta["retries"] = retries + 1
            # 失败重试放回队列头，尽快恢复。
            with self._lock:
                self._queue.insert(0, task)
            self._last_event = f"任务下发失败，准备重试({retries + 1}/{max_retries}): {task.task_id}"
            time.sleep(0.8)
            return

        self._last_event = f"任务下发失败且超过重试次数: {task.task_id}"
        if task.source == "reply":
            reply_id = str(task.meta.get("reply_id") or "")
            if reply_id:
                execute(
                    "UPDATE replies SET status = 'dispatch_failed' WHERE id = ?",
                    (reply_id,),
                )
                execute(
                    """
                    INSERT INTO audit_events (actor, action, target_type, target_id, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "system",
                        "reply_dispatch_failed",
                        "reply",
                        reply_id,
                        json.dumps({"error": error[:300]}, ensure_ascii=False),
                        now_ts(),
                    ),
                )

    def _is_speaking(self, endpoint: dict[str, Any]) -> bool:
        """查询直播实例是否正在说话。"""

        url = f"{endpoint['base_url']}/is_speaking"
        resp = requests.post(
            url,
            json={"sessionid": endpoint["session_id"]},
            timeout=(3, 8),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"/is_speaking 调用失败: HTTP {resp.status_code}")
        data = resp.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"/is_speaking 返回异常: {data}")
        return bool(data.get("data"))

    def _send_text(self, endpoint: dict[str, Any], text: str, interrupt: bool) -> None:
        """下发文本到 `app.py /human`。"""

        payload: dict[str, Any] = {
            "sessionid": endpoint["session_id"],
            "type": "echo",
            "text": text,
        }
        if interrupt:
            payload["interrupt"] = True

        resp = requests.post(
            f"{endpoint['base_url']}/human",
            json=payload,
            timeout=(3, 10),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"/human 调用失败: HTTP {resp.status_code}")

        data = resp.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"/human 返回异常: {data}")


speaker_dispatcher = SpeakerDispatcher()

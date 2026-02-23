"""直播运行时进程管理。

该模块用于托管 `app.py` 子进程，提供启动、停止、状态查询和日志尾读能力。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import threading
import uuid
from typing import Callable

from .config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LiveRunInfo:
    """直播运行信息。"""

    session_id: str
    pid: int
    cmdline: list[str]
    log_path: str
    started_at: str


class LiveProcessManager:
    """直播进程管理器。

    设计要点:
    - 目前只允许单实例运行，避免重复占用虚拟摄像头设备。
    - 标准输出与错误输出合并写入日志文件，并保留内存环形缓冲便于前端查询。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._session_id: str | None = None
        self._pid: int | None = None
        self._cmdline: list[str] = []
        self._started_at: str | None = None
        self._log_path: Path | None = None
        self._log_buffer: deque[str] = deque(maxlen=settings.live_log_buffer_size)

    def is_running(self) -> bool:
        """判断当前是否有运行中的直播进程。"""

        with self._lock:
            return self._process is not None and self._process.poll() is None

    def current(self) -> dict[str, object]:
        """返回当前进程状态。"""

        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "running": running,
                "session_id": self._session_id,
                "pid": self._pid,
                "cmdline": self._cmdline,
                "started_at": self._started_at,
                "log_path": str(self._log_path) if self._log_path else None,
                "return_code": None if not self._process else self._process.poll(),
            }

    def start(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        on_exit: Callable[[str, int], None] | None = None,
    ) -> LiveRunInfo:
        """启动直播进程。"""

        with self._lock:
            if self.is_running():
                raise RuntimeError("直播进程已在运行")

            settings.logs_dir.mkdir(parents=True, exist_ok=True)
            session_id = f"live_{uuid.uuid4().hex[:12]}"
            log_path = settings.logs_dir / f"{session_id}.log"

            merged_env = os.environ.copy()
            if env:
                merged_env.update(env)

            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            self._process = proc
            self._session_id = session_id
            self._pid = proc.pid
            self._cmdline = command
            self._started_at = _now()
            self._log_path = log_path
            self._log_buffer.clear()

            reader = threading.Thread(
                target=self._read_output,
                args=(proc, log_path),
                daemon=True,
                name=f"live-log-reader-{session_id}",
            )
            reader.start()

            monitor = threading.Thread(
                target=self._monitor_exit,
                args=(proc, session_id, on_exit),
                daemon=True,
                name=f"live-monitor-{session_id}",
            )
            monitor.start()

            return LiveRunInfo(
                session_id=session_id,
                pid=proc.pid,
                cmdline=command,
                log_path=str(log_path),
                started_at=self._started_at,
            )

    def stop(self, force: bool = False) -> dict[str, object]:
        """停止当前直播进程。"""

        with self._lock:
            if not self._process:
                return {"stopped": False, "message": "当前没有运行中的直播进程"}

            proc = self._process

        # 在锁外执行阻塞等待，避免影响并发查询。
        if force:
            proc.kill()
            proc.wait(timeout=8)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=8)

        return {
            "stopped": True,
            "message": "直播进程已停止",
            "return_code": proc.returncode,
        }

    def tail_logs(self, limit: int = 200) -> list[str]:
        """读取最近日志行。"""

        if limit <= 0:
            return []

        with self._lock:
            return list(self._log_buffer)[-limit:]

    def _read_output(self, proc: subprocess.Popen[str], log_path: Path) -> None:
        """持续读取子进程输出，并写入日志文件和内存缓冲。"""

        if proc.stdout is None:
            return

        with log_path.open("a", encoding="utf-8") as f:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                with self._lock:
                    self._log_buffer.append(line)
                f.write(line + "\n")
                f.flush()

    def _monitor_exit(
        self,
        proc: subprocess.Popen[str],
        session_id: str,
        on_exit: Callable[[str, int], None] | None,
    ) -> None:
        """等待进程退出并触发回调。"""

        code = proc.wait()

        with self._lock:
            # 避免旧进程结束时误清理新进程状态。
            if self._session_id == session_id:
                self._process = None
                self._pid = None

        if on_exit:
            on_exit(session_id, code)


live_manager = LiveProcessManager()

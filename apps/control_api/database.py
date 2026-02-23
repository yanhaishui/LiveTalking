"""SQLite 数据访问与建表逻辑。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator

from .config import settings


# SQLite 连接是轻量级对象，使用“每次操作创建连接”的方式更稳定。
_DB_LOCK = threading.RLock()


def now_ts() -> str:
    """返回 ISO8601 时间戳（UTC）。"""

    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """获取数据库连接。

    注意:
    - `check_same_thread=False` 允许在多线程场景下使用独立连接。
    - 每次调用都创建新连接，避免跨线程复用同一连接导致问题。
    """

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """初始化数据库表结构。"""

    settings.data_dir.mkdir(parents=True, exist_ok=True)

    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS avatars (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_path TEXT NOT NULL,
            cover_image TEXT,
            status TEXT NOT NULL DEFAULT 'ready',
            tags TEXT,
            meta_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS voices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            engine TEXT NOT NULL,
            ref_wav_path TEXT,
            profile_json TEXT,
            preview_wav_path TEXT,
            status TEXT NOT NULL DEFAULT 'ready',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'sequential',
            interval_sec INTEGER NOT NULL DEFAULT 30,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS playlist_items (
            id TEXT PRIMARY KEY,
            playlist_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            weight INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id),
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS live_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_id TEXT NOT NULL,
            voice_id TEXT,
            model TEXT NOT NULL DEFAULT 'wav2lip',
            transport TEXT NOT NULL DEFAULT 'virtualcam',
            listen_port INTEGER NOT NULL DEFAULT 8010,
            tts TEXT,
            tts_server TEXT,
            ref_file TEXT,
            ref_text TEXT,
            extra_args TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS live_sessions (
            id TEXT PRIMARY KEY,
            preset_id TEXT,
            status TEXT NOT NULL,
            pid INTEGER,
            cmdline TEXT,
            log_path TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            error TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            payload_json TEXT,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 0,
            retry_backoff_sec INTEGER NOT NULL DEFAULT 5,
            parent_job_id TEXT,
            result_json TEXT,
            error TEXT,
            last_error_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS room_messages (
            id TEXT PRIMARY KEY,
            platform TEXT,
            room_id TEXT,
            source_msg_id TEXT,
            user_id TEXT,
            user_name TEXT,
            content TEXT NOT NULL,
            msg_time TEXT,
            priority INTEGER NOT NULL DEFAULT 50,
            source_payload_json TEXT,
            handled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS replies (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            message_id TEXT,
            answer TEXT NOT NULL,
            source TEXT,
            priority INTEGER NOT NULL DEFAULT 80,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            detail_json TEXT,
            created_at TEXT NOT NULL
        )
        """,
    ]

    with _DB_LOCK:
        with get_conn() as conn:
            cur = conn.cursor()
            for ddl in ddl_statements:
                cur.execute(ddl)
            _run_migrations(cur)


def _run_migrations(cur: sqlite3.Cursor) -> None:
    """对旧版本数据库执行轻量迁移。

    说明:
    - `CREATE TABLE IF NOT EXISTS` 不会为旧表补列。
    - 这里通过 `PRAGMA table_info` 检测并补齐 Phase 2 需要的字段。
    """

    # jobs: 失败恢复与自动重试元数据。
    _ensure_column(cur, "jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "jobs", "max_retries", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "jobs", "retry_backoff_sec", "INTEGER NOT NULL DEFAULT 5")
    _ensure_column(cur, "jobs", "parent_job_id", "TEXT")
    _ensure_column(cur, "jobs", "last_error_at", "TEXT")

    # voices: 试听产物路径。
    _ensure_column(cur, "voices", "preview_wav_path", "TEXT")

    # room_messages: 平台消息标准化与优先级字段。
    _ensure_column(cur, "room_messages", "source_msg_id", "TEXT")
    _ensure_column(cur, "room_messages", "priority", "INTEGER NOT NULL DEFAULT 50")
    _ensure_column(cur, "room_messages", "source_payload_json", "TEXT")

    # replies: 调度优先级。
    _ensure_column(cur, "replies", "priority", "INTEGER NOT NULL DEFAULT 80")

    # 平台消息去重索引（仅对带 source_msg_id 的记录生效）。
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_room_messages_platform_room_source
        ON room_messages (platform, room_id, source_msg_id)
        WHERE source_msg_id IS NOT NULL
        """
    )


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    """如果列不存在，则执行 `ALTER TABLE ... ADD COLUMN`。"""

    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    exists = any(str(row[1]) == column for row in rows)
    if exists:
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    """执行写操作，返回影响行数。"""

    with _DB_LOCK:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """查询单条记录并转为字典。"""

    with _DB_LOCK:
        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """查询多条记录并转为字典列表。"""

    with _DB_LOCK:
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

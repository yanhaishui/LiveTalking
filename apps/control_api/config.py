"""控制层配置模块。

该模块集中管理路径、默认参数和运行时常量，避免散落在各业务文件中。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass(frozen=True)
class Settings:
    """应用配置。

    说明:
    - `project_root` 使用 control_api 的上两级目录，确保在项目内任意目录运行都能定位到根目录。
    - `python_exec` 默认取当前解释器，便于在虚拟环境内启动 `app.py`。
    """

    project_root: Path = Path(__file__).resolve().parents[2]
    data_dir: Path = project_root / "data"
    db_path: Path = data_dir / "meta.db"
    logs_dir: Path = data_dir / "logs"

    app_entry: Path = project_root / "app.py"
    python_exec: str = sys.executable

    # 直播默认参数（可被 API 请求覆盖）
    default_transport: str = "virtualcam"
    default_model: str = "wav2lip"
    default_listen_port: int = 8010

    # 日志缓冲条数
    live_log_buffer_size: int = 2000


settings = Settings()

"""本地启动入口。

用法:
    python -m apps.control_api
"""

from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run("apps.control_api.main:app", host="127.0.0.1", port=9001, reload=False)

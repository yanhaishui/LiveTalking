# control_api

LiveTalking 管理台后端控制层（第一版骨架）。

## 已实现能力
- 系统接口: 健康检查、能力检测
- 系统体检: `system/checks`（模型文件、XTTS 连通、端口占用等）
- 资产管理: Avatar / Voice CRUD + 克隆任务提交
- 本地文件上传: `uploads/avatar-video`、`uploads/voice-wav`（用于浏览器选择文件后自动上传）
- 声音试听: 支持 `voices/{id}:preview` 生成 XTTS 试听 wav
- 脚本与轮播: Script / Playlist 基础 CRUD
- 直播预设: Preset 基础 CRUD
- 直播控制: 启动/停止 `app.py` 子进程、查询状态、查看日志
- 统一播报调度: 多轮播计划并发调度 + 回复插播优先（`/human` 注入）
- 消息与回复: 消息入库、规则回复生成、回复入队播报、优先级调度
- 平台消息标准化入口: `platform/messages:ingest`（去重 + 自动生成回复 + 自动入播报队列）
- 任务中心: 任务创建/列表/详情/取消
- 任务重试: `jobs/{id}:retry`（基于历史配置创建新任务）
- 失败恢复与重试: jobs 支持重启恢复、自动回退重试（可配置次数与间隔）

## 启动方式
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking
source .venv/bin/activate
python -m apps.control_api
```

默认监听: `127.0.0.1:9001`

## 依赖
请确保已安装:
- fastapi
- uvicorn

可执行:
```bash
pip install fastapi uvicorn
```

## 代码说明
- `main.py`: 接口入口与业务编排
- `database.py`: SQLite 建表与查询封装
- `live_runtime.py`: 直播进程管理器
- `job_runner.py`: 异步任务执行器（wav2lip/xtts）
- `speaker_dispatcher.py`: 统一播报调度器（脚本轮播 + 回复插播）
- `schemas.py`: 请求参数模型
- `config.py`: 路径与默认配置

## 数据库
- 文件: `data/meta.db`
- 启动时自动初始化表结构

## 注意事项
- 当前版本默认单直播实例运行，避免虚拟摄像头冲突。
- 建议使用 `virtualcam` 运行模式；`webrtc` 的 sessionid 为动态值，当前不支持自动轮播/自动插播。
- 真实平台（抖音/快手/视频号）消息采集器尚未内置，本项目已提供标准化接入接口供外部采集器调用。

# web_admin

LiveTalking 管理台前端（静态页面版）。

## 快速预览
1. 启动 control-api:
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking
source .venv/bin/activate
python -m apps.control_api
```

2. 启动静态文件服务（任一方式）:
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/web_admin
python -m http.server 9100
```

3. 打开浏览器:
- [http://127.0.0.1:9100](http://127.0.0.1:9100)

## 说明
- 页面默认请求 `http://127.0.0.1:9001`。
- 可在页面顶部修改并保存 API 地址。
- 已支持:
  - 模块化导航（首页/直播/形象/声音/脚本/轮播/预设/任务消息）
  - 系统体检页（模型文件、资产目录、XTTS 连通性、端口占用检查）
  - 页面内编辑表单（不再依赖浏览器弹窗编辑）
  - 全局状态芯片 + Toast 通知 + 自动刷新（15s）
  - 各模块列表搜索/筛选/统计 + 复制ID快捷操作
  - 直播控制（启动/停止/状态/日志）
  - 三步开播向导（基础配置/声音配置/推流确认）
  - 会话日志实时追踪（自动轮询、自动滚动、关键词过滤、复制/清空视图）
  - 形象/声音克隆支持本机文件选择（自动上传并回填服务器路径）
  - 文件上传进度条 + 格式/大小校验
  - 播报调度器状态查看与手工播报入队（支持优先级）
  - Avatar/Voice 克隆任务提交与资产管理（Voice 支持参数化克隆 + 试听）
  - 脚本库管理
  - 轮播计划管理（含脚本挂载，多计划并发调度）
  - 直播预设管理（支持“一键按预设开播”）
  - 平台消息联调入口（去重 + 自动生成回复 + 自动入播报队列）
  - 任务中心（查看/取消/失败重试/详情日志抽屉）
  - OBS 接入说明（virtualcam 方式 + rtcpush 方式）

## 常见问题
- 若浏览器控制台出现 `OPTIONS 405`，请确认已重启 `control_api` 到最新版本（含 CORS 中间件）。
- `favicon.ico 404` 不影响功能。
- 若轮播/插播没有发声，请先确认当前开播使用的是 `virtualcam`（`webrtc` 暂不支持自动播报注入）。
- OBS 推流地址与串流密钥是在 OBS 软件中设置，不在本系统里设置（除非你使用 `rtcpush` 并填写 `push_url`）。

# LiveTalking 管理台开发 TODO

## 0. 文档说明
- 目标: 将当前 LiveTalking 从命令行演示升级为可交付客户使用的本地产品。
- 当前范围: 已完成本地 Web 管理台与 Phase 2 增强，现已启动 Electron 桌面版研发（Windows + macOS）。
- 技术基线: `wav2lip + virtualcam` 为主链路，`webrtc/rtcpush` 作为扩展。
- 进度标识:
  - `[ ]` 未开始
  - `[~]` 进行中
  - `[x]` 已完成

---

## 1. 优先级清单（去重后）

## P0（当前必须完成）
- [x] 视频克隆任务流水线: 失败恢复 + 自动重试
- [x] 声音克隆任务流水线: 试听能力 + 参数化
- [x] 脚本轮播与问答混合编排: 多计划并发 + 问答优先
- [~] 直播间消息接入: 平台标准化入口 + 去重 + 优先级调度（已落地统一入口，待接各平台官方采集器）

## P1（P0 完成后）
- [ ] 关键模块中文注释补齐（流程、状态机、异常分支）
- [ ] 统一接口错误码与错误信息结构
- [ ] 关键功能单元测试/集成测试

## P2（桌面端产品化）
- [x] Electron 桌面端封装（Windows + macOS）
- [~] 安装器与升级机制（已接入 NSIS/DMG + autoUpdater 框架，待证书发布联调）
- [~] 审计/权限/导入导出（已实现设置导入导出 + 诊断包导出，待权限体系）

---

## 2. 已完成能力（基线）

## 2.1 资产与管理
- [x] UI 克隆真人视频模型（wav2lip avatar）
- [x] UI 克隆真人声音模型（上传 wav 一键克隆）
- [x] Avatar/Voice/Script/Playlist/Preset 的 CRUD
- [x] 预置注册 `wav2lip256_avatar1` 到本地元数据库
- [x] 直播控制/预设中的 `Avatar ID`、`TTS`、`REF_FILE`、`REF_TEXT` 下拉联动

## 2.2 直播与运维
- [x] UI 一键启动/停止直播（托管 `app.py` 子进程）
- [x] 直播运行状态与日志查询
- [x] OBS 接入说明与 `rtcpush push_url` 配置入口
- [x] 管理台品牌名替换为“码布斯 MEH 数字人系统”

## 2.3 自动播报与任务中心
- [x] 任务中心（创建/查询/取消）
- [x] `avatars:clone / voices:clone` 异步任务接口
- [x] 消息入库 + 规则回复 + speak 入队
- [x] 统一播报调度器（轮播脚本 + 智能回复插播）
- [x] 调度器状态查询与手工播报接口

---

## 3. 技术路线（已确认）

## 3.1 总体架构
- 运行时引擎: 复用 `app.py`
- 控制层: `apps/control_api`（FastAPI）
- 前端层: `apps/web_admin`
- 数据层: SQLite `data/meta.db` + 文件资产目录
- 进程层: control-api 托管 `app.py` 子进程（启停/日志/状态）

## 3.2 目录规划
- `apps/control_api/`: API、任务队列、进程管理、调度器、数据访问
- `apps/web_admin/`: 本地管理台前端
- `data/avatars/`: 数字人视频资产
- `data/voices/`: 声音资产
- `data/meta.db`: 元数据数据库

---

## 4. 核心流程（目标态）

## 4.1 视频克隆（wav2lip）
1. 上传视频素材并提交任务
2. 后台执行 `wav2lip/genavatar.py`
3. 失败自动重试，进程中断可恢复
4. 成功产物落盘并注册 Avatar

## 4.2 声音克隆（xtts）
1. 上传参考音频并提交克隆任务
2. 后台调用 XTTS `clone_speaker`
3. 克隆成功后支持试听（样例文案合成）
4. 声音参数可配置并保存到 profile

## 4.3 轮播 + 智能回复混合编排
1. 多轮播计划并发调度
2. 检测提问后生成回复并进入高优先级队列
3. 插播优先，结束后继续轮播

---

## 5. 接口与数据（现状）

## 5.1 接口
- [x] 系统状态: `health/capabilities`
- [x] 资产管理: `avatars/voices` + `:clone`
- [x] 脚本与轮播: `scripts/playlists` + `playlist_items`
- [x] 直播控制: `live/sessions` + `live/presets`
- [x] 消息与回复: `room/messages` + `replies`
- [x] 任务中心: `jobs` + `job_logs`
- [x] `POST /api/v1/voices/{voice_id}:preview`（声音试听）
- [x] `POST /api/v1/platform/messages:ingest`（平台消息标准化接入）
- [x] jobs 重试状态字段（`retry_count/max_retries/retry_backoff_sec`）已落库

## 5.2 数据表
- [x] `avatars`
- [x] `voices`
- [x] `scripts`
- [x] `playlists`
- [x] `playlist_items`
- [x] `live_presets`
- [x] `live_sessions`
- [x] `jobs`
- [x] `job_logs`
- [x] `room_messages`
- [x] `replies`
- [x] `system_settings`
- [x] `audit_events`
- [x] jobs 扩展字段: 重试计数/重试上限/回退秒数/最后错误时间
- [x] voices 扩展字段: `preview_wav_path`
- [x] room_messages 扩展字段: `source_msg_id/priority/source_payload_json`
- [x] replies 扩展字段: `priority`

---

## 6. 分阶段计划（去重后）

## Phase 1: MVP（已完成）
- [x] 控制 API 骨架、数据库初始化、直播启停、基础 CRUD
- [x] 前端管理台基础框架 + 轮播计划/直播预设管理
- [x] 异步任务中心 + CORS + 基础联调

## Phase 2: 业务增强（当前进行中）
- [x] 视频克隆任务失败恢复与自动重试
- [x] 声音克隆试听与参数化
- [x] 多轮播计划并发策略
- [~] 平台消息接入与优先级调度细化（待接各平台官方消息采集器）

## Phase 3: 产品化（进行中）
- [x] Electron 桌面端基础工程（主进程/预加载/渲染层/本地控制 API 托管）
- [x] Windows 打包链路（NSIS）与安装体验
- [x] macOS 打包链路（DMG）与安装体验
- [~] 代码签名（Windows 证书 / macOS notarization）
- [~] 自动更新（electron-updater）与版本发布策略
- [~] 审计/权限/导入导出

---

## 8. Electron 桌面版研发清单（Windows + macOS）

### 8.1 架构与目录（第一阶段）
- [x] 新增 `apps/desktop` 工程目录与基础脚手架
- [x] 主进程能力: 窗口管理、托盘、生命周期、崩溃恢复
- [x] 预加载桥接: 仅暴露白名单 IPC（状态/启停/日志/设置）
- [x] 渲染层容器: 承载现有 `web_admin`，提供桌面状态条与快捷入口

### 8.2 本机运行能力
- [x] 托管 `control_api` 子进程（启动/停止/重启/状态）
- [x] Python 解释器路径探测（mac `.venv/bin/python`、windows `.venv\\Scripts\\python.exe`）
- [x] 本地日志采集与“导出诊断包”
- [x] 端口占用冲突提示（9001/8010/自定义端口）

### 8.3 可用性与引导
- [x] 首次启动向导（环境检测/模型检测/XTTS连通）
- [x] 一键修复建议（缺依赖、端口冲突、模型缺失）
- [x] 本机模式/云端模式切换（低配机器默认云端）

### 8.4 打包与发布（跨平台）
- [x] Windows: `electron-builder + NSIS` 出 `exe`
- [x] macOS: `electron-builder + DMG` 出安装包
- [x] CI 打包流水线（GitHub Actions: windows-latest + macos-latest）
- [x] 版本规范与渠道（内测/正式）

### 8.5 安全与发布门槛
- [~] Windows 代码签名（避免安装拦截）
- [~] macOS 签名 + notarization（避免“已损坏”提示）
- [x] 自动更新灰度策略与回滚开关

---

## 7. 今日滚动记录
- [x] 去重并按优先级重排 TODO，明确 Electron 进入产品化阶段
- [x] 已完成历史交付项同步为基线状态
- [x] 完成任务失败恢复与自动重试（`jobs` 重启恢复 + 自动回退重试）
- [x] 完成声音克隆参数化与自动试听（XTTS `tts_stream` 产出 wav）
- [x] 完成多轮播计划并发调度（不再仅执行第一条计划）
- [x] 完成消息优先级调度链路（reply/manual/playlist 分级入队）
- [x] 新增平台消息标准化入口（去重 + 自动生成回复 + 自动入播报队列）
- [x] 管理台新增“声音试听”“参数化克隆”“平台消息联调”操作入口
- [x] 管理台 UI 重构为“首页 + 模块化页面”结构，并将编辑从浏览器弹窗改为页面内表单
- [x] 管理台 UI 二次增强：全局状态芯片、Toast 通知、模块搜索筛选统计、自动刷新与复制ID快捷操作
- [x] 管理台日志体验增强：实时追踪开关、自动滚动、刷新间隔、关键词过滤、复制/清空视图
- [x] 形象/声音克隆支持本机文件选择上传（无需手输绝对路径）
- [x] 任务中心增强：失败原因展示、任务详情抽屉、一键重试
- [x] 系统体检模块：模型/资产/XTTS/端口可用性开播前检查
- [x] 直播控制升级为三步开播向导（基础/声音/推流）
- [x] 上传体验增强：文件大小与格式校验 + 实时上传进度条
- [x] Electron 第一版骨架已落地：`main/preload/renderer` + 状态栏 + 日志面板 + 嵌入 web_admin
- [x] Electron 主进程已托管 control_api：支持启动/停止/重启与实时状态同步
- [x] web_admin 增加桌面桥接：支持桌面端下发 API 地址并自动应用
- [x] Electron 主进程增强：托盘、崩溃自动恢复、端口冲突检测、设置持久化、诊断包导出
- [x] Electron 桌面 UI 增强：首次向导、环境体检、local/cloud 模式切换、更新检查、设置导入导出
- [x] 跨平台打包发布基线：`electron-builder` 配置 + GitHub Actions 工作流 + 发布文档
- [x] 本机实测打包通过：`npm run dist:mac` 产出 DMG、`npm run dist:win` 产出 EXE
- [x] 自动更新策略补齐：支持 stable/beta 通道切换 + 更新检查总开关（回滚）  
- [x] macOS 公证自动化钩子已接入：`afterSign -> apps/desktop/scripts/notarize.js`
- [x] 发布源企业化配置：`electron-builder.config.js` 支持 GitHub/Generic 发布源与环境变量注入
- [x] 客户交付文档补齐：新增桌面版客户使用手册（安装/首启/日常/排障）
- [x] 修复安装版启动路径问题：支持 UI 选择项目目录 + 内置 web_admin 资源兜底
- [x] 修复更新检查体验：未配置更新源不报堆栈，404/网络错误改为友好提示并缩短顶部文案
- [x] 修复桌面端中部布局挤压：取消右侧面板等分压缩，日志/设置/体检区恢复正常分区显示
- [x] 增强版本可见性：桌面首页展示 Desktop 版本号，便于确认是否已安装最新包

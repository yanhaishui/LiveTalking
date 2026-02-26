# LiveTalking 管理台开发 TODO（精简与扩展）

本文件为项目当前已知事项与新增“数字人（视频 + 声音）克隆”工程级清单。变更以工程角度（数字人专家 / 模型专家 / 架构师 / 工程师）表述，并匹配当前代码库结构（`apps/`、`wav2lip/`、`musetalk/`、`ultralight/`、`models/`、`data/`）。

---

**当前基线（摘录）**
- **运行时**: `app.py` 驱动，控制层在 `apps/control_api`（FastAPI），前端在 `apps/web_admin`。
- **资产存放**: 运行时素材在 [data/](data/)，训练/推理模型在 [models/](models/)。
- **已具备能力**: Video/Voice 克隆入口、任务中心、Electron 桌面骨架与基本打包链路。

---

**数字人克隆工程清单（视频 + 声音 → 本地数字人模型）**

- **目标说明**: 将用户在绿幕前拍摄的真人视频与参考语音，转换为可复用、可部署的“数字人”资产（视觉模型产物落在 [models/](models/)；运行时素材与元数据落在 [data/](data/)），支持离线训练、增量更新与实时推理调用。

- **1. 数据采集与元数据规范**: 定义并实现上传校验（视频 MP4、帧率、分辨率、绿幕色度规范；音频 16/24kHz WAV 单声道）。为每个 take 生成结构化元数据（face_id, take_id, mic_profile, chroma_info），并写入 `data/meta.db`。

- **2. 绿幕预处理与分割**: 实现色度抠像 + 深度分割的流水线（人脸/头发/肩部分层蒙版、alpha 修复、边缘精细化）。优先复用 `musetalk/utils/face_detection` 与 `ultralight/face_detect_utils`，并提供 GPU/CPU 两套实现以兼容低配机器。

- **3. 视频→视觉模型流水线**: 设计训练流程：帧抽取→关键点对齐→时序增强→训练/微调（wav2lip / musetalk 的 U-Net / VAE 模型）→验证（嘴型同步率/视觉伪影检测）→模型导出（权重+config+version）。产物写入 [models/avatars/] 并注册到元库。

- **4. 声音克隆流水线**: 明确使用接口（如 `clone_speaker`），实现特征提取（mel, f0）、数据增强（噪声/混响）、训练/微调、导出 speaker embedding 与合成器权重。产物写入 [models/voices/]；提供 `POST /api/v1/voices/{id}:preview` 试听接口。

- **5. 模型元数据与兼容性**: 统一模型 schema（name, version, backbone, sample_rate, input_shape, metrics），支持 PyTorch(.pth/.pt)、ONNX(.onnx)、TorchScript 以便不同 runtime 使用。

- **6. 产物注册与管理 API**: 在 `apps/control_api` 增加 `POST /api/v1/models/register`、`GET /api/v1/models`、`DELETE /api/v1/models/{id}`，并扩展 `avatars`/`voices` 表字段（model_path, model_version, metrics）。

- **7. 推理服务设计**: 提供可切换 GPU/CPU 的本地推理 runtime（FastAPI 或独立子进程），暴露批量与流式接口：视觉推理（wav2lip/musetalk）、声学合成（xtts）。支持批量队列化与优先级处理（任务中心集成）。

- **8. A/V 同步与延迟控制**: 明确端到端延迟预算（capture→process→render），实现时间戳对齐、帧插值与 playout buffer，在 `apps/control_api/live_runtime.py` 中集成延迟补偿策略。

- **9. 集成与复用现有模块**: 将流水线对接 `wav2lip/`、`musetalk/`、`ultralight/`，并在 `apps/` 增加任务处理器（avatars:train/import/export，voices:train/import/export）。

- **10. 质量评估与自动化回退**: 建立自动化评估套件（嘴型同步 L1/L2、音频合成近似 MOS、视觉伪影检测），不达标自动回退并触发人工审核流程。

- **11. 隐私与合规**: 上传与导出流程加入同意/许可字段；实现模型/数据的访问控制与审计（扩展 `audit_events`），并对导出模型支持可选加密。

- **12. CI/训练自动化与部署**: 提供训练脚本 `scripts/train_avatar.sh` / `scripts/train_voice.sh`、轻量推理镜像 `Dockerfile.inference`，并在 CI 中加入训练/验证阶段的 smoke tests。

- **13. 交付页与用户引导**: 在 `apps/web_admin` 增加“数字人导入/克隆”页面与上传向导；在 `docs/` 添加采集指南（绿幕要求、灯光、麦克风设置）和一键复现示例（从上传到推理）。

**交付优先级建议**
- P0: 数据规范 + 上传校验 + 绿幕预处理 + 快速试听验收路径（最小可用流程）。
- P1: 视觉/声音训练流水线 + 模型导出 + 元数据注册 + 推理 API。 
- P2: 自动评估、ONNX/TorchScript 优化、CI 镜像、桌面集成体验打磨。

---

如需，我可将上述每个要点拆成具体实现任务（含目标文件、API 定义与关键脚本），形成可直接执行的 issue/PR 清单并再次写入 `TODO.md`。

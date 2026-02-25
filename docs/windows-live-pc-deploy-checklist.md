# LiveTalking Windows 直播电脑部署清单（GPU 本机推理）

本文档用于同事在 Windows 直播电脑上本地部署并启动 LiveTalking，让该电脑自己的显卡参与推理。

## 1. 部署目标（先确认）

- 运行位置: Windows 直播电脑本机运行 `python app.py ...`。
- GPU 使用: 数字人口型推理使用 Windows 直播电脑的 GPU。
- 对外访问: 局域网其他电脑通过 `http://<Windows电脑IP>:8010/...` 访问。

## 2. 一次性准备清单

- [ ] Windows 10/11 64 位（建议专业版，最新补丁）。
- [ ] NVIDIA 独显（建议 3060 及以上，显存 8GB+）。
- [ ] 已安装最新显卡驱动，`nvidia-smi` 可用。
- [ ] 安装 Git。
- [ ] 安装 Miniconda（推荐）或 Python 3.10。
- [ ] 安装 Microsoft VC++ Runtime（建议）。
- [ ] 下载 LiveTalking 项目代码。
- [ ] 下载模型和 Avatar 素材（至少 `wav2lip.pth` + 一个 avatar）。
- [ ] 放通防火墙端口（至少 TCP 8010；WebRTC 场景需要 UDP）。

## 3. 下载地址（官方/项目）

## 3.1 基础软件

- Git for Windows: [https://git-scm.com/download/win](https://git-scm.com/download/win)
- Miniconda 安装说明: [https://www.anaconda.com/docs/getting-started/miniconda/install](https://www.anaconda.com/docs/getting-started/miniconda/install)
- Python Windows 下载页（如不用 Conda）: [https://www.python.org/downloads/windows/](https://www.python.org/downloads/windows/)
- NVIDIA 驱动下载: [https://www.nvidia.com/Download/index.aspx](https://www.nvidia.com/Download/index.aspx)
- CUDA 下载页（按需）: [https://developer.nvidia.com/cuda-downloads](https://developer.nvidia.com/cuda-downloads)
- PyTorch 安装向导: [https://pytorch.org/get-started/locally/](https://pytorch.org/get-started/locally/)
- VC++ Runtime: [https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170)

## 3.2 项目与模型

- LiveTalking 仓库: [https://github.com/lipku/LiveTalking](https://github.com/lipku/LiveTalking)
- 模型/素材下载（项目 README 提供）:
  - 夸克: [https://pan.quark.cn/s/83a750323ef0](https://pan.quark.cn/s/83a750323ef0)
  - Google Drive: [https://drive.google.com/drive/folders/1FOC_MD6wdogyyX_7V1d4NDIO7P9NlSAJ?usp=sharing](https://drive.google.com/drive/folders/1FOC_MD6wdogyyX_7V1d4NDIO7P9NlSAJ?usp=sharing)

## 4. 详细部署步骤（推荐 Conda 方案）

以下命令建议在 `Anaconda Prompt` 执行。

## 4.1 获取代码

```powershell
cd /d D:\
git clone https://github.com/lipku/LiveTalking.git
cd LiveTalking
```

如果公司内已经有维护仓库，可改为你们自己的仓库地址。

## 4.2 创建 Python 环境并安装依赖

```powershell
conda create -n livetalking python=3.10 -y
conda activate livetalking
```

按项目 README（CUDA 12.4 示例）安装 PyTorch：

```powershell
conda install pytorch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 pytorch-cuda=12.4 -c pytorch -c nvidia -y
```

安装项目依赖：

```powershell
pip install -r requirements.txt
```

## 4.3 放置模型与素材

按以下结构准备（关键）：

- `models/wav2lip.pth`
- `data/avatars/wav2lip256_avatar1/`（整个目录）

如果下载包里权重文件名不是 `wav2lip.pth`，请重命名后放到 `models/`。

## 4.4 部署后自检

```powershell
python -c "import torch;print('cuda=',torch.cuda.is_available());print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')"
```

如果输出 `cuda=True` 且打印出显卡型号，说明 GPU 环境正常。

## 4.5 启动服务（WebRTC）

```powershell
cd /d D:\LiveTalking
conda activate livetalking
python app.py --transport webrtc --model wav2lip --avatar_id wav2lip256_avatar1 --listenport 8010
```

启动成功后，日志会提示访问地址模板：

- `http://<serverip>:8010/dashboard.html`
- `http://<serverip>:8010/webrtcapi.html`

## 4.6 防火墙放行（管理员 PowerShell）

至少放行 TCP 8010：

```powershell
New-NetFirewallRule -DisplayName "LiveTalking TCP 8010" -Direction Inbound -Protocol TCP -LocalPort 8010 -Action Allow -Profile Private
```

项目 README 对 WebRTC 的建议是开放 UDP 端口（1-65535）。内网环境可按安全策略收敛：

```powershell
New-NetFirewallRule -DisplayName "LiveTalking UDP WebRTC" -Direction Inbound -Protocol UDP -LocalPort 1-65535 -Action Allow -Profile Private
```

## 4.7 获取 Windows 电脑 IP 并给同事访问

```powershell
ipconfig
```

找到本机 IPv4 地址（例如 `192.168.1.20`），同事访问：

- `http://192.168.1.20:8010/dashboard.html`

## 5. 验收清单（上线前）

- [ ] 本机访问 `http://127.0.0.1:8010/dashboard.html` 正常打开。
- [ ] 局域网其他电脑访问 `http://<Windows_IP>:8010/dashboard.html` 正常。
- [ ] 触发一次播报，画面与语音正常。
- [ ] `nvidia-smi` 能看到 Python 进程占用显存/算力。
- [ ] 端口未冲突（8010 未被其它程序占用）。

## 6. 常见问题

## 6.1 启动报错找不到模型

- 检查 `models/wav2lip.pth` 是否存在。
- 检查 `data/avatars/<avatar_id>` 是否存在并与启动参数一致。

## 6.2 浏览器能打开页面但没有视频

- 优先检查防火墙 UDP 策略。
- 确认启动参数是 `--transport webrtc`。
- 尝试先本机浏览器访问，再跨机器访问。

## 6.3 CUDA 不可用

- 先执行 `nvidia-smi`，确认驱动正常。
- 重新核对 PyTorch 与 CUDA 版本匹配关系（见 PyTorch 官方安装页）。
- 确认当前在 `livetalking` 环境内执行。

## 6.4 依赖安装慢或失败

- 先单独安装 PyTorch，再安装 `requirements.txt`。
- 网络受限时可使用企业内网镜像源。

## 7. 可选: MEH Desktop（桌面壳）本机模式

如果使用桌面端 `.exe`，它本质是管理壳。要让本机 GPU 推理生效，仍需本机有完整 LiveTalking 代码、Python 环境、模型和素材。  
桌面端首次运行后，在“运行设置”里选择 LiveTalking 项目目录，再启动本地 API。

## 8. 一键启动（start_livetalking.bat）

仓库已提供一键启动脚本：

- `start_livetalking.bat`
- 位置: LiveTalking 项目根目录

使用方式：

1. 确认你已经完成第 4 章环境安装（Conda、依赖、模型、素材）。
2. 双击 `start_livetalking.bat`。
3. 看到启动日志后，访问 `http://127.0.0.1:8010/dashboard.html`。
4. 其他电脑访问 `http://<Windows_IP>:8010/dashboard.html`。

可配置项（编辑 bat 文件顶部）：

- `LIVETALKING_ENV`：Conda 环境名（默认 `livetalking`）。
- `TRANSPORT`：传输模式（默认 `webrtc`）。
- `MODEL`：模型（默认 `wav2lip`）。
- `AVATAR_ID`：素材 ID（默认 `wav2lip256_avatar1`）。
- `LISTEN_PORT`：监听端口（默认 `8010`）。
- `LIVETALKING_PYTHON`：可选，指定 Python 绝对路径，跳过 Conda 激活。

# desktop (Electron)

`apps/desktop` 是码布斯 MEH 数字人系统的桌面端壳工程，面向 Windows/macOS 客户端交付。

## 第一阶段已实现
- 主进程托管 `python -m apps.control_api`
- Local/Cloud 模式切换（本机 API / 远程 API）
- 端口冲突检测（9001 / 直播端口）
- 环境体检（仓库结构、Python、模型、XTTS、端口）
- API 崩溃自动恢复（指数退避，最多 3 次）
- 托盘能力（最小化到托盘、托盘菜单启停 API）
- 运行日志面板 + 诊断包导出
- 设置导入/导出
- 自动更新检查框架（`electron-updater`）
  - 支持稳定/测试通道切换（`stable` / `beta`）
  - 支持“禁用更新检查”回滚开关
- 内嵌 `web_admin` 并通过 `postMessage` 自动注入 API 地址

## 本地开发启动
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop
npm install
npm run dev
```

## 可选环境变量
- `LIVETALKING_REPO_ROOT`: 指定 LiveTalking 仓库根目录
- `LIVETALKING_PYTHON`: 指定 Python 解释器路径（优先级最高）

## 打包命令
```bash
# macOS DMG
npm run dist:mac

# Windows NSIS
npm run dist:win
```

## 发布到企业仓库（自动更新源）
通过环境变量声明发布源（默认不发布）：

```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop
DESKTOP_PUBLISH_PROVIDER=github \
DESKTOP_PUBLISH_OWNER=yanhaishui \
DESKTOP_PUBLISH_REPO=LiveTalking \
GH_TOKEN=<your_token> \
npm run dist:mac:publish
```

支持参数：
- `DESKTOP_PUBLISH_PROVIDER=github|generic`
- `DESKTOP_PUBLISH_OWNER` / `DESKTOP_PUBLISH_REPO`
- `DESKTOP_PUBLISH_RELEASE_TYPE=release|prerelease|draft`
- `DESKTOP_PUBLISH_URL`（generic 模式）

## 自动更新说明
- 已接入 `electron-updater`。
- 默认未强绑定发布源，避免本地打包失败。
- 生产发布时请在 CI/发布分支中注入 `electron-builder` 的 `publish` 配置（如 GitHub Releases / 私有更新服务）。
- 未配置发布源时，点击“检查更新”会返回提示，但不影响主功能。

## 签名与发布
- Windows 签名、macOS notarization、CI 打包流程见：
  - `/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/docs/desktop-release.md`
  - `/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/.github/workflows/desktop-build.yml`
- 已配置 `afterSign -> scripts/notarize.js`，当 CI 提供 Apple 凭据时自动公证；无凭据会自动跳过。

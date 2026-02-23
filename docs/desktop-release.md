# Electron 桌面版发布指南（Windows + macOS）

## 1. 构建产物
- Windows: NSIS 安装包（`.exe`）
- macOS: DMG 安装包（`.dmg`）

对应命令：
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop
npm install
npm run dist:win
npm run dist:mac
```

发布配置文件：
- `/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop/electron-builder.config.js`

## 2. 代码签名

## 2.1 Windows 签名
`electron-builder` 会读取以下环境变量进行签名：
- `CSC_LINK`: 证书文件（base64 或文件路径）
- `CSC_KEY_PASSWORD`: 证书密码

未配置签名时，安装包可生成，但 Windows SmartScreen 风险提示会更明显。

## 2.2 macOS 签名 + notarization
建议在 CI 中配置：
- `APPLE_ID`
- `APPLE_APP_SPECIFIC_PASSWORD`
- `APPLE_TEAM_ID`
- `CSC_LINK`
- `CSC_KEY_PASSWORD`

未完成 notarization 时，用户可能看到“应用已损坏/无法验证开发者”。
项目已内置公证钩子：`/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop/scripts/notarize.js`。

## 3. 自动更新
当前使用 `electron-updater`，发布源可选 GitHub Releases 或私有更新源。

要求：
1. 设置发布环境变量并使用 `*:publish` 脚本。  
2. 每次发布时递增 `apps/desktop/package.json` 的 `version`。  
3. 客户端在“检查更新”中触发版本检查。

示例（GitHub Releases）：
```bash
cd /Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/apps/desktop
DESKTOP_PUBLISH_PROVIDER=github \
DESKTOP_PUBLISH_OWNER=yanhaishui \
DESKTOP_PUBLISH_REPO=LiveTalking \
DESKTOP_PUBLISH_RELEASE_TYPE=release \
GH_TOKEN=<token> \
npm run dist:mac:publish
```

灰度策略建议：
- `stable` 通道给正式用户。
- `beta` 通道给内测用户。
- 出现回归时，可在客户端设置里关闭“自动更新检查”作为快速回滚开关。

## 4. CI 打包
已提供工作流：
- `/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/.github/workflows/desktop-build.yml`

默认行为：
- 在 `workflow_dispatch` 或 `v*` 标签 push 时触发
- 分别在 `windows-latest` / `macos-latest` 打包
- `v*` 标签默认走发布脚本（自动更新可用）
- 上传构建产物到 GitHub Actions Artifacts

## 5. 发布建议流程
1. 本地回归（启动桌面端、体检、开播链路）
2. 提升版本号并提交
3. 触发 CI 生成安装包
4. 用测试机器安装验证（Windows + macOS）
5. 发布 GitHub Release（供自动更新拉取）

## 6. 客户侧使用文档
- `/Users/yanhaishui/IdeaProjects/jhipster/LiveTalking/docs/customer-desktop-guide.md`

补充说明：
- 安装版首次启动需要在桌面端“运行设置”里选择 LiveTalking 项目目录（包含 `app.py`）。
- 桌面端已内置 `web_admin` 静态资源，可作为管理台托管兜底。

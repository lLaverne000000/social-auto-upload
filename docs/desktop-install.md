# 离线桌面版安装与使用

离线桌面版把图形界面、完整 `sau` CLI、Python 运行时和匹配版本的 Chromium
放进安装包。安装后不依赖 Codex、源码目录、系统 Python、Node.js，也不需要首次
下载浏览器。

这份说明适用于通过原生 macOS Intel、macOS Apple Silicon 和 Windows x64 验证后
生成的正式交付文件。只有拿到与发布清单和校验值匹配的安装包，才按下面步骤安装；
源码仓库中的构建脚本不等于已经完成原生验证的安装包。

## 版本和架构

- `SocialAutoUpload-macOS-Universal.pkg`：一个安装包同时携带 `x86_64` 和
  `arm64` 两套原生应用及 Chromium 载荷，启动时按当前 Mac 架构选择对应载荷。
  这是“双原生载荷安装包”，不是把一套二进制改名成通用二进制。
- `SocialAutoUpload-Windows-x64-Setup.exe`：支持 Windows 10/11 x64，按当前用户
  安装，不需要写入机器级目录。
- 两个平台都是“GUI + CLI”组合：GUI 是日常入口，CLI 保留安装前已有的完整平台
  命令。首版 GUI 覆盖抖音、快手、视频号和小红书；其他已接入平台继续使用 CLI。
- 当前交付是未签名版本。Apple notarization 和 Windows Authenticode 签名需要单独的
  发布凭据，不能从“能安装”推断为“已经过 Apple/Microsoft 签名审核”。

## 安装前先校验文件

只使用可信来源提供的安装包、`SHA256SUMS` 和 `release-manifest.json`。如果校验值
不一致，停止安装并重新获取交付文件，不要通过关闭系统安全功能继续运行。

macOS，在交付目录执行：

```bash
shasum -a 256 -c SHA256SUMS
```

Windows PowerShell，查看 Windows 安装包的实际 SHA-256，再与 `SHA256SUMS` 中同名
记录逐字比较：

```powershell
Get-FileHash -Algorithm SHA256 .\SocialAutoUpload-Windows-x64-Setup.exe
Get-Content .\SHA256SUMS
```

## macOS 安装与启动

1. 双击 `SocialAutoUpload-macOS-Universal.pkg`，按安装器提示完成安装。
2. 应用安装到 `/Applications/Social Auto Upload.app`。
3. 在 Finder 的“应用程序”中打开 `Social Auto Upload`，进入 GUI。

因为首版未签名、未 notarize，Gatekeeper 可能阻止安装或首次启动。仅在安装包来源
可信且 SHA-256 已匹配时，先尝试打开一次，然后进入“系统设置 → 隐私与安全性”，
在“安全性”区域为这个安装包或应用选择“仍要打开”，再按系统提示确认。Apple 说明
该按钮通常只在尝试打开后约一小时内出现；受管理的 Mac 可能不允许用户放行。不要
全局关闭 Gatekeeper，也不要批量移除系统隔离属性。参见
[Apple 官方单应用放行说明](https://support.apple.com/guide/mac-help/open-an-app-by-overriding-security-settings-mh40617/mac)。

安装器会在条件允许且不覆盖现有同名文件时创建 `/usr/local/bin/sau`。先验证：

```bash
sau --help
```

如果终端提示找不到命令，直接使用应用内的 CLI；它与 GUI 使用相同的数据和治理逻辑：

```bash
"/Applications/Social Auto Upload.app/Contents/MacOS/sau" --help
"/Applications/Social Auto Upload.app/Contents/MacOS/sau" safety status \
  --platform douyin --account creator
```

## Windows 安装与启动

1. 双击 `SocialAutoUpload-Windows-x64-Setup.exe`。
2. 安装范围为当前用户，默认目录是
   `%LOCALAPPDATA%\Programs\SocialAutoUpload`。
3. 安装器创建开始菜单 GUI 和 CLI 快捷方式；桌面 GUI 快捷方式由安装选项控制。
4. “把 CLI 加入当前用户 PATH”默认不勾选。需要直接在新终端运行 `sau.exe` 时才
   勾选，不会修改机器级 PATH。

未签名安装包可能显示“Windows 已保护你的电脑”。确认来源和 SHA-256 后，可以在
SmartScreen 提示中选择“更多信息”，核对文件名，再选择“仍要运行”。不要关闭整个
SmartScreen。Windows 11 的 Smart App Control 或组织策略可能完全禁止未签名应用且
不显示继续选项；这种情况应使用签名版本或联系设备管理员。参见
[Microsoft SmartScreen reputation 说明](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
和 [Windows 应用与浏览器控制说明](https://support.microsoft.com/windows/security/windows-security-app-browser-control-in-the-windows-security-app)。

从开始菜单打开 `Social Auto Upload` 使用 GUI。CLI 可以从“Social Auto Upload
Command Line”快捷方式启动，也可以在 PowerShell 中直接执行：

```powershell
& "$env:LOCALAPPDATA\Programs\SocialAutoUpload\sau.exe" --help
& "$env:LOCALAPPDATA\Programs\SocialAutoUpload\sau.exe" safety status `
  --platform douyin --account creator
```

如果安装时选择了用户 PATH，关闭并重新打开终端后也可以执行：

```powershell
sau.exe --help
```

## GUI 基本使用

1. 打开“账号管理”，选择平台和本机账号名称，启动登录。
2. 在弹出的可见 Chromium 中完成平台官方登录、扫码或人工验证。每台电脑分别保存
   自己的账号状态，安装器不会复制或同步 cookie/profile。
3. 打开“发布中心”，选择平台、账号、素材并填写标题、正文、标签和平台必填声明。
4. 提交后查看任务状态。“已排队”只表示进入本机队列，不表示已经发布成功。
5. 任务显示“等待你的发布确认”时，检查浏览器中的账号、素材、标题和声明，再点击
   “确认当前页面内容并继续发布”。自动点击最终发布默认关闭。
6. 只有执行核心明确返回“发布成功”才视为成功。如果 GUI 显示无法确认结果，不要
   立即重复提交，先检查平台页面和本地安全状态。

> 同一账号不要在多台电脑同时发布。本机发布锁不能协调其他电脑。

GUI 和 CLI 经过同一个发布服务，不能绕过并发锁、冷却、重复内容拦截、风险熔断、
审计和失败证据。抖音和小红书默认使用可见浏览器并保留最终人工确认；只有用户明确
选择自动发布时才跳过本次确认，其他强制治理仍然生效。打包没有新增每日发布上限、
强制发布时段或跨电脑分布式锁。

## CLI 使用

安装包中的 CLI 与 GUI 共用版本、配置、账号目录、浏览器和治理代码。例如：

```bash
sau douyin login --account creator
sau douyin check --account creator
sau douyin upload-video --account creator --file /absolute/path/demo.mp4 \
  --title "示例标题" --desc "示例简介" --declaration none
sau safety status --platform douyin --account creator --json
```

Windows 未加入 PATH 时，把示例开头的 `sau` 替换为
`& "$env:LOCALAPPDATA\Programs\SocialAutoUpload\sau.exe"`；macOS 找不到 wrapper 时，
替换为 `"/Applications/Social Auto Upload.app/Contents/MacOS/sau"`。完整平台和参数见
[CLI 使用说明](./CLI.md)。

## 验证离线浏览器载荷

这项检查验证“浏览器已随安装包提供”，不验证平台网页能离线使用：

1. 完成安装并确认安装包 SHA-256 后，暂时断开网络。
2. 启动 GUI；界面应直接打开，不应出现安装 Python、Node.js 或下载 Chromium 的步骤。
3. 在“账号管理”启动一个测试登录。独立 Chromium 窗口应能直接启动；平台页面因
   断网加载失败是预期现象。
4. 退出测试登录和 GUI，恢复网络，再进行真实登录。

如果应用报告 `browser-manifest.json`、浏览器文件缺失或校验失败，重新安装同一份已
校验的安装包。桌面版不要运行 `patchright install chromium` 来掩盖载荷损坏；它应
始终使用安装包内经过清单校验的浏览器。

## 安装位置与数据位置

| 系统 | 应用位置 | 当前用户数据位置 |
| --- | --- | --- |
| macOS | `/Applications/Social Auto Upload.app` | `~/Library/Application Support/SocialAutoUpload` |
| Windows | `%LOCALAPPDATA%\Programs\SocialAutoUpload` | `%LOCALAPPDATA%\SocialAutoUpload` |

当前用户数据包括 cookie、浏览器 profile、日志、发布治理状态、失败证据、工作素材和
数据库。它不在应用安装目录内；卸载应用默认保留这些数据，升级或重装可以继续使用。

## 卸载与可选数据清理

macOS：

1. 退出应用，把 `/Applications/Social Auto Upload.app` 移到废纸篓。
2. 如果 `/usr/local/bin/sau` 确认是本安装包创建的 CLI wrapper，再删除它；不要删除
   其他软件创建的同名命令。

可以先核对 wrapper 的固定执行行；只有第一条命令完整匹配时才执行第二条：

```bash
grep -Fx 'exec "/Applications/Social Auto Upload.app/Contents/MacOS/sau" "$@"' /usr/local/bin/sau
sudo rm /usr/local/bin/sau
```

Windows：进入“设置 → 应用 → 已安装的应用”，找到 `Social Auto Upload` 并选择
“卸载”。安装器只移除当前用户的程序、快捷方式和由它拥有的用户 PATH 项，不会删除
`%LOCALAPPDATA%\SocialAutoUpload`。

只有确定不再需要本机登录状态、发布治理状态、日志和工作素材时，才单独执行以下
数据清理。该操作不可撤销，而且卸载器永远不会自动执行它。

macOS：

```bash
rm -rf "$HOME/Library/Application Support/SocialAutoUpload"
```

Windows PowerShell：

```powershell
Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'SocialAutoUpload') -Recurse -Force
```

## 风险边界

封装安装包降低的是安装和迁移门槛，不是平台风控概率。它不保证避开平台的人机识别、
内容审核或账号限制，也不提供 CAPTCHA 求解、浏览器指纹伪装、代理轮换、挑战页绕过
或其他反检测机制。平台页面、规则和账号状态变化仍可能导致登录失败、发布中止或账号
受限。可控部分来自可见浏览器、人工确认、失败即停、并发锁、冷却、去重、审计和谨慎
的操作频率，而不是安装包格式。

源码开发或自定义部署请改看[源码安装说明](./install.md)。

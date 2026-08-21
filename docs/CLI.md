# CLI 使用说明

项目现在提供一个统一的 CLI 入口 `sau`，当前主线已经接入：

- `douyin`
- `kuaishou`
- `xiaohongshu`
- `bilibili`
- `tencent`
- `baijiahao`
- `alipay`
- `weibo`
- `hupu`
- `youtube`

实现说明：

- `sau_cli.py` 是当前 CLI 的主入口和唯一主要实现文件
- 源码模式的 `sau.exe` 是 Windows 虚拟环境生成的入口；离线桌面版则携带独立冻结的
  `sau.exe`，不依赖虚拟环境
- 离线桌面安装包保留同一套完整 CLI；GUI 目前覆盖抖音、快手、视频号和小红书，其他
  已接入平台继续通过 CLI 使用
- 安装包模式下，CLI 与 GUI 共用版本、账号数据、内置 Chromium 和发布治理，不需要
  Codex、源码目录、系统 Python、Node.js 或另行执行 `patchright install chromium`
- 如果需要给 OpenClaw、Codex 等 agent 使用，可参考仓库内 skill：
  - `skills/douyin-upload/`
  - `skills/kuaishou-upload/`
  - `skills/xiaohongshu-upload/`
  - `skills/bilibili-upload/`

视频号、百家号和支付宝生活号目前只有 CLI 入口，暂未提供对应的 skill。

## 安装 CLI 入口

### 离线桌面安装包

macOS 安装位置是 `/Applications/Social Auto Upload.app`。安装器在条件允许且不覆盖
现有命令时创建 `/usr/local/bin/sau`；如果 `sau --help` 找不到命令，直接执行：

```bash
"/Applications/Social Auto Upload.app/Contents/MacOS/sau" --help
```

同一个 macOS `.pkg` 携带 `x86_64` 和 `arm64` 两套原生 CLI/浏览器载荷，并按当前
Mac 架构选择，不依赖 Rosetta 冒充另一架构。

Windows x64 默认按当前用户安装到
`%LOCALAPPDATA%\Programs\SocialAutoUpload`。用户 PATH 选项默认不勾选；未加入 PATH
时，从开始菜单的 `Social Auto Upload Command Line` 快捷方式启动，或执行：

```powershell
& "$env:LOCALAPPDATA\Programs\SocialAutoUpload\sau.exe" --help
```

安装、未签名系统提示、离线浏览器验证和卸载见
[离线桌面版安装说明](./desktop-install.md)。

### 源码安装

如果你希望直接使用 `sau` 命令，而不是手动执行 `python sau_cli.py`，先在项目根目录安装一次：

```bash
uv pip install -e .
```

安装后就可以直接使用：

```bash
sau douyin --help
sau kuaishou --help
sau xiaohongshu --help
sau bilibili --help
sau tencent --help
sau baijiahao --help
sau alipay --help
sau weibo --help
sau hupu --help
sau youtube --help
```

## 安装 patchright 浏览器

以下步骤只用于源码安装。离线桌面安装包已经携带经过清单校验的 Chromium；桌面版
不要另行下载浏览器来覆盖或掩盖缺失/损坏的安装载荷。

Windows 下推荐先指定镜像，再安装 Chromium：

```powershell
$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"; patchright install chromium
```

## 安全状态命令

安全状态命令只读取本地文件，不启动浏览器、不访问平台，也不会创建或修改账号目录：

```bash
sau safety status --platform douyin --account <account_name>
sau safety status --platform xiaohongshu --account <account_name>
sau safety status --platform douyin --account <account_name> --json
```

命令显示上次成功时间、按 `--min-publish-interval` 估算的剩余冷却、7 日去重记录数、发布锁、审计日志大小和最近一次失败证据。状态文件损坏时命令会明确报告并返回非零退出码，不会把未知状态当成安全状态。

## 抖音 CLI 子命令

```bash
sau douyin login --account <account_name>
sau douyin login --account <account_name> --headless
sau douyin check --account <account_name>
sau douyin upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 运动,训练 --declaration none
sau douyin upload-note --account <account_name> --images videos/1.png videos/2.png --title "图文标题" --note "图文示例" --tags 图文,测试
```

抖音和小红书上传默认启用安全发布模式：

- 浏览器可见，最终点击发布前需要在终端输入 `PUBLISH`
- 同一平台只允许一个发布任务运行
- 同一账号发布成功后默认冷却 30 分钟
- 7 天内相同标题、正文和素材会被拦截
- 检测到验证码、滑块、登录/验证跳转、账号异常、操作频繁、上传失败或系统繁忙时立即停止，不自动处理、不重复点击
- 发布页导航返回 HTTP 4xx/5xx 时立即熔断；视频上传硬截止 15 分钟，图文上传硬截止 10 分钟
- 审计记录和状态保存在账号文件目录下的 `.sau_safety/`；成功 URL 回执保存在 `.sau_safety/receipts/`
- 审计日志达到 5 MiB 前自动轮转并保留 5 份；失败证据写入账号隔离的 `.sau_safety/evidence/` 子目录
- 失败证据只含任务 ID、平台、账号、操作阶段、异常类型、脱敏原因和去掉查询参数/片段的页面 URL，不含页面正文、cookie、请求头或截图
- 浏览器 profile/安全状态目录权限为 `0700`，cookie、审计、状态和回执文件权限为 `0600`

可用 `--min-publish-interval MINUTES` 调整业务冷却时间。无人值守任务必须显式传入
`--automatic-publish --headless`；这只关闭人工确认，不会关闭并发锁、重复内容拦截、风险熔断和审计。

Phase 3C 治理决策保持现有使用方式：不增加每日数量上限，不强制 headed/人工确认，
不禁用 `--automatic-publish`，也不延长默认冷却或限制发布时间段。这四项只有在用户明确
重新批准后才能改变；并发锁、去重、风险熔断、审计和成功回执仍然强制生效。

视频必须显式传 `--declaration`。确实无需声明时传 `--declaration none`；需要声明时传发布页显示的准确声明文本。程序不再自动选择“内容由AI生成”。

## 快手 CLI 子命令

```bash
sau kuaishou login --account <account_name>
sau kuaishou check --account <account_name>
sau kuaishou upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 运动,训练
sau kuaishou upload-note --account <account_name> --images videos/1.png videos/2.png videos/3.png --title "图文标题" --note "图文示例" --tags 图文,测试
```

## 小红书 CLI 子命令

```bash
sau xiaohongshu login --account <account_name>
sau xiaohongshu check --account <account_name>
sau xiaohongshu upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 小红书,视频 --content-source original
sau xiaohongshu upload-note --account <account_name> --images videos/1.png videos/2.png videos/3.png --title "图文标题" --note "图文示例" --tags 图文,测试 --content-source original
```

`--content-source` 为必选项。原创内容传 `original`；转载内容传 `repost`，并同时填写 `--repost-source "媒体名称"`。缺少选择或转载来源时会在浏览器启动前停止。

抖音和小红书会按账号复用 `cookies/.browser_profiles/` 下的独立持久化浏览器 profile。旧 cookie JSON 仅首次导入，后续登录状态由该 profile 延续；上传流程不再在 CLI 和 uploader 两层重复校验 cookie。每次发布任务都有唯一 `task_id`，平台成功 URL 已确认但 URL 中无法提取作品 ID 时，回执会标记 `manual_reconciliation_required`，不会为了补 ID 额外请求平台。

海外环境如果无法登录默认创作者后台，可以通过环境变量切换到 RedNote 域名。该设置同时作用于登录、cookie 校验、视频发布和图文发布：

```bash
SAU_XHS_CREATOR_BASE_URL=https://creator.rednote.com sau xiaohongshu login --account <account_name>
```

## Bilibili CLI 子命令

```bash
sau bilibili login --account <account_name>
sau bilibili check --account <account_name>
sau bilibili upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tid 249 --tags 足球,测试 --thumbnail covers/demo.png
```

补充说明：

- `creator` 之类的名字只是示例值，真正传的是用户自定义的 `account_name`
- 一个 `account_name` 对应一个账号文件，可以准备多个账号并发使用
- 浏览器平台统一元数据约定：
- 视频使用 `title + desc + tags`
- 图文使用 `title + note + tags`
- `sau bilibili ...` 会自动准备 `biliup`
- 如果本地没有 `biliup`，第一次运行会自动下载
- 如果上游 GitHub Release 有更新，运行时会先自动更新
- `sau bilibili login --account <name>` 建议由用户自己在本地真实终端里执行；如果终端里的二维码显示不完整，可直接打开当前目录下的 `qrcode.png` 扫码

## 视频号 CLI 子命令

```bash
sau tencent login --account <account_name>
sau tencent check --account <account_name>
sau tencent upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 视频号,测试
```

视频号支持定时发布、草稿、合集和双比例封面：

```bash
sau tencent upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --schedule "2026-03-24 21:30" --thumbnail-landscape covers/landscape.png --thumbnail-portrait covers/portrait.png --collection "我的合集"
sau tencent upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --draft
```

视频号登录和上传依赖浏览器中的登录态。无头模式下如果需要扫码，CLI 会生成临时二维码；需要人工查看页面时可以加 `--headed`。

## 百家号 CLI 子命令

```bash
sau baijiahao login --account <account_name>
sau baijiahao check --account <account_name>
sau baijiahao upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 百家号,测试
```

百家号当前支持登录、账号检查和视频上传；支持 `--thumbnail` 与 `--collection`，暂不支持 `--schedule`。上传前需要先完成百度账号登录并保存账号文件。

## 支付宝生活号 CLI 子命令

```bash
sau alipay login --account <account_name>
sau alipay check --account <account_name>
sau alipay upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 生活号,测试
```

支付宝生活号当前支持登录、账号检查和视频上传；支持 `--thumbnail` 与 `--collection`，暂不支持图文上传和 `--schedule`。首次使用前需要在支付宝内容创作后台完成登录，并确认账号已开通生活号内容创作权限。

## YouTube CLI 子命令

```bash
sau youtube login --account <account_name>
sau youtube check --account <account_name>
sau youtube upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags tag1,tag2 --playlist "我的系列" --visibility public
```

YouTube 登录需要在浏览器中完成 Google 账号登录，不使用二维码。`--visibility` 可选 `public`、`unlisted` 或 `private`，`--playlist` 可选。

## 微博 CLI 子命令

```bash
sau weibo login --account <account_name>
sau weibo check --account <account_name>
sau weibo upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 微博,测试 --thumbnail covers/demo.png
```

微博当前支持登录、账号检查和视频上传；标题最多 30 个字，封面图建议小于 5 MB，暂不支持图文上传和 `--schedule`。

## 虎扑 CLI 子命令

```bash
sau hupu login --account <account_name>
sau hupu check --account <account_name>
sau hupu upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tags 虎扑,测试 --thumbnail covers/demo.png
```

虎扑当前支持登录、账号检查和视频上传；标题长度要求为 4–40 个字，暂不支持图文上传和 `--schedule`。虎扑登录可能需要在浏览器中完成 QQ 或手机号登录，需要人工查看页面时可以加 `--headed`。

## 登录二维码说明

- 抖音、快手、小红书、视频号、百家号、支付宝生活号、微博和虎扑登录过程中，CLI / uploader 可能会生成临时二维码图片
- 对普通用户来说，可以直接打开该图片扫码
- 对可操作本地文件的 agent 来说，不要只把图片路径告诉用户
- 这类二维码图片本身就是给用户扫码的，agent 应优先直接展示/发送本地图片给用户
- Bilibili 和 YouTube 当前不走这套本地二维码图片托管链路，登录按上面的平台说明处理即可

## 定时发布

抖音、快手、小红书、视频号的图文或视频上传，以及 Bilibili 的视频上传支持 `--schedule`。只要传了 `--schedule`，CLI 就会自动切换到对应平台的定时发布策略；不传则默认立即发布。百家号、支付宝生活号、微博和虎扑当前不支持 `--schedule`。

```bash
sau douyin upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --declaration none --schedule "2026-03-24 21:30"
sau douyin upload-note --account <account_name> --images videos/1.png videos/2.png --title "图文标题" --note "图文示例" --schedule "2026-03-24 21:30"
sau kuaishou upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --schedule "2026-03-24 21:30"
sau kuaishou upload-note --account <account_name> --images videos/1.png videos/2.png videos/3.png --title "图文标题" --note "图文示例" --schedule "2026-03-24 21:30"
sau xiaohongshu upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --content-source original --schedule "2026-03-24 21:30"
sau xiaohongshu upload-note --account <account_name> --images videos/1.png videos/2.png videos/3.png --title "图文标题" --note "图文示例" --content-source original --schedule "2026-03-24 21:30"
sau bilibili upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tid 249 --schedule "2026-03-24 21:30"
sau tencent upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --schedule "2026-03-24 21:30"
```

## 运行时参数

CLI 将 `debug` 和 `headless` 拆成了两个独立维度：

```bash
--debug
--headless
--headed
```

- `--debug`: 打开调试行为，例如失败时保留更多调试信息
- `--headless`: 无头模式运行
- `--headed`: 有头模式运行

如果都不传，普通平台仍按 `headless=True` 运行；抖音和小红书上传默认按 `headed` 运行。

补充：

- 抖音和小红书上传默认显示浏览器并要求最终人工确认
- 快手等其他现有 CLI 仍保持原来的无头默认值

## 视频上传参数

```bash
--file videos/demo.mp4
--title "示例标题"
--desc "示例简介"
--tags 运动,训练
--thumbnail videos/demo.png
--thumbnail-landscape videos/cover-4x3.png
--thumbnail-portrait videos/cover-3x4.png
```

抖音和视频号支持同时设置两种比例的封面图：

- `--thumbnail-landscape`: 4:3 横版封面
- `--thumbnail-portrait`: 3:4 竖版封面
- `--thumbnail`: 兼容旧参数，等同于 3:4 竖版封面

视频号、百家号和支付宝生活号支持使用 `--collection` 指定已有合集；百家号和支付宝生活号还支持 `--thumbnail` 指定封面图。

抖音额外支持：

```bash
--product-link https://example.com/item
--product-title 示例商品
```

Bilibili 额外要求：

```bash
--tid 249
```

- `--tid` 第一版是必填
- `--tags` 会映射到 `biliup upload --tag`
- `--schedule` 会映射到 Bilibili 所需的时间戳参数

## 图文上传参数

```bash
--images videos/1.png videos/2.png videos/3.png
--title "图文标题"
--note "图文内容"
--tags 图文,测试
```

图文上传当前限制：

- 抖音：最多 35 张图片，不支持 GIF
- 快手：支持多张图片，建议传真实不同文件，不要把同一路径重复多次
- 小红书：支持多张图片，正文 `--note` 可选，但 `--title` 建议始终显式传入

后续维护 CLI 时，优先看 `sau_cli.py`、`uploader/` 和 `skills/`。

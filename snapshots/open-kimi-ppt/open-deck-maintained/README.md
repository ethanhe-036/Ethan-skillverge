# Open Deck Skill

[简体中文](README.md) | [English](README_EN.md)

[![version](https://img.shields.io/badge/release-1.4.1-6657E8)](package.json)
[![node](https://img.shields.io/badge/node-%3E%3D18-brightgreen)](https://nodejs.org)

逆向 Kimi Slides 实现的非官方演示文稿 Skill，让 AI Coding Agent 可以创建或编辑 PPTD、把图片/PDF 等视觉参考复刻为 PPTD、只读检查现有 PPTX、保守填充已有文本/备注，并把受支持的 PPTD 子集离线导出为可编辑 PPTX。创作工作流默认交付完整 PPTD 项目、严格 source-bound 质量报告和对应 PPTX：优先使用无浏览器、无网络的本地 DrawingML 导出器；只有本地后端无法保真且用户授权时，才把公开 Kimi writer 作为 fallback。PPTD 支持页内元素动画和[预设主题](theme.md)，并附带本地浏览器编辑器。Codex 通过 `$open-kimi-ppt` 显式调用；Claude Code、Cursor、WorkBuddy 等宿主只有在能发现并遵循 `SKILL.md`、且具备相应文件/浏览器工具时才可使用，具体工具能力与交付完成度由宿主决定。

> [!IMPORTANT]
> 本项目通过逆向分析 Kimi Slides Skill、PPTD 格式以及公开网页编辑器的前端行为与通信协议实现，并非 Kimi 或 Moonshot AI 的官方项目，也未获得其认可或支持。项目依赖的公开前端资源和兼容协议可能随 Kimi 更新而失效，仅供学习与研究使用。

> [!NOTE]
> 通用原生编辑仍从完整 PPTD 项目开始。本包不包含 PPTX→PPTD 导入器；PPTX sidecar 只支持结构检查，以及替换已有普通文本框/已有备注正文，并明确拒绝对象、切换和任意 OOXML 编辑。超出该范围时需要源 PPTD、明确可用的外部转换能力，或把可查看页面当作视觉参考重新创作；复刻不是格式转换。

## 当前实现与验收状态

| README 能力 | 当前实现证据 | 边界 |
| --- | --- | --- |
| Codex 发现与呈现 | `SKILL.md`、`agents/openai.yaml`、Skill 图标和产品契约测试；使用 `$open-kimi-ppt` 显式调用 | Codex 隐式调用已关闭，以避免和内置演示文稿能力冲突；其他 Agent 取决于其 Skill/工具支持 |
| 创建、编辑、复刻 PPTD | 完整 PPTD 规范、30 套 preset、canonical registry、仓库示例和默认双交付工作流 | 没有 PPTX→PPTD importer；视觉复刻不是转换 |
| 严格质量门禁与溯源 | `pptd_quality.py` 对 manifest、页面、`deck.meta.json`、`media/sources.json` 和本地媒体做 SHA-256 绑定；warning 即阻断导出 | 报告在任何源文件变化后都会失效，不能代替视觉检查或法律意见 |
| PPTX 检查与保守填充 | `pptx_native.py` 清点/校验 OPC，并仅替换已有文本 shape 与已有备注正文；候选包校验后原子发布 | 不创建对象或备注部件，不编辑表格、图表、图片、动画、切换或任意 OOXML |
| 离线 PPTX 导出 | `export_pptx_local.py` 为受支持的文本、基础 shape、直线、本地 PNG/JPEG、备注和安全切换生成完整 OPC/DrawingML | 不支持的表格、图表、icon、动画、远程资源、自定义路径等 fail closed；不宣称与浏览器 writer 功能等价 |
| P3 可选资产准入 | `asset_pack_audit.py` 离线校验来源、逐文件 SHA-256、NOTICE、归因、派生链、再分发依据和品牌授权证据 | 核心包目前不含约 12,000 个图标、约 190 个声音或整套品牌资产；候选资产只能在 P0–P2 完成后作为 opt-in pack 独立准入，技术审计不等于法律意见 |
| 远程 writer fallback、字体请求与复杂 PPTD | 公开 writer 路径、OOXML/ZIP 校验和 3 份真实示例产物；PowerPoint round-trip 的 8/8 页保留 shape、动画与 fade | 当前公开 Kimi writer 是外部依赖；本轮真实签名仍返回 HTTP 401。PowerPoint 另存会移除该样例字体部件，因此结果只报告实际包内计数，不承诺 Office 再保存后保留 |
| 页内元素动画 | 动画示例的 8 个 `.page` 均含 `animations`，对应示例 PPTX 和 PowerPoint 另存副本的 8 张 slide 均含 `p:timing` | PowerPoint round-trip 已通过；其他 Office/WPS 版本的播放一致性仍取决于宿主 |
| 图片质检 | `export_images.py` 生成逐页图片、页码映射和总览；无登录隔离 profile 的真实一页导出已通过；支持图像输入的 Agent 按 Skill 检查、修复并复检 | 脚本本身不是审美模型；无图像输入时只做结构检查并披露跳过 |
| 本地编辑器与手动导出 | 隔离 Chrome 已完成真实系统目录选择、公开 iframe 编辑、磁盘自动保存、SHA 与 journal 复核；保存/journal 对抗性回归同时通过 | Web File System Access API 对外部进程仍没有原子 CAS；不要同时用其他程序改同一组文件 |
| 安装与公开获取 | `npx open-deck-skill@latest install` 是 1.4.1 的公开安装主路径；CLI、安装事务和 npm tarball 均有测试 | 安装前可用 `npm view open-deck-skill version` 核对注册表；源码安装仍是可审计的备选。旧包已撤下，但 Skill ID 仍是 `$open-kimi-ppt` |

完整未完成门禁与修复记录见 [urgent.md](urgent.md)。上表把公开分发、代码已实现、本地已验证和依赖外部系统的 writer canary 分开；公开安装可用不代表 Kimi writer 已通过，本轮真实签名仍返回 HTTP 401，因此不能宣称外部 PPTX 导出端到端门禁已经完成。

## 安装

需要 Node.js 18 或更高版本。默认装到共享目录 `~/.agents/skills/open-kimi-ppt`（Windows PowerShell 为 `$env:USERPROFILE\.agents\skills\open-kimi-ppt`，Command Prompt 为 `%USERPROFILE%\.agents\skills\open-kimi-ppt`），Codex 会从该共享目录发现 Skill。

> [!WARNING]
> 旧包 `open-kimi-ppt-skill` 已于 2026-08-07 从 npm 撤下，请勿继续使用。当前公开 npm 包与 CLI 名称均为全小写 `open-deck-skill`；先用 `npm view open-deck-skill version` 确认注册表返回 `1.4.1` 或更高版本，再运行下面的 `npx` 命令。如果注册表暂时不可用，仍可使用后面的源码安装备选。npm/CLI 名称已经改变，但 Codex Skill ID 与显式调用仍是 `$open-kimi-ppt`。

### 方式一：通过公共 npm 包安装（推荐）

```bash
# 确认公开版本
npm view open-deck-skill version

# 交互多选目录（空格选择、回车确认）
npx open-deck-skill@latest install

# 非交互：只装共享目录
npx open-deck-skill@latest install -y

# 装到全部已检测到的 Agent 目录（不存在的会跳过）
npx open-deck-skill@latest install --all
```

`--all` 与交互多选会检测以下目录：`~/.agents/skills`、`~/.codex/skills`、`~/.claude/skills`、`~/.cursor/skills`、`~/.workbuddy/skills`。

**WorkBuddy 用户**：WorkBuddy 发现不了共享目录，请在交互列表中选择 WorkBuddy，或显式指定其 Skill 目录：

macOS / Linux：

```bash
npx open-deck-skill@latest install --target ~/.workbuddy/skills
```

Windows PowerShell：

```powershell
npx open-deck-skill@latest install --target "$env:USERPROFILE\.workbuddy\skills"
```

Windows Command Prompt：

```bat
npx open-deck-skill@latest install --target "%USERPROFILE%\.workbuddy\skills"
```

### 方式二：从源码安装（备选）

需要审计源码或公共注册表暂时不可用时，在本仓库源码 checkout 根目录执行；Codex 等 Agent 也可以代为运行：

```bash
node bin/open-kimi-ppt-skill.js install -y
```

### Agent 发现不了 Skill 时

先用共享目录，不要默认对每个 Agent 各装一遍；确认某个 Agent 发现不了时，再为它单独指定目录（`--target` 可重复；Windows 路径应加引号，PowerShell 使用 `$env:USERPROFILE`，Command Prompt 使用 `%USERPROFILE%`）：

```bash
node bin/open-kimi-ppt-skill.js install --target ~/.codex/skills --target ~/.claude/skills
```

### 更新

通过 npm 安装时，重新执行对应的 `npx open-deck-skill@latest install ...` 命令即可更新；从源码安装时重新执行同一条 `node bin/open-kimi-ppt-skill.js install ...` 命令。当初用过 `--target` / `--all` 就带上相同参数。更新只替换 Skill 文件，不影响已生成的 PPTD / PPTX 项目。

传入多个 `--target` 时，安装器会先解析、去重并预检全部目标，全部通过后才开始替换；后续目标提交失败时，会按逆序恢复本次已更新且未被外部再次修改的目标。若外部修改导致无法安全回滚，命令会保留并报告恢复目录，不会静默覆盖。

包安装和本地编辑器支持 Node.js 18+；自动浏览器导出与图片质检当前只验证并默认接受 `agent-browser` 0.33.2，该版本另要求 Node.js 24+。更高版本会 fail closed；只有用户明确设置 `OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER=1` 才会带警告继续。自动导出还需要 Python 3（当前 CI 固定验证 3.12）、PyYAML 与 websocket-client；图片质检另需 Pillow。导出脚本不会静默执行全局 npm 或 pip 安装，缺少依赖时会在浏览器工作开始前给出明确的安装命令。当前公开 PPTX writer 的签名接口可能要求已登录的 Kimi 浏览器会话：取得 CDP endpoint 并成功安装 Blob 监控时，未登录会快速报出 401/403；只有无法取得 CDP endpoint 时才使用有界的文件下载兼容路径，该路径无法区分认证失败，会在当前尝试超时后明确提示检查专用登录会话。若 endpoint 已存在但 iframe 目标或 hook 无法唯一确认，导出会拒绝降级并直接失败。点击导出后，默认嵌字体尝试共用约 75 秒的产物等待/捕获期限；若没有产物，会关闭旧会话并在干净会话中禁用嵌字体重试（产物期限约 180 秒）。浏览器启动、页面/文稿载入与清理另有各自的有界超时。结果摘要会报告是否发生降级、是否观察到字体开关及实际字体部件数。`--force` 会保留并报告正式输出的 `previousOutputBackup`；若同时替换已有 `--keep-browser-raw` 文件，还会报告 `previousBrowserRawBackup`。确认新产物后须显式移动或删除这些备份，下一次强制导出才会继续。

## 使用

在 Codex 中请显式使用 `$open-kimi-ppt`。本 Skill 默认关闭隐式调用，避免与内置演示文稿能力发生路由重叠，也避免普通 PPT 请求在用户不知情时启用第三方 Kimi writer 或登录会话。

### 让 Agent 生成 PPT

安装完成后，直接向 Agent 描述需求即可。创作工作流会在最终源文件上生成 `--fail-on warning` 的 source-bound 质量报告，再优先离线导出对应 PPTX；本地后端遇到不支持语义会 fail closed，仅在用户授权后才尝试公开 writer。若没有任何后端能安全保留需求，Agent 必须交付完整 PPTD、保留错误证据并把 PPTX 标为未完成，不能伪造双交付成功。

为了更稳定的出品，Prompt 里最好带上风格（如「深色产品发布风」），提供完整 PPTD 模板，或附上可查看的 PPT/PPTX 页面作为视觉风格参考；只写主题、不给风格时效果更容易波动。PPT/PPTX 视觉参考不会被宣称为已转换的原生模板。

#### Prompt 示例

**示例 1：小米 YU7（约 8 页，图片作背景）**

```text
使用 $open-kimi-ppt 做一个介绍小米 yu7的 PPT,要求图片做背景,素材从网上找,8 页左右
```

| 在线编辑 PPTD | 导出 PPTX |
| :---: | :---: |
| [![小米 YU7 在线编辑](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-yu7-editor.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-yu7-editor.png) | [![小米 YU7 导出 PPTX](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-yu7-pptx.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-yu7-pptx.png) |

[![WorkBuddy 生成小米 YU7 PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-workbuddy-yu7.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-workbuddy-yu7.png)

**示例 2：DJI Pocket 4（图片作背景）**

```text
使用 $open-kimi-ppt 帮我生成DJI Pocket4 的 PPT,要求图片做背景,素材从网上找
```

| 在线编辑 PPTD | 导出 PPTX |
| :---: | :---: |
| [![DJI Pocket 4 在线编辑](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-dji-pocket4-editor.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-dji-pocket4-editor.png) | [![DJI Pocket 4 导出 PPTX](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-dji-pocket4-pptx.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-dji-pocket4-pptx.png) |

**示例 3：iPhone 17 Pro（约 8 页）**

```text
使用 $open-kimi-ppt 制作 iPhone 17 Pro 介绍 PPT
```

[![iPhone 17 Pro](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-iphone-17pro.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-iphone-17pro.png)

**示例 4：带页内元素动画（现场演示）**

```text
使用 $open-kimi-ppt 做一个介绍小米 yu7的 PPT,要求图片做背景,素材从网上找,8 页左右
要求带元素入场动画
```

成品示例见 [example/xiaomi-yu7-ppt-animation](https://github.com/FeidaWang/Open-Deck-Skill/tree/main/example/xiaomi-yu7-ppt-animation)（含 PPTD 项目与 PPTX；在当前源码根目录运行 `node bin/open-kimi-ppt-skill.js serve` 可打开预览动画）。

### 本地命令行工作流（P0–P2）

当前源码提供 7 个由 Node CLI 白名单路由到 Python 的 deck 命令。它们不会隐式安装依赖；P0–P2 的质量门、PPTX sidecar 和本地导出不启动浏览器、不调用 Kimi，也不发起网络请求。下面示例使用当前源码 checkout 中的 CLI；如果已安装 CLI 的 `--help` 已列出这些命令，可把 `node bin/open-kimi-ppt-skill.js` 换成 `open-deck-skill`。自动探测到的 Python 不合适时，使用 `OPEN_DECK_PYTHON` 指向明确的 Python 3 可执行文件。

| 命令 | 用途 |
| --- | --- |
| `lint` | 检查 PPTD，并生成绑定 manifest、页面、`deck.meta.json`、`media/sources.json` 和本地媒体字节的完整质量报告或紧凑 receipt |
| `export-local` | 只接受当前、完整且 `warning` 级别也通过的质量报告，把受支持 PPTD 子集离线导出为可编辑 PPTX |
| `inspect-pptx` / `validate-pptx` | 只读清点现有 PPTX，或校验 ZIP、OPC、XML、CRC 与 relationships |
| `fill-pptx` | 按 JSON plan 替换已有普通文本 shape / 已有备注正文，验证候选包并保持 slide timing 指纹 |
| `audit-assets` | 离线审计用户提供的可选 P3 asset pack；不会下载或自动引入资产 |
| `audit-prompts` | 检查 Skill prompt/context 的预算、引用完整性和重复内容 |

严格的本地导出顺序如下。质量报告必须在所有源文件定稿后生成；任何被绑定的文件发生变化，都必须重新运行 `lint`，旧报告不能复用或手工修改。

```bash
node bin/open-kimi-ppt-skill.js lint ./deck/deck.pptd \
  --fail-on warning \
  --output ./deck/quality-report.json

node bin/open-kimi-ppt-skill.js export-local ./deck/deck.pptd \
  --quality-report ./deck/quality-report.json \
  --output ./deck/deck.pptx

node bin/open-kimi-ppt-skill.js validate-pptx ./deck/deck.pptx --pretty
```

本地 DrawingML 后端支持纯色页面背景、普通文本、基础 preset shape、两点直线与部分箭头、本地 PNG/JPEG、普通备注，以及 `none` / `fade` / `push` / `wipe` 安全切换。表格、图表、icon、自定义路径、远程图片、GIF/SVG、元素动画、渐变和 morph 等语义会 fail closed，不会被静默删掉、栅格化或替换。

对已有 PPTX 做窄范围填充时，先 inspect，创建使用 1-based 页码的 plan，并始终写入不同的输出路径：

```json
{
  "replacements": [
    {"slide": 1, "shape_id": 2, "text": "新标题", "lang": "zh-CN"},
    {"slide": 2, "shape_name": "Subtitle 3", "text": "普通文本"}
  ],
  "notes": [
    {"slide": 1, "text": "更新后的演讲者备注", "lang": "zh-CN"}
  ]
}
```

```bash
node bin/open-kimi-ppt-skill.js inspect-pptx ./source.pptx --pretty
node bin/open-kimi-ppt-skill.js fill-pptx ./source.pptx ./fill-plan.json \
  --output ./filled.pptx --pretty
node bin/open-kimi-ppt-skill.js validate-pptx ./filled.pptx --pretty
```

每个 replacement 必须用 `shape_id` 或 `shape_name` 二选一定位。该 sidecar 不创建新对象或备注部件，也不编辑表格、图表、图片、动画、切换或任意 OOXML；它不是 PPTX→PPTD importer，更不是通用 PowerPoint 编辑器。`deck.meta.json` 约定见 [项目元数据说明](skills/open-kimi-ppt/reference/deck-metadata.md)，P3 独立资产包准入规则与审计命令见 [可选资产包说明](skills/open-kimi-ppt/reference/asset-packs.md)。

### 在线编辑与手动导出

建议直接让 AI 启动本地编辑器，例如说：

```text
帮我在当前源码根目录执行 node bin/open-kimi-ppt-skill.js serve
```

也可以自己在终端运行：

```bash
node bin/open-kimi-ppt-skill.js serve
```

然后打开 <http://127.0.0.1:55173/>，选择包含 `.pptd` 清单、`pages/` 和 `media/` 的完整项目文件夹，即可在浏览器中查看、编辑项目并导出 PPTX。仓库自带的 [example/dji-pocket4](https://github.com/FeidaWang/Open-Deck-Skill/tree/main/example/dji-pocket4) 是一个完整的 18 页示例项目，可直接打开体验。

为在读取任何页面前建立最小文件能力，本地编辑器对 **`.pptd` 清单** 使用受限 YAML 子集：必须只有一个顶层 `pages` 字符串路径数组，并拒绝 block scalar、anchor/alias、tag、merge key、复杂键和重复键。各 `.page` 页面内容仍按完整 PPTD 页面格式交给 writer 处理；若现有清单用了上述高级 YAML 语法，请先等价改写为简单键和值再打开。

```bash
# 启动后自动打开浏览器
node bin/open-kimi-ppt-skill.js serve --open

# 使用其他端口
node bin/open-kimi-ppt-skill.js serve --port 56000
```

这些命令使用仓库内的 CLI；仅安装到 `~/.agents/skills` 的 Skill 文件夹并不包含顶层 CLI。公共包用户可改用 `npx open-deck-skill@latest serve`；也可在已审核的源码目录执行 `npm install --global .` 后使用 `open-deck-skill serve`。

可写目录需要使用支持 File System Access API 的 Chromium 系浏览器；其他浏览器会回退为只读文件夹上传。按 `Ctrl+C` 停止服务。

### Windows：常驻调试浏览器说明

在 Windows 上导出 PPTX 或页面质检图片时，脚本会自动启动一个**常驻的调试浏览器**。这是有意设计，不是异常进程：

- **为什么需要**：agent-browser 在 Windows 下无法自行启动 Chrome（Chrome 启动器把进程交接给子进程后立即退出，被误判为崩溃），导出只能改为驱动一个外部启动的浏览器。
- **它是什么**：优先使用本机 Chrome，未安装时回退到 Edge；使用独立配置目录 `%TEMP%\okp-cdp-profile`，由 Chrome 选择空闲调试端口，窗口定位在屏幕外。脚本只会复用该专用目录中 ownership marker 与 `DevToolsActivePort` 共同验证的实例，绝不会因为常用端口（如 `9337`）恰好存活就接管未知浏览器。
- **为什么常驻**：导出完成后实例保持运行，下次导出通常会复用同一个实例，而不是每导一次就多一个浏览器进程。这里没有跨进程 singleton 锁；不要让两个导出进程并发使用同一专用 profile。若想关掉，直接结束该浏览器进程即可，下次导出会自动重新拉起。
- **想完全自控**：自行以 `--remote-debugging-port=<端口>` 启动浏览器，并把环境变量 `AGENT_BROWSER_CDP` 设为该端口，脚本会优先使用你自己的实例。

macOS / Linux 无此行为，不受影响。

### PPTX 导出登录会话

当前公开 writer 可能需要 Kimi 登录会话才能取得 PPTX 签名。建议为导出单独准备一个浏览器配置目录，不要让脚本自动读取日常 Chrome 配置：

```bash
export AGENT_BROWSER_PROFILE="$HOME/.open-kimi-ppt-browser"
agent-browser --profile "$AGENT_BROWSER_PROFILE" open https://www.kimi.com
```

Windows 不能可靠使用上述 agent-browser 启动方式预登录；请显式启动专用调试浏览器。PowerShell 示例（按实际安装位置替换 Chrome，可改用 Edge）：

```powershell
$env:AGENT_BROWSER_PROFILE = "$env:USERPROFILE\.open-kimi-ppt-browser"
$env:AGENT_BROWSER_CDP = "9223"
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --user-data-dir="$env:AGENT_BROWSER_PROFILE" --remote-debugging-port=$env:AGENT_BROWSER_CDP https://www.kimi.com
```

Windows Command Prompt：

```bat
set "AGENT_BROWSER_PROFILE=%USERPROFILE%\.open-kimi-ppt-browser"
set "AGENT_BROWSER_CDP=9223"
"%ProgramFiles%\Google\Chrome\Application\chrome.exe" --user-data-dir="%AGENT_BROWSER_PROFILE%" --remote-debugging-port=%AGENT_BROWSER_CDP% https://www.kimi.com
```

在打开的浏览器中手动登录；macOS/Linux 可随后关闭浏览器并保留 profile 变量，Windows 则保持该调试浏览器运行并同时保留 `AGENT_BROWSER_CDP` 再执行 `export_pptx.py`。Windows profile 必须是专用配置目录的绝对路径；脚本会拒绝 `Default`、`Profile N` 和日常 `User Data` 根目录。配置目录和 state 文件都包含登录凭据，应限制文件权限、不要提交到仓库，也不要在日志中输出。

## 功能特性

- PPTD 生成：让 Agent 生成完整、可继续编辑的 PPTD 项目，支持从零创作、风格迁移、PPTD 模板复用、图片/PDF 复刻。
- 质量与溯源：导出前以 warning 为阻断阈值，绑定 `deck.meta.json`、`media/sources.json`、页面和本地媒体哈希；源文件变化后旧报告自动失效。
- PPTX sidecar：只读检查/校验现有 PPTX，并可保守替换已有普通文本和已有备注正文；不是 PPTX→PPTD importer，也不是通用 PowerPoint 编辑器。
- 预设主题：内置 30 套随 Skill 提供的 design system preset，点名即可套用；完整列表与预览图见 [theme.md](theme.md)。
- 元素动画：默认不加。提示词加上「要求带元素入场动画」即可，由 AI 按页编排合适的入场效果。
- PPTX 生成：受支持的 PPTD 子集默认走离线 DrawingML 后端；高级语义只在本地 fail closed 且用户授权后使用公开 writer fallback。远程 writer 会请求嵌入字体，结果只报告实际字体部件数。
- 视觉质检：当当前 Agent 支持图像输入时，Skill 工作流要求它在导出 PPTX 前运行 `export_images.py` 导出整份页面图片并拼接总览图，再由多模态 Agent 逐项核查（变形、遮挡、出界、对比度、排版、文字溢出）、修改页面并复检。脚本负责可复现的渲染与页码映射，不会自行判断审美或阻止绕过该工作流；不支持图像输入时只能做结构检查，并必须披露未完成图像质检。
- 在线编辑：通过浏览器查看和编辑本地 PPTD 项目，自动保存，可配置页面切换动画。
- 手动导出：在编辑器中随时手动导出 PPTX。
- PPTD 原生编辑：可继续修改完整 PPTD 项目；PPTX-only 输入只能检查、保守填充或作为视觉复刻参考，本包不提供 PPTX→PPTD 导入。
- 安全可控：本地编辑仅在用户明确授权的项目目录内读写文件。

## 为什么选 open-kimi-ppt

常见 PPT Skill 大致分三类：用代码库直接拼 OOXML / pptxgenjs、整页生成图片再塞进 PPTX、或输出网页 HTML 翻页。open-kimi-ppt 走的是 PPTD 中间层 + 真实可编辑 PPTX 这条路线，想让 Agent 好写、人好看、PowerPoint 能改。

| | open-kimi-ppt | 代码拼 PPTX（如 pptxgenjs） | 整页图片 PPT | 网页 HTML PPT |
| --- | --- | --- | --- | --- |
| 交付物 | PPTD 项目 + PPTX | 多为仅 PPTX | 多为仅 PPTX | 单文件 HTML |
| Agent 友好度 | YAML 逐页描述，结构清晰 | 坐标/API 细节多，易排版翻车 | 依赖出图模型与提示词 | HTML/CSS 模板约束强 |
| PowerPoint 可编辑 | 示例产物中的文本、形状、图片可继续改；8 页动画样例已通过 PowerPoint 打开、另存与重渲染 | 可编辑，但难二次精修 | 整页位图，难改字 | 不是原生 PPTX |
| 视觉质量 | 真实版式 + 支持图像输入时的 Agent 质检 | 依赖 Agent 手调布局 | 画面统一，偏海报感 | 动效强，适合演示分享 |
| 二次编辑 | 浏览器可视化编辑 + 自动保存 | 主要靠改代码重导出 | 基本需重新出图 | 改 HTML 源码 |
| 适用场景 | 要交可改的正式 PPTX，又要好看 | 结构化汇报、模板填充 | 视觉统一的海报风讲稿 | 浏览器内演讲 / 发布会 |

具体来说：

- PPTD 用 YAML 描述主题、布局与元素，比直接写 OOXML / pptxgenjs 更稳，也比整页渲一张图更方便局部修改。
- 默认交付完整 PPTD、source-bound 质量报告和对应 PPTX；离线导出器不支持的语义会显式拒绝，远程 fallback 也失败时必须明确报告 partial delivery。
- 提示词写「要求带元素入场动画」即可启用元素动画，具体效果与节奏由 AI 处理，不用自己点名动画类型。
- 仓库动画示例已完成一次真实 PowerPoint round-trip：8/8 页的可编辑 DrawingML shape、页内动画和 fade 均保留，重渲染无溢出；但本轮在线 writer 的签名仍返回 HTTP 401，未取得新的 writer 产物，因此不能把示例 round-trip 外推成当前外部服务已通过。
- 浏览器里可以预览、微调、配置切换动画并再次导出，不用每次都让 Agent 重跑全流程。
- 支持图像输入的 Agent 按 Skill 工作流执行时，会先用整页截图和总览图检查遮挡、出界、对比度、溢出等问题，修完再出 PPTX；这一步是 Agent 编排，不是 `export_images.py` 内置的自动审美评分器。
- 不绑定官方模型，成本更低。相对官方 Kimi Slides，可以在任意兼容 Agent 里使用 DeepSeek 等低成本模型；模型不支持多模态时，按 PPTD 规范生成也能做出像样的成品，有多模态时再做一遍视觉质检会更稳。

[![DeepSeek 生成 Liquid Glass 风格 PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-deepseek-liquid-glass.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-deepseek-liquid-glass.png)

*上图：在 WorkBuddy 中用 DeepSeek-V4-Flash 生成的 Apple Liquid Glass 风格 PPT。*

[![Reasonix + DeepSeek 生成 DJI Pocket 4 Pro PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-reasonix-deepseek.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-reasonix-deepseek.png)

*上图：在 Reasonix 中使用 DeepSeek-V4-Flash 生成 DJI Pocket 4 Pro PPT。*

[![ChatGPT / Codex 使用 5.6 Luna 生成 iPhone 17 Pro PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-codex-iphone17pro.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-codex-iphone17pro.png)

*上图：在 ChatGPT / Codex 中使用 5.6 Luna 模型生成的 iPhone 17 Pro PPT。*

### 关于风格与主题

默认 **不会** 自动套用固定主题：未指定风格时由 Agent 按场景指南自行发挥。Skill 内附 30 套 design-system preset，**仅在你点名时**才会使用（例如「用 pine-green-strategy」）。

完整主题名、风格说明与预览图见 **[theme.md](theme.md)**。

> [!TIP]
> 建议在 Prompt 里写明 PPT 风格、点名一套 preset、提供完整 PPTD 模板，或附上可查看的 PPT / PPTX 页面作为视觉参考。有风格约束或参照时，效果会稳定不少；只给主题不给风格时，Agent 只能自行发挥，容易波动。

常见用法：

1. **在 Prompt 里描述风格**：例如「深色科技风」「杂志排版」「苹果 liquid glass」「极简留白 + 大字报」等；
2. **点名预设主题**：例如「用 `pine-green-strategy`」——主题列表见 [theme.md](theme.md)；
3. **提供参考**：上传完整 PPTD 模板以原生复用；现有 PPT / PPTX / 截图仅作为可查看的视觉参考，让 Agent 迁移配色、版式与风格。

可组合使用：先点名 preset 或给模板定调，再用一句话补充本次要强化的风格。

## 界面预览

| 在线编辑 PPTD | 导出 PPTX |
| :---: | :---: |
| [![在线编辑 PPTD](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/editor-overview.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/editor-overview.png) | [![导出 PPTX](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/export-pptx.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/export-pptx.png) |

## 什么是 PPTD

PPTD 是一种基于 YAML 的演示文稿 DSL，是 OOXML 之上的简化抽象层：保留主题、页面布局、元素位置等核心信息，去除了 Master 等复杂嵌套，每页自包含、所见即所得。完整的格式定义见 [reference/pptd.md](skills/open-kimi-ppt/reference/pptd.md)。

一个完整的 PPTD 项目目录结构如下：

```text
deck/
  deck.pptd             # 清单文件
  deck.meta.json        # 可选：受众、目标、语言、节奏、目标应用与切换锁定
  quality-report.json   # 源文件定稿后生成的严格 source-bound 报告
  pages/                # 每页一个 .page 文件
  media/
    sources.json        # 引用本地媒体时记录来源、许可、SHA-256 与派生链
    *                   # 本地媒体资源（如有）
  deck.pptx             # 工作流默认尝试生成的 PPTX 成品
```

## 工作原理与安全边界

- CLI 只在 `127.0.0.1` 启动静态文件服务，不会监听局域网地址。
- 浏览器只在用户主动授权后读取完整 PPTD 项目目录。
- 保存回调只允许修改 `.pptd` 和 `.page` 文件，并拒绝绝对路径与 `..` 路径越界。
- 可写目录的多文件保存会先写入项目根目录的私有恢复日志。宿主通过 IndexedDB 保存目录句柄到不透明项目 scope 的映射，以 Web Locks 串行化同一浏览器 origin 内、同一目录的多标签页操作，并在该 scope 独立的 `localStorage` 键中保存与事务 ID、日志字节数及 SHA-256 绑定的授权证据；缺少这些浏览器能力时写入会 fail closed。文件固定按“新页面 → manifest → 删除旧页面”提交；`armed` 阶段中断时，下一次可写打开只有在日志与授权完全匹配时才恢复完整旧版本；全部文稿写入确认后先进入 `committed`，清理窗口中断则保留完整新版本并只完成日志收口。伪造、篡改、跨项目、无授权或存在外部冲突的日志会在修改任何文稿文件前整体拒绝并保持冻结；只读打开不会绕过待恢复事务。
- 自动保存会在写入前后用稳定快照与 SHA-256 检测外部改动，冲突或提交结果不确定时会冻结后续保存并要求明确重新载入。浏览器 File System Access API 无法为本地原生编辑器提供真正的跨进程原子 CAS；不要同时用两个程序修改同一组 PPTD 文件。
- Python 导出器在 POSIX 上以持久目录句柄、逐级 `openat`/`O_NOFOLLOW` 和 descriptor-relative 发布约束项目输入与输出；Windows 使用持久目录 `HANDLE`，打开输入后以最终文件句柄验证仍位于锚定根内，并以目标父目录 `RootDirectory` 执行内核级 no-replace rename。缺少可靠 `dirfd` 或 Windows HANDLE 能力的平台不会退回路径式发布，而是直接 fail closed。
- PPTD 内容由本地宿主交给公开的 Kimi 网页编辑器代码处理，这本身跨越网络信任边界；远程图片、字体和编辑器资源也可能从对应服务器加载。机密或受监管内容只有在用户授权且 Kimi/Moonshot 的数据处理政策可接受时才应使用此流程。
- 本项目不会提供、注入、打印或导出 Kimi 登录令牌，导出代码也不会主动枚举或打开用户的 Kimi 私有文稿。若用户明确选择 `AGENT_BROWSER_PROFILE` / `AGENT_BROWSER_STATE`，该会话会交给公开 Kimi 页面及其 writer 签名流程；同源页面代码仍拥有用户授予该会话的站点权限，脚本无法用技术手段把 Cookie 权限严格缩减到单个签名接口。因此应使用专用、最小权限的账号与配置目录，不要复用含有敏感私有文稿的日常浏览器会话。

## 兼容性说明

这是针对当前公开实现的兼容宿主，不是稳定的官方 SDK。Kimi 更新前端资源哈希、PPTD 格式或 iframe/RPC 协议后，本项目可能需要同步升级。成功生成 PPTX 也不代表 PowerPoint、WPS 和 Keynote 对所有动画效果都能完全一致地播放。

## 本地开发

`npm test` 会同时运行 Node 与 Python 测试；CI 另执行 npm package dry-run，并在 Ubuntu、macOS、Windows 上分别覆盖 Node 18 与 24，Windows 回归包括路径 `stat` / descriptor `fstat` 文件标识差异和可写句柄 `fsync`。测试假设所选 Python 环境已经安装 PyYAML、Pillow 与 websocket-client。若默认 `python3` 不是该环境，请把测试专用的 `OPEN_KIMI_PYTHON` 设为独立虚拟环境中 Python 可执行文件的绝对路径；deck CLI 使用的覆盖变量则是 `OPEN_DECK_PYTHON`。

```bash
npm install --global .
OPEN_KIMI_PYTHON=/absolute/path/to/venv/bin/python npm test
npm run pack:check
```

Windows PowerShell 可先执行 `$env:OPEN_KIMI_PYTHON = "C:\absolute\path\to\venv\Scripts\python.exe"`，再运行 `npm test`。

## 声明

Kimi、Kimi Slides 及相关商标归其权利人所有。

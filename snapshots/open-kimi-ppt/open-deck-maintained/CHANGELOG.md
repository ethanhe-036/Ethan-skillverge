# 更新日志

[简体中文](CHANGELOG.md) | [English](CHANGELOG_EN.md)

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.4.1] - 2026-08-10

### 修复

- 为 Node.js 18 测试环境显式安装 Node Web Crypto 测试适配器，同时保持浏览器运行时对原生 Web Crypto 的 fail-closed 要求
- 让安装器回归兼容 Windows checkout 的 CRLF，并把“打开旧文件句柄后目录改名”的 inode 语义测试限定到 POSIX；Windows 对该场景继续在发布前安全拒绝
- Windows 导出发布改用 `NtSetInformationFile` 的目录 `HANDLE` 相对、no-replace 重命名；统一 Windows 路径 stat/descriptor fstat 的稳定文件快照，并在关闭可写句柄后按文件身份、类型与精确尺寸复核
- 在 ZIP 中央目录物化前直接解码并校验 PPTX 原始成员名，避免 Windows 路径规范化掩盖反斜杠成员

## [1.4.0] - 2026-08-10

### 安全与可靠性

- Python 导出器把项目根和输出父目录升级为稳定目录能力：POSIX 使用逐级 `openat`/`O_NOFOLLOW` 与 descriptor-relative 发布，Windows 使用稳定目录 `HANDLE`、最终文件句柄归属校验与相对 `RootDirectory` 的 no-replace rename；缺少可靠内核能力的平台直接 fail closed。manifest、page、image、PPTX、browser-raw 与图片目录的祖先换链均被拒绝
- 本地编辑器以受限语义解析器建立页面能力，只接受唯一顶层 `pages`；JSON/YAML 在对象物化或授权前执行节点、深度、字符串、路径与 YAML 总行数预算，逐行解析改为惰性迭代，并严格拒绝非法 UTF-8
- 所有跨 iframe RPC 统一使用 deadline、session epoch 与结果未知状态；多文件保存新增依赖排序、持久恢复 journal、按目录项目隔离的不透明宿主授权、覆盖 recovery 与完整文件事务的同项目 Web Lock、`preparing → armed → committed` 崩溃收口和不可逆 finalization 提交点、备份摘要验证与外部冲突预检
- 多目标安装先全量预检再提交，失败时逆序回滚；提交和回滚均验证目标能力与安装树内容，避免祖先换链或删除并发修改；保留的 `_user_meta.json` 在事务期维持同一 inode，避免旧文件句柄的晚到写入丢失
- Kimi writer 的 origin/path、按钮和格式控件集中为显式 UI contract，歧义匹配 fail closed；默认只接受已验证的 `agent-browser` 0.33.2，更高版本必须由用户显式设置 `OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER=1`
- 修复 PPTX Blob 监控遗漏 FileSaver `dispatchEvent(click)` 下载路径的问题；同步 WASM 暂时占用 writer 主线程时，CDP 状态轮询会在总产物期限内继续等待，并以无凭据的签名、进度、Blob 与下载信号报告最终停点
- 对齐当前公开编辑器的真实保存协议：严格验证并丢弃有界 `saveFrom/slideId/chatId/file/title` metadata，支持旧字符串及当前 `{path, content}[]` `fileContent` echo，并在本地编辑器与自动导出宿主中完整计入队列和字节预算

### 变更

- 正式发布坐标迁移到 `FeidaWang/Open-Deck-Skill`；新 npm 包和主 CLI 使用全小写 `open-deck-skill`，Codex Skill ID 与显式调用继续保持 `$open-kimi-ppt`
- 旧 npm 包 `open-kimi-ppt-skill` 保持撤下状态，不再作为安装或更新入口；源码入口 `node bin/open-kimi-ppt-skill.js` 仅为 checkout 内部兼容路径

### 文档

- 明确 POSIX `dirfd`、Windows 目录 `HANDLE`、无可靠内核能力时 fail-closed 的平台边界，以及外部本地编辑的非原子 CAS 和中断保存 journal 的恢复行为
- 新增 Codex 品牌图标、显式 `$open-kimi-ppt` 路由与 README 功能验收矩阵；默认关闭会与内置演示文稿能力冲突的隐式调用；用真实示例回归验证 slide 数、fade、字体部件、可编辑 DrawingML 形状和页内动画 `p:timing`
- 补充真实系统目录选择与磁盘自动保存 canary、无登录图片导出 canary，以及 Microsoft PowerPoint 动画样例 round-trip；明确记录上游 PPTX 签名 HTTP 401 和 PowerPoint 另存后字体部件不保留的边界
- 公开披露旧 npm 包已撤下，并记录新包 `open-deck-skill@1.4.0`、正式仓库与 `$open-kimi-ppt` Skill ID 的迁移边界；发布后恢复经 registry 验证的 `npx` 主路径

## [1.3.0] - 2026-08-09

### 安全与可靠性

- 本地编辑器改为文稿会话的 prepare/commit 切换：冻结并排空保存队列后再切换，保存绑定不可变项目上下文；恢复失败会持续封锁写入，避免跨目录误写和静默丢稿
- 保存 RPC 严格校验操作、路径、页面闭包、图片能力、文件数与字节预算；本地图片只按载入时授权的声明依赖提供，不再允许路径猜测或 iframe 自授权
- 静态编辑器资源使用启动时不可变快照，拒绝 symlink，并限制单文件、总字节、条目数、文件数和目录深度；运行期请求不再访问文件系统
- PPTX 与页面图片导出改用隔离下载目录、输入/输出边界检查、staging、回滚和原子 no-replace 发布；ZIP、OOXML、页面数和解压资源均设上限
- PPTX 导出优先从固定的隔离 iframe 分块取回 writer 生成的 Blob；取得 CDP endpoint 且监控安装成功时，writer 签名返回 401/403 会快速失败且不读取或记录令牌。只有无法取得 endpoint 时才使用有界文件下载兼容路径；已有 endpoint 但目标/hook 无法确认时拒绝降级
- 点击导出后的默认嵌字体产物等待/捕获期限约 75 秒；超时后关闭旧会话并在干净会话中禁用字体重试（产物期限约 180 秒），浏览器启动与文稿载入另有独立上限；摘要报告字体开关是否可观察、是否降级及实际字体部件数
- 安装器改为同文件系统 staging + 备份恢复，保留本地 `_user_meta.json`，失败不再先删除可用安装
- Penpal 改为随包固定版本并启用 CSP；发布测试同时执行 Node 与 Python 测试

### 变更

- 导出脚本不再隐式运行 pip 或全局 npm 安装；缺少依赖时只报告可执行命令并等待授权
- 包 CLI/本地编辑器仍支持 Node.js 18+；已测试且被接受的 `agent-browser`（最低 0.33.2）自动导出与图片质检要求 Node.js 24+
- `--keep-browser-raw` 是 official PPTX 成功后的诊断副产物；晚到同名冲突会使用唯一文件名并报告实际路径；强制替换时正式文件与已有 raw 文件的恢复备份分别报告
- 文档说明当前公开 writer 可能需要已登录的 Kimi 会话；只支持用户明确选择的专用 `AGENT_BROWSER_PROFILE` / `AGENT_BROWSER_STATE`，不会默认复用日常 Chrome 配置
- 文档明确本包不含 PPTX→PPTD 导入器、支持自定义 Skill 安装目录，并说明公开 Kimi iframe 的网络信任边界与实际字体嵌入结果
- 新增 `agents/openai.yaml`，便于兼容 Agent 正确展示并调用 Skill

## [1.2.0] - 2026-08-06

### 新增

- CLI 支持 `-h` / `--help` 与 `-V` / `--version`

### 变更

- npm 包名与 CLI 由 `open-kimi-ppt-skills` 统一为 `open-kimi-ppt-skill`（与 GitHub 仓库名一致）。请改用 `npx open-kimi-ppt-skill@latest`；旧包名将不再更新

## [1.1.3] - 2026-08-06

### 新增

- `install` 在交互终端下列出 `.agents` / `.codex` / `.claude` / `.cursor` / `.workbuddy` 技能目录，支持空格多选
- 支持 `-y/--yes`（非交互默认目录）、可重复的 `--target`
- `--all` 仅装到已检测到的 Agent 目录；对应目录不存在的 Agent 会跳过并提示，不会新建目录
- Windows 导出自动启动一个常驻调试浏览器（Chrome，未安装时回退 Edge），规避 agent-browser 无法自行拉起 Chrome 导致的导出失败；实例导出后常驻、后续导出复用同一个，也可用 `AGENT_BROWSER_CDP` 指定自己启动的调试浏览器

### 修复

- 修复 `find_download` 在 Downloads 目录扫描时因 `.crdownload` 被 Chrome 重命名而触发 `FileNotFoundError` 导致导出中断（Related to #4）

### 变更

- README 默认指引 AI 通过 `npx open-kimi-ppt-skill@latest install -y` 安装，不再建议 clone 仓库

## [1.1.2] - 2026-08-06

### 修复

- 修复中文 Windows 下 `export_pptx.py` / `export_images.py` 导出卡住或 GBK 解码失败（stdout 改走临时文件 + UTF-8）
- 规避 agent-browser `--download-path` 导致 Chrome 静默取消下载：改为点击下载并轮询默认 Downloads
- 修复 npm 包误打入 `__pycache__/*.pyc`（`files` 仅列出脚本源文件）

## [1.1.1] - 2026-08-06

### 新增

- PPTD 对齐官方元素级 `animations` 规范；Skill 补充动画 / `notes` 使用边界
- 对齐配图优先级、文风禁令、澄清提问、复刻细则、并行写页等官方策略
- 内置约 30 套 preset design system，并支持点名使用
- 恢复 `customFonts`（Google Fonts）与海报推荐尺寸
- 根目录主题目录：`theme.md` / `theme_EN.md`（含预览图）
- 示例项目 `example/xiaomi-yu7-ppt-animation`（页内元素入场动画）

### 变更

- 场景文档同步官方动画细则与 `customFonts` 引用
- README 补充元素动画、预设主题说明与示例 Prompt

## [1.0.2] - 2026-08-06

### 变更

- `install` 默认直接覆盖已安装的 Skill，无需再加 `--force`（旧的 `--force` 仍可兼容传入）

## [1.0.1] - 2026-08-06

### 新增

- Skill 工作流增加 **step0 本地前置检测**：生成前检查 Node.js 18+、npm/npx、python3，并提示需要 Chromium 系浏览器
- 导出脚本在启动时检测 **Node.js 18+** 与 **npm**；缺失或版本过低时给出明确安装指引
- CLI（`open-kimi-ppt-skill`）启动时校验 Node.js 主版本 ≥ 18
- 缺失 **PyYAML** 时自动 `pip install --user pyyaml`（与 Pillow / websocket-client 行为一致）

### 文档

- 补充多 Agent / 多模型实测截图（ChatGPT·Codex + 5.6 Luna、Reasonix + DeepSeek、WorkBuddy 等）
- 安装说明改为「自动 / 手动二选一」，并补充 Windows 路径说明
- README 结构与示例图更新

## [1.0.0] - 2026-08-05

### 新增

- 首次发布 `open-kimi-ppt-skill`
- PPTD 生成 / 编辑 / 复刻，默认同时交付可编辑 PPTD 项目与 PPTX 成品
- 浏览器侧导出 PPTX（嵌字体、淡入淡出切换），导出前可选多模态视觉质检
- 本地在线 PPTD 编辑器（`npx open-kimi-ppt-skill serve`）
- CLI 安装 Skill 到 `~/.agents/skills`（可用 `--target` 指定其他 Agent 目录）

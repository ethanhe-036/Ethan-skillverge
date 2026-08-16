# Urgent 修复清单

本文记录 2026-08-09 至 2026-08-10 重新审计发现并执行的核心修复。本地代码中发现的 3 个 P1 和若干 P2 已完成，并补充了失败优先的对抗性回归；当前未发现仍可复现的本地 P0–P2。维护者已确认合法再分发权，正式仓库为 `FeidaWang/Open-Deck-Skill`，npm 包与主 CLI 为 `open-deck-skill`；1.4.0 是该坐标下的首个正式版本，1.4.1 补齐 Node.js 18 与 Windows CI 的跨平台测试契约。原生目录选择器的实际写回现已真实通过；唯一仍未完成的外部产品 canary 是当前 Kimi writer 的 PPTX 产物，真实运行在签名阶段返回 HTTP 401。它不会被冒充为已验证。

## P0：公开分发链迁移（已闭环）

现状：

- 旧包 `open-kimi-ppt-skill` 已于 2026-08-07 从 npm 撤下，不再作为发布入口。
- 正式仓库为 `https://github.com/FeidaWang/Open-Deck-Skill`，公开、未归档、默认分支为 `main`，Issues 已启用；发布使用既有远端历史和普通 fast-forward push，从未 force-push。
- 新 npm 名称按注册表规则规范化为全小写 `open-deck-skill`；当前版本为 `1.4.1`，旧包与旧 CLI 名不再作为安装或更新入口。
- 发布闭环同时验证 registry 元数据、全新缓存下的 `npx` CLI、Skill 安装与 `serve --help`，不以本地 `npm pack` 代替公开可获取性。

发布闭环：

- [x] 维护者确认代码、示例、设计素材和 Kimi 兼容实现的合法再分发授权，并选择未归档的正式仓库 `FeidaWang/Open-Deck-Skill`。
- [x] 以具备 `ADMIN` 权限的 GitHub 账号取回远端历史，使用普通提交更新 1.4.0，并启用 Issues 后复核 `package.json` / README 链接。
- [x] 以已认证 npm 账号发布 `open-deck-skill@1.4.0`，并从干净环境验证 `npm view`、`npx --version`、安装、Skill 发现和 `serve --help`。

## P1：必须优先修复

### 1. 用稳定目录能力替代一次性路径校验

涉及文件：

- `skills/open-kimi-ppt/scripts/export_pptx.py`
- `skills/open-kimi-ppt/scripts/export_images.py`
- 对应 Python 测试

现状：

- `safe_project_path()` 先用 `Path.resolve()` 判断输入位于项目内，后续再按路径名打开文件。
- 输出目录同样先校验，之后再按路径名创建 staging 和发布文件。
- `O_NOFOLLOW` 目前只保护最后一级文件，不能防止中间目录在校验后被重命名并替换为符号链接。

风险：

- 可在校验后把项目内目录替换为指向项目外部的符号链接，使项目外文件被嵌入 payload 并发送给 Kimi。
- 可在输出校验后替换输出父目录，使 PPTX 或图片产物发布到非预期位置。

要求：

- [x] 输入项目根和输出父目录必须由已打开的目录句柄锚定，不能把 `resolve()` 后的字符串视作持久能力。
- [x] POSIX 优先使用逐级 `openat()`，目录段使用 `O_DIRECTORY | O_NOFOLLOW`；Linux 可使用 `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)`。
- [x] 文件读取、staging 创建和最终发布均从锚定目录句柄出发，不得重新按未经锚定的绝对路径打开。
- [x] 跨平台回退方案至少应记录并复核每级祖先的设备号/inode（Windows 使用相应文件标识），且最终文件操作必须基于已验证句柄。
- [x] 明确内部符号链接策略：若无法在所有平台安全支持，应 fail closed，而不是静默降级。

验收测试：

- [x] 输入路径校验完成后交换中间目录为外部符号链接，导出必须拒绝，外部内容不得进入 `imageMap`。
- [x] 输出路径校验完成后交换父目录，正式产物不得出现在重定向目录。
- [x] 对 manifest、page、image、official output、browser-raw output 和图片目录分别覆盖祖先交换。
- [x] 正常目录、允许的内部文件和无竞态发布仍能成功。

### 2. 使用受限语义解析器读取 YAML 顶层 `pages`

涉及文件：

- `editor/lib.js`
- `editor/app.js`
- `test/editor-lib.test.js`
- 必要时新增受限 YAML 解析模块

现状：

- `extractPagePaths()` 使用正则寻找第一个任意缩进的 `pages:`。
- 嵌套的 `metadata.pages` 会被当成文稿的顶层页面列表。
- `loadManifest()` 随后读取这些文件，并把内容发送给跨域 Kimi iframe。

最小反例：

```yaml
version: v2
metadata:
  pages:
    - private.page
pages:
  - safe.page
```

当前结果错误地是 `private.page`。

要求：

- [x] 只接受唯一的顶层 `pages` 键。
- [x] 拒绝重复键、YAML alias/anchor、merge key、复杂键和超出支持子集的结构。
- [x] `pages` 必须是字符串路径数组，并统一 JSON/YAML 对空数组的语义。
- [x] 页数、路径长度、深度和节点数限制必须在建立能力集合之前执行。
- [x] 解析失败时不得读取任何 page，也不得扩大图片或可写路径能力。

验收测试：

- [x] 嵌套 `metadata.pages` 不得影响顶层页面列表。
- [x] 顶层重复 `pages` 必须拒绝。
- [x] 只有嵌套 `pages`、错误缩进、alias、merge key、复杂键必须受控失败。
- [x] 失败时 `textFromIndexedFile()` 对非 manifest 文件的调用次数为零。
- [x] 合法 JSON/YAML manifest 得到一致的 canonical page paths。

### 3. 为所有远端 RPC 增加 deadline 和结果未知恢复状态

涉及文件：

- `editor/save-lifecycle.js`
- `editor/app.js`
- `test/editor-state-machine.test.js`

现状：

- `setEditable(false/true)`、`setPPTD()`、`getSlideStatus()` 等远端调用没有 deadline。
- iframe 丢失回复时，`preparingSwitch`/`switchLease` 可永久保留，打开、重载和切换文稿全部被锁死。

要求：

- [x] 建立统一的 RPC 调用包装器，为所有握手后调用设置有界 deadline。
- [x] 优先使用 Penpal 的调用超时能力；不能只用会留下未处理请求的简单 `Promise.race`。
- [x] 超时必须按“请求可能已经在远端生效”处理：保持保存阻断、标记远端状态不确定，并尝试重新握手/重装已知文稿。
- [x] 只有远端状态被确认恢复，或用户明确授权丢弃并完成 reload 后，才能重新开放保存。
- [x] 超时、断连和迟到回复必须受 lease/epoch 约束，旧请求不得修改新会话状态。

验收测试：

- [x] freeze、thaw、`setPPTD`、状态查询分别使用永不完成 Promise，均在 deadline 后退出。
- [x] 超时后不允许第二次保存或切换偷偷继续写盘。
- [x] 迟到成功/失败回复不得复活旧 lease 或改变新文稿。
- [x] 可恢复路径成功后正常解锁；恢复失败保持 discard-only。

## P2：数据完整性和可靠性

### 4. 为多文件保存增加崩溃一致性

涉及文件：

- `editor/app.js`
- `editor/file-transaction.js`
- 新增 journal/staging 模块及测试

要求：

- [x] 先按依赖排序：新页面 put → manifest put → 已废弃页面 delete。
- [x] 不得直接按远端 RPC 提供的任意顺序提交。
- [x] 引入可恢复 journal 或同项目 staging，记录事务 ID、预期旧版本、目标版本和提交阶段。
- [x] 启动/重载时检测未完成事务，并提供确定性的完成或回滚路径。
- [x] 保留现有外部 writer 冲突检测；journal 不能覆盖冲突文件。
- [x] 宿主授权按不泄露目录路径的持久项目 scope 隔离；同源不同项目或多标签页不得互相清理、授权或冻结。
- [x] recovery 以及每次 `begin → 文件写入/回滚 → finish` 完整事务由同一项目 scope 的 Web Lock 串行化，不能只锁 scope ID 分配。
- [x] 使用 `preparing → armed → committed` 状态收口；完整提交后即使在删除 journal 与清除授权之间崩溃，也不能把成功保存误判为待回滚事务。
- [x] 进入 journal finalization 后若宿主提交确认、日志删除或授权清理失败，禁止再发起第二轮文件回滚；保持完整文稿并冻结到下一次 scoped recovery。

验收测试：

- [x] 在每一个文件 close 后注入进程中断，重新加载后都能恢复到完整旧版本或完整新版本。
- [x] manifest 永远不能稳定指向尚未存在的页面。
- [x] rollback 与外部修改冲突时保留外部版本并报告不确定状态。
- [x] 同一 origin 下 A/B 两个目录项目、同项目多标签页及每一个 finish 清理边界都有确定性回归。

### 5. 严格拒绝非法 UTF-8

涉及文件：

- `editor/app.js`
- 对应加载测试

要求：

- [x] 目录模式和上传 fallback 都使用 `ArrayBuffer` 加 `TextDecoder("utf-8", { fatal: true })`。
- [x] 删除 `File.text()` 与非 fatal `TextDecoder` 的有损路径。
- [x] 编码错误应显示具体文件路径，且不得更新内存 baseline 或进入保存流程。

验收测试：

- [x] JSON/YAML 字符串内部含非法 UTF-8 时加载受控失败。
- [x] 原始文件字节保持不变，保存接口不被调用。

### 6. 在 JSON 物化前执行资源门禁

涉及文件：

- `editor/lib.js`
- 相关资源限制测试

要求：

- [x] 页面和 manifest 的 JSON 在 `JSON.parse()` 前完成结构/深度/节点预算预检，或改用受限流式解析器。
- [x] 对源字节、字符串长度、节点数、深度分别设置预算。
- [x] YAML 在任何逐行对象物化前执行总行数预算，并使用惰性逐行迭代，空行不得绕过资源限制。
- [x] 解析工作不得长期阻塞 UI 主线程；必要时迁移到 Worker。

验收测试：

- [x] 超节点、超深度和超长字符串输入在完整对象图构造前被拒绝。
- [x] 合法边界值正常解析，超出一项立即失败。

### 7. 多目标安装改为两阶段流程

涉及文件：

- `bin/open-kimi-ppt-skill.js`
- `test/install-skill.test.js`

要求：

- [x] 在修改任何目标前，完成所有目标的解析、规范化、去重、权限、源目录碰撞和 metadata 检查。
- [x] 预检全部成功后才逐个提交。
- [x] 明确跨文件系统无法全局原子提交时的契约，并输出每个目标的最终状态。
- [x] 若提交中途失败，尽最大努力回滚本次已提交目标；回滚不完整必须报告准确恢复路径。
- [x] 保留 `_user_meta.json` 时维持同一 inode，使旧文件句柄的晚到写入仍落入新安装；无法建立该能力时 fail closed。

验收测试：

- [x] 第二个目标预检失败时，第一个目标不得被创建或更新。
- [x] 重复/别名目标只处理一次。
- [x] 第二个目标提交失败时，第一个目标恢复到原始版本。
- [x] 发布后通过已打开旧 metadata 句柄写入时，不得静默删除新内容或错误报告安装成功。

### 8. 建立远端 Kimi UI 的契约门禁

涉及文件：

- `skills/open-kimi-ppt/scripts/export_pptx.py`
- `skills/open-kimi-ppt/scripts/export_images.py`
- `.github/workflows/ci.yml`
- 测试 fixture 与兼容性文档

要求：

- [x] 把中文按钮、DOM 选择器和 writer URL 集中到显式 adapter，不再散落在导出流程中。
- [x] 选择器必须拒绝歧义，不能默认取最后一个匹配项。
- [x] 明确 `agent-browser` 的已验证版本范围，而非只有最低版本。
- [x] 保存经过脱敏的 DOM/可访问性快照 fixture，覆盖导出对话框契约。
- [ ] 条件允许时建立使用专用账号和非敏感文稿的定时 synthetic canary；它应验证一次真实 PPTX 和图片导出成功，而不仅是 mock。

  本轮使用全新无登录 profile 和一页非敏感 PPTD 真实完成图片导出：页面 JPEG 为 1920×1080，总览为 1968×416，格式和视觉检查均通过。PPTX 真实运行在 writer 20%→50% 后发出一次签名请求并返回 HTTP 401；专用系统 Chrome/CDP 复用仍为 401，日常已登录 Chrome 的本地 iframe 中，字体开启与关闭各等待 55 秒也均无下载。没有 Blob 或 PPTX，因此 ZIP、OOXML、页数、fade、字体和渲染不能宣称通过。阻断现已明确归类为外部 Kimi writer/签名授权或上游服务，而非本地 Cookie/Token 读取问题。

## 已知平台边界，不应误称为完全解决

- Web File System Access API 不提供对外部本地进程的原子 CAS。现有多次版本校验只能缩小窗口，不能消除最后一次检查与 `close/removeEntry` 之间的竞态。
- 文档应继续明确：不要同时在网页编辑器和其他程序中修改同一组文件。
- 当前没有 PPTX → PPTD importer，这是明确的产品范围，不属于本轮回归。
- `--force` 保留恢复备份并要求用户确认后清理，是显式安全策略，不应通过静默删除备份来“优化”。

## 完成定义

只有同时满足以下条件，才能认为本清单完成：

- [x] 三个 P1 均有失败优先的对抗性回归测试。
- [x] Node 和 Python 全量测试通过。
- [x] 新增至少一次真实编辑器目录加载/保存验证。

  已用隔离 Chrome、真实系统目录选择器和 `/private/tmp` 非敏感一页项目完成加载与写回。UI 显示“自动保存已开启 / 刚刚已保存”，页面 SHA 确实变化，manifest SHA 保持不变，目录无恢复 journal，console/errors 为空；专用浏览器、CDP 与本地服务随后全部退出。真实运行同时揭示并修复当前 Kimi onSave 的 `saveFrom/slideId/chatId/file/title/fileContent[]` 协议兼容，metadata 经过小型严格 schema 验证后丢弃，echo 数组完整计入资源预算。
- [x] 新增至少一次真实导出 smoke test，或明确记录因登录/外部服务不可用而未验证的范围。
- [x] 文档描述与实际超时、备份、远端依赖及并发边界一致。
- [x] 代码评审按“能力边界、并发状态、崩溃恢复、资源预检”四类不变量重新检查，而不是仅按函数逐个检查。

## 执行结果（2026-08-10）

- P1 代码与对抗性回归均已完成：稳定目录能力、受限语义解析和有界 RPC/会话恢复全部 fail closed。
- P2 代码项已完成：依赖排序与来源绑定的恢复 journal、严格 UTF-8、JSON/YAML 物化前预算、安装器两阶段事务和 Kimi UI contract 均已落地。
- Node 18 与当前 Node 全量测试：各 `142/142` 通过（含 loopback editor server、Web Crypto 测试适配与 npm-style CLI symlink 回归；Windows 的 POSIX-only inode 用例按平台跳过）。
- Python 3.12 隔离环境全量测试：`174/174` 通过；导出脚本与测试 `py_compile` 通过。新增真实示例契约测试验证 34 张 slide、逐页 fade、字体部件、可编辑 DrawingML shape，以及动画示例 8/8 页的 `animations` / `p:timing`。
- GitHub Actions `31328319794` 已在 Ubuntu、macOS、Windows Server 2025 × Node 18/24 全部通过；Windows 实机覆盖 NT 原生目录 `HANDLE` 相对发布、关闭句柄后的稳定快照、图片目录事务与 PPTX/ZIP 校验。npm tarball dry-run 为 `104` 个条目，并已通过打包后 CLI/install 闭包 smoke；发布后仍须从 registry 独立复核 `open-deck-skill --version`、`serve --help`、Skill 安装、`quick_validate` 与 Codex metadata/assets。
- Markdown 闭包：源码 `80` 个 Markdown / `116` 个相对链接、tarball `78` 个 Markdown / `112` 个相对链接，均无缺失目标。
- Codex 用户级安装：已从本 checkout 事务式安装到 `~/.agents/skills/open-kimi-ppt`；安装后的 `SKILL.md`、`agents/openai.yaml`、图标与 logo 均和审计源码 SHA-256 一致，`quick_validate` 通过。策略要求显式 `$open-kimi-ppt`，默认关闭隐式调用，避免和 Codex 内置演示文稿能力发生不透明路由冲突。
- 真实浏览器 smoke：本地服务、公开 Kimi iframe 握手、真实系统目录授权、磁盘自动保存与 journal 清理均通过；无登录隔离 profile 的一页图片导出成功，页面图和总览通过完整性与视觉检查。
- Microsoft PowerPoint round-trip：动画示例 8/8 页成功打开并另存，8/8 保留 editable DrawingML shape、`p:timing` 与 fade，渲染无溢出；另存后字体部件从示例中的非零值变为 0，因此不能把 writer 字体部件等同于 Office 再保存后的字体保留保证。
- 未伪造的剩余外部产品门禁只有真实 PPTX writer 产物。writer 已修正 FileSaver 事件漏捕和同步 WASM/CDP 轮询误判，并能报告精确停点；本轮外部签名仍为 HTTP 401。因此本地 1.4.1 已达到可发布标准，但不能把当前 Kimi writer 的在线 PPTX canary 宣称为已通过。

## 设计原则

1. 路径不是字符串，而是由稳定根句柄约束的能力。
2. 解析结果会决定文件读取权限，因此能力只能来自语义解析。
3. 每个跨 iframe RPC 都是可能出现未知结果的分布式状态转换。
4. 异常安全、并发安全和崩溃一致是三个不同层级，必须分别验证。
5. 资源预算必须在解析、排队、复制和 RPC 之前执行。

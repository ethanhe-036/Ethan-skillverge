# Changelog

[简体中文](CHANGELOG.md) | [English](CHANGELOG_EN.md)

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.1] - 2026-08-10

### Fixed

- Install Node Web Crypto explicitly in the Node.js 18 test environment while keeping the browser runtime fail-closed when native Web Crypto is unavailable
- Make installer regressions tolerate CRLF checkouts on Windows and scope the open-old-handle rename/inode semantics test to POSIX; Windows continues to reject that case safely before publication
- Publish Windows exports through directory-`HANDLE`-relative, no-replace `NtSetInformationFile` renames; normalize stable Windows path-stat/descriptor-fstat snapshots and verify file identity, type, and exact size after closing writable handles
- Decode and validate raw PPTX member names before ZIP central-directory materialization so Windows path normalization cannot hide backslash members

## [1.4.0] - 2026-08-10

### Security and reliability

- Upgrade Python exporters from path-string checks to stable project/output directory capabilities: POSIX uses component-wise `openat`/`O_NOFOLLOW` and descriptor-relative publication; Windows uses a persistent directory `HANDLE`, final-handle containment checks, and no-replace rename relative to `RootDirectory`; platforms without a reliable kernel capability fail closed. Ancestor swaps for manifests, pages, images, official/raw PPTX files, and image directories are rejected
- Build local-editor page capabilities with a restricted semantic parser that accepts only one top-level `pages`; enforce JSON/YAML node, depth, string, path, and total YAML-line budgets before materialisation or authorization, iterate lines lazily, and reject malformed UTF-8 losslessly
- Give every cross-iframe RPC a deadline, session epoch, and explicit unknown-result state; add dependency-ordered multi-file saves, a durable recovery journal, opaque host authorization isolated per directory project, a project Web Lock covering recovery and the complete file transaction, `preparing → armed → committed` crash closure with an irreversible finalization point, backup-digest verification, and external-conflict preflight
- Preflight every multi-target install before committing, then roll back in reverse order on failure; commit and rollback verify both target capabilities and installed-tree content so ancestor swaps or concurrent edits are never silently removed, while preserved `_user_meta.json` keeps the same inode during the transaction so late writes through old file handles are not lost
- Centralize the Kimi writer origin/path, buttons, and format controls in an explicit UI contract with ambiguity rejection; accept only tested `agent-browser` 0.33.2 by default, requiring the user's explicit `OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER=1` override for newer versions
- Fix PPTX Blob monitoring for FileSaver's `dispatchEvent(click)` download path; when synchronous WASM temporarily occupies the writer main thread, CDP polling now continues within the overall artifact deadline and reports bounded credential-free signature, progress, Blob, and download signals at the final stopping point
- Match the public editor's current save protocol: strictly validate and discard bounded `saveFrom/slideId/chatId/file/title` metadata, support both the legacy string and current `{path, content}[]` `fileContent` echo, and account for it in local-editor and export-host queue/byte budgets

### Changed

- Move the formal release coordinate to `FeidaWang/Open-Deck-Skill`; use the lowercase `open-deck-skill` for the new npm package and primary CLI while retaining `$open-kimi-ppt` as the Codex Skill ID and explicit invocation
- Keep the old `open-kimi-ppt-skill` npm package retired; `node bin/open-kimi-ppt-skill.js` remains only as the source-checkout compatibility entry point

### Documentation

- Clarify the platform boundary between POSIX `dirfd`, Windows directory `HANDLE`, and fail-closed platforms without a reliable kernel capability, plus the non-atomic external-writer CAS boundary and interrupted-save journal recovery
- Add Codex artwork, explicit `$open-kimi-ppt` routing, and a README feature-verification matrix; disable implicit invocation by default to avoid conflicts with built-in presentation capabilities; regress real examples for slide count, fade transitions, font parts, editable DrawingML shapes, and on-slide animation `p:timing`
- Add a real native-directory disk-save canary, a fresh-profile image-export canary, and a Microsoft PowerPoint animation-sample round-trip; explicitly record the upstream PPTX-signature HTTP 401 and the loss of font parts after PowerPoint resaves the sample
- Disclose the retirement of the old npm package and record the migration boundary between `open-deck-skill@1.4.0`, the formal repository, and the retained `$open-kimi-ppt` Skill ID; restore the registry-verified `npx` primary path after publication

## [1.3.0] - 2026-08-09

### Security and reliability

- Reworked the local editor around prepare/commit deck sessions: freeze and drain saves before switching, bind every save to an immutable project context, and keep writes blocked when recovery cannot confirm editor state
- Strictly validate save operations, paths, page closure, image capabilities, file counts, and byte budgets; local images are now served only from dependencies authorized at load time, without suffix guessing or iframe self-authorization
- Serve editor assets from an immutable startup snapshot, reject symlinks, and cap per-file bytes, total bytes, entries, files, and directory depth; runtime requests no longer touch the filesystem
- Isolate browser downloads and add input/output boundaries, staging, rollback, atomic no-replace publication, and ZIP/OOXML/page-count/resource limits to PPTX and image exports
- Prefer bounded-chunk retrieval of the writer-generated PPTX Blob from one fixed isolated iframe. When a CDP endpoint exists and monitoring installs, signature HTTP 401/403 fails fast without reading or logging tokens. The bounded filesystem compatibility path is used only when no endpoint can be obtained; an existing but ambiguous/failed target is fail-closed
- Bound the post-click artifact wait/capture phase of the default font-embedding attempt to about 75 seconds, then close that session and retry once in a clean session with embedding disabled under an approximately 180-second artifact deadline; browser startup and deck loading have separate bounds, and the summary reports whether the control was observed, whether fallback occurred, and the verified font-part count
- Make installation transactional on one filesystem, preserve local `_user_meta.json`, and restore the previous working skill on failure
- Vendor the pinned Penpal module, apply CSP, and run both Node and Python suites before publication

### Changed

- Exporters no longer run pip or global npm installs implicitly; missing dependencies produce an actionable command and wait for authorization
- The package CLI/local editor still support Node.js 18+, while tested `agent-browser` automation (minimum 0.33.2) for export and image QA requires Node.js 24+
- `--keep-browser-raw` is a diagnostic artifact published after the official PPTX; a late name conflict uses a unique filename and reports the actual path, while forced replacement reports recovery backups for the official and any existing raw artifact separately
- Document that the current public writer may require a signed-in Kimi session; only an explicitly selected dedicated `AGENT_BROWSER_PROFILE` / `AGENT_BROWSER_STATE` is supported, never silent reuse of an everyday Chrome profile
- Documentation now states that no PPTX-to-PPTD importer is bundled, supports custom skill roots, and describes the public Kimi iframe trust boundary and verified font-embedding result
- Added `agents/openai.yaml` so compatible agents can present and invoke the skill correctly

## [1.2.0] - 2026-08-06

### Added

- CLI supports `-h` / `--help` and `-V` / `--version`

### Changed

- Renamed the npm package and CLI from `open-kimi-ppt-skills` to `open-kimi-ppt-skill` to match the GitHub repo. Use `npx open-kimi-ppt-skill@latest` going forward; the old package name will no longer receive updates

## [1.1.3] - 2026-08-06

### Added

- Interactive `install` checklist for `.agents` / `.codex` / `.claude` / `.cursor` / `.workbuddy` skill directories (space to multi-select)
- `-y/--yes` (non-interactive default) and repeatable `--target`
- `--all` installs only into detected agent directories; missing agents are skipped with a notice instead of being created
- Windows export auto-starts a persistent debug browser (Chrome, falling back to Edge) to work around agent-browser failing to launch Chrome itself; the instance stays resident and is reused across exports, and `AGENT_BROWSER_CDP` can point to a debug browser you started yourself

### Fixed

- Tolerate files vanishing mid-scan in `find_download` when Chrome renames `.crdownload` entries in Downloads, avoiding `FileNotFoundError` aborting export (Related to #4)

### Changed

- README now steers agents to `npx open-kimi-ppt-skill@latest install -y` instead of cloning the repo

## [1.1.2] - 2026-08-06

### Fixed

- Fix Chinese-locale Windows export hang / GBK decode errors in `export_pptx.py` / `export_images.py` (capture stdout via temp file + UTF-8)
- Work around agent-browser `--download-path` silently canceling Chrome downloads: click download and poll the default Downloads folder
- Stop shipping `__pycache__/*.pyc` in the npm package (list script sources explicitly in `files`)

## [1.1.1] - 2026-08-06

### Added

- Align PPTD with official element-level `animations`; Skill notes for animation / `notes` usage bounds
- Align image priority, anti-AI copy rules, clarification asks, replicate guidance, and parallel page writes
- Ship ~30 preset design systems, invoked only when named
- Restore `customFonts` (Google Fonts) and poster size recommendations
- Root theme catalogs: `theme.md` / `theme_EN.md` (with preview images)
- Sample project `example/xiaomi-yu7-ppt-animation` (on-slide entrance animations)

### Changed

- Sync scenario docs with official animation guidance and `customFonts` references
- README: document element animations, preset themes, and sample prompts

## [1.0.2] - 2026-08-06

### Changed

- `install` overwrites an existing skill by default; `--force` is no longer required (still accepted for compatibility)

## [1.0.1] - 2026-08-06

### Added

- Skill workflow **step0 prerequisite check**: verify Node.js 18+, npm/npx, and python3 before generation; note that a Chromium-based browser is required for export
- Export scripts check **Node.js 18+** and **npm** at startup, with clear install guidance when missing or too old
- CLI (`open-kimi-ppt-skill`) refuses to start when the Node.js major version is below 18
- Auto-install **PyYAML** via `pip install --user pyyaml` when missing (same pattern as Pillow / websocket-client)

### Docs

- Added multi-agent / multi-model example screenshots (ChatGPT·Codex + 5.6 Luna, Reasonix + DeepSeek, WorkBuddy, and more)
- Clarified install as “automatic or manual — pick one”, with Windows path notes
- Updated README structure and example images

## [1.0.0] - 2026-08-05

### Added

- Initial release of `open-kimi-ppt-skill`
- PPTD create / edit / replicate, delivering both an editable PPTD project and a PPTX by default
- Browser-side PPTX export (embedded fonts, fade transitions) with optional multimodal visual QA before export
- Local in-browser PPTD editor (`npx open-kimi-ppt-skill serve`)
- CLI to install the skill into `~/.agents/skills` (or another agent directory via `--target`)

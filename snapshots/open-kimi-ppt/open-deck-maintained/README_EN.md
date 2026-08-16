# Open Deck Skill

[简体中文](README.md) | [English](README_EN.md)

[![version](https://img.shields.io/badge/release-1.4.1-6657E8)](package.json)
[![node](https://img.shields.io/badge/node-%3E%3D18-brightgreen)](https://nodejs.org)

An unofficial presentation skill for AI coding agents, reverse-engineered from Kimi Slides. It lets an agent create or edit PPTD, recreate visual references such as images/PDFs in PPTD, inspect existing PPTX files, conservatively fill existing text/notes, and export a supported PPTD subset to editable PPTX offline. The authoring workflow delivers the complete PPTD project, a strict source-bound quality report, and the matching PPTX: it prefers the browserless, network-free local DrawingML exporter and uses the public Kimi writer only as an authorized fallback when the local backend cannot preserve required semantics. PPTD supports on-slide element animations and [preset themes](theme_EN.md), and a local in-browser editor is included. Codex can invoke it explicitly as `$open-kimi-ppt`; Claude Code, Cursor, WorkBuddy, and other hosts can use it only when they discover and follow `SKILL.md` and expose the required file/browser tools. Actual tool availability and delivery completeness remain host-dependent.

> [!IMPORTANT]
> This project is implemented by reverse-engineering the Kimi Slides skill, the PPTD format, and the frontend behavior and communication protocol of the publicly accessible web editor. It is not an official Kimi or Moonshot AI project and is not endorsed or supported by them. Public frontend resources and compatibility contracts used by this project may change without notice. Provided for learning and research purposes only.

> [!NOTE]
> General native editing still starts from a complete PPTD project. This package does not include a PPTX-to-PPTD importer; its PPTX sidecar only inventories structure and replaces plain text in existing text shapes or an existing notes body, while rejecting object, transition, and arbitrary OOXML edits. Anything broader requires the source PPTD, an explicitly available external conversion capability, or a newly authored visual recreation. Recreation is not conversion.

## Current implementation and verification status

| README capability | Current evidence | Boundary |
| --- | --- | --- |
| Codex discovery and presentation | `SKILL.md`, `agents/openai.yaml`, skill artwork, and product-contract tests; invoke explicitly with `$open-kimi-ppt` | Codex implicit invocation is disabled to avoid conflicts with built-in presentation capabilities; other hosts depend on their Skill/tool support |
| Create, edit, and visually replicate PPTD | Complete PPTD reference, 30 presets, canonical registries, repository examples, and the dual-delivery workflow | There is no PPTX-to-PPTD importer; visual recreation is not conversion |
| Strict quality gate and provenance | `pptd_quality.py` SHA-256-binds the manifest, pages, `deck.meta.json`, `media/sources.json`, and local media; warnings block export | Any source change invalidates the report; it is neither visual QA nor legal advice |
| PPTX inspect and conservative fill | `pptx_native.py` inventories/validates OPC and only replaces existing text shapes or an existing notes body before atomic publication | It does not create objects or notes parts and rejects tables, charts, pictures, animations, transitions, and arbitrary OOXML |
| Offline PPTX export | `export_pptx_local.py` emits full OPC/DrawingML for supported text, basic shapes, straight lines, local PNG/JPEG, notes, and safe transitions | Unsupported tables, charts, icons, animation, remote assets, custom paths, and related semantics fail closed; it is not browser-writer parity |
| Optional P3 asset admission | `asset_pack_audit.py` verifies sources, per-file SHA-256, notices, attribution, derivation chains, redistribution basis, and brand-permission evidence offline | Core does not currently bundle the roughly 12,000 icons, roughly 190 sounds, or full brand-asset sets; candidates may enter only as separate opt-in packs after P0–P2, and technical audit is not legal advice |
| Remote writer fallback, font request, and complex PPTD | Public-writer path, OOXML/ZIP validation, and three real example artifacts; an eight-slide PowerPoint round-trip preserved shapes, animation, and fade | The public Kimi writer is external and the real signature still returned HTTP 401. PowerPoint removed the sample's font parts when resaving, so results report only actual package parts and do not promise post-resave retention |
| On-slide element animations | All eight `.page` files in the animation example contain `animations`; all eight slides in both the example and PowerPoint-resaved copy contain `p:timing` | The PowerPoint round-trip passed; playback consistency in other Office/WPS versions remains host-dependent |
| Image QA | `export_images.py` creates page images, page mapping, and an overview; a real one-page export in a fresh unsigned profile passed; an image-capable agent inspects, fixes, and rechecks them | The script is not an aesthetic model; without image input only structural review is possible and the skip must be disclosed |
| Local editor and manual export | An isolated Chrome run completed native directory selection, public-iframe editing, automatic disk save, SHA verification, and journal cleanup; adversarial save/journal tests also pass | The Web File System Access API still cannot provide atomic CAS against external processes; do not edit the same files concurrently elsewhere |
| Installation and public acquisition | `npx open-deck-skill@latest install` is the primary public path for 1.4.1; the CLI, install transaction, and npm tarball are tested | Check the registry with `npm view open-deck-skill version`; source installation remains an auditable fallback. The old package is retired, while the Skill ID remains `$open-kimi-ppt` |

See [urgent.md](urgent.md) for the complete outstanding gates and remediation record. The table separates public distribution, implemented code, locally verified behavior, and the external writer canary. A working public install does not mean the Kimi writer passed: the real signature still returned HTTP 401 in this audit, so the external PPTX export gate must not be reported as end-to-end complete.

## Install

Node.js 18 or later is required. The default location is the shared directory `~/.agents/skills/open-kimi-ppt` (Windows PowerShell: `$env:USERPROFILE\.agents\skills\open-kimi-ppt`; Command Prompt: `%USERPROFILE%\.agents\skills\open-kimi-ppt`), which Codex discovers directly.

> [!WARNING]
> The old `open-kimi-ppt-skill` package was unpublished from npm on 2026-08-07 and must no longer be used. The current public npm package and CLI are both named with the lowercase `open-deck-skill`; first verify that `npm view open-deck-skill version` returns `1.4.1` or newer, then use the `npx` commands below. If the registry is temporarily unavailable, use the source-install fallback instead. The npm/CLI name changed, while the Codex Skill ID and explicit invocation remain `$open-kimi-ppt`.

### Option 1: Install from the public npm package (recommended)

```bash
# Confirm the public version
npm view open-deck-skill version

# Interactive checklist (space to select, Enter to confirm)
npx open-deck-skill@latest install

# Non-interactive: shared directory only
npx open-deck-skill@latest install -y

# All detected agent skill directories (missing ones are skipped)
npx open-deck-skill@latest install --all
```

Directories detected by `--all` and the interactive checklist: `~/.agents/skills`, `~/.codex/skills`, `~/.claude/skills`, `~/.cursor/skills`, `~/.workbuddy/skills`.

**WorkBuddy users**: WorkBuddy cannot discover the shared directory. Select WorkBuddy in the interactive checklist or pass its Skill directory explicitly:

macOS / Linux:

```bash
npx open-deck-skill@latest install --target ~/.workbuddy/skills
```

Windows PowerShell:

```powershell
npx open-deck-skill@latest install --target "$env:USERPROFILE\.workbuddy\skills"
```

Windows Command Prompt:

```bat
npx open-deck-skill@latest install --target "%USERPROFILE%\.workbuddy\skills"
```

### Option 2: Install from source (fallback)

To audit the source or when the public registry is temporarily unavailable, run this from an audited repository checkout, or ask Codex or another agent to run it:

```bash
node bin/open-kimi-ppt-skill.js install -y
```

### When an agent can't discover the skill

Start with the shared directory instead of installing once per agent. If a specific agent can't discover the skill there, pass its directory explicitly (`--target` may be repeated; quote Windows paths, using `$env:USERPROFILE` in PowerShell or `%USERPROFILE%` in Command Prompt):

```bash
node bin/open-kimi-ppt-skill.js install --target ~/.codex/skills --target ~/.claude/skills
```

### Update

For an npm install, rerun the corresponding `npx open-deck-skill@latest install ...` command. For a source install, rerun the same `node bin/open-kimi-ppt-skill.js install ...` command. If you originally used `--target` / `--all`, pass the same flags. Updating only replaces the skill files and does not touch PPTD / PPTX projects you already generated.

With repeated `--target` options, the installer resolves, deduplicates, and preflights every destination before replacing any of them. If a later commit fails, it restores earlier targets in reverse order when their newly installed trees are still unchanged. If an external modification makes rollback unsafe, the command preserves and reports the recovery directory rather than overwriting it silently.

Package installation and the local editor support Node.js 18+, while automated browser export and image QA currently test and accept `agent-browser` 0.33.2 by default; that version requires Node.js 24+. Newer versions fail closed unless the user explicitly sets `OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER=1`, in which case the exporter continues with a warning. Automated export also needs Python 3 (CI currently pins and verifies 3.12), PyYAML, and websocket-client; image QA additionally needs Pillow. Export scripts never silently mutate global npm or pip environments and report an exact install command before browser work starts when a dependency is missing. The current public PPTX writer's signature endpoint may require a signed-in Kimi browser session. When a CDP endpoint is obtained and Blob monitoring installs successfully, an unsigned session fails fast on HTTP 401/403. The bounded filesystem compatibility path is used only when no CDP endpoint can be obtained; it cannot classify authentication failures and reports that limitation after the current attempt times out. If an endpoint exists but the iframe target or hook cannot be identified uniquely, export fails closed instead of downgrading. After the export click, the default font-embedding attempt shares an approximately 75-second artifact wait/capture deadline; if it yields no artifact, the exporter closes that session and retries in a clean session with embedding disabled under an approximately 180-second artifact deadline. Browser startup, page/deck loading, and cleanup have separate bounded timeouts. The result summary reports the fallback, whether the font control was observed, and the verified font-part count. `--force` retains and reports the official artifact as `previousOutputBackup`; replacing an existing `--keep-browser-raw` artifact also reports `previousBrowserRawBackup`. After accepting the new artifacts, explicitly move or remove those backups before another forced export.

## Usage

In Codex, invoke `$open-kimi-ppt` explicitly. Implicit invocation is disabled by default to avoid routing conflicts with built-in presentation capabilities and to prevent an ordinary PPT request from using the third-party Kimi writer or a signed-in session without the user's awareness.

### Generate a presentation with your agent

Once installed, just describe what you need. The authoring workflow generates a `--fail-on warning` source-bound quality report from the final project and then prefers offline PPTX export. The local backend fails closed on unsupported semantics and the public writer is attempted only with user authorization. If no backend can safely preserve the request, the agent must deliver the complete PPTD, preserve the error evidence, and mark PPTX as incomplete rather than pretending dual delivery succeeded.

For more stable quality, put a style in the prompt (e.g. “dark product-launch look”), provide a complete PPTD template, or attach inspectable PPT/PPTX pages as visual style references. Topic-only prompts without style guidance tend to vary more. PPT/PPTX visual references are not claimed to be converted native templates.

```text
Use $open-kimi-ppt to create a liquid-glass-style deck about the history of Apple.
```

**Example: Xiaomi YU7 (~8 pages, images as backgrounds)**

```text
Use $open-kimi-ppt to create a Xiaomi YU7 intro PPT, with images as backgrounds from the web, about 8 pages.
```

[![WorkBuddy generating Xiaomi YU7 PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-workbuddy-yu7.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-workbuddy-yu7.png)

**Example: iPhone 17 Pro (~8 pages)**

```text
Use $open-kimi-ppt to create an iPhone 17 Pro intro PPT.
```

[![iPhone 17 Pro](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-iphone-17pro.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-iphone-17pro.png)

**Example: on-slide element animations (live presentation)**

```text
Use $open-kimi-ppt to create a Xiaomi YU7 intro PPT, with images as backgrounds from the web, about 8 pages.
Require element entrance animations.
```

See the sample deck at [example/xiaomi-yu7-ppt-animation](https://github.com/FeidaWang/Open-Deck-Skill/tree/main/example/xiaomi-yu7-ppt-animation) (PPTD project + PPTX; from the current source root, run `node bin/open-kimi-ppt-skill.js serve` to preview animations).

### Local command-line workflow (P0–P2)

The current source exposes seven allowlisted deck commands that the Node CLI routes to Python. They do not install dependencies implicitly; the P0–P2 quality gate, PPTX sidecar, and local exporter do not launch a browser, call Kimi, or make network requests. The examples below use the CLI from a current source checkout. If an installed CLI lists these commands in `--help`, you may replace `node bin/open-kimi-ppt-skill.js` with `open-deck-skill`. Set `OPEN_DECK_PYTHON` to an explicit Python 3 executable when auto-detection is unsuitable.

| Command | Purpose |
| --- | --- |
| `lint` | Check PPTD and emit a full report or compact receipt bound to the manifest, pages, `deck.meta.json`, `media/sources.json`, and local-media bytes |
| `export-local` | Accept only a current, complete quality report that also passes at `warning`, then export the supported PPTD subset to editable PPTX offline |
| `inspect-pptx` / `validate-pptx` | Inventory an existing PPTX read-only, or validate ZIP, OPC, XML, CRC, and relationships |
| `fill-pptx` | Replace existing plain-text shapes or an existing notes body from a JSON plan, validate the candidate, and preserve slide-timing fingerprints |
| `audit-assets` | Audit a user-supplied optional P3 asset pack offline; it does not download or admit assets automatically |
| `audit-prompts` | Check Skill prompt/context budgets, reference integrity, and duplication |

The strict local-export sequence is shown below. Generate the quality report only after every source input is final. Any change to a bound file requires a new `lint` run; do not reuse or hand-edit the old report.

```bash
node bin/open-kimi-ppt-skill.js lint ./deck/deck.pptd \
  --fail-on warning \
  --output ./deck/quality-report.json

node bin/open-kimi-ppt-skill.js export-local ./deck/deck.pptd \
  --quality-report ./deck/quality-report.json \
  --output ./deck/deck.pptx

node bin/open-kimi-ppt-skill.js validate-pptx ./deck/deck.pptx --pretty
```

The local DrawingML backend supports solid slide backgrounds, plain text, basic preset shapes, two-point straight lines and selected arrowheads, local PNG/JPEG, plain notes, and safe `none` / `fade` / `push` / `wipe` transitions. Tables, charts, icons, custom paths, remote images, GIF/SVG, element animation, gradients, morph, and other unsupported semantics fail closed rather than being silently dropped, rasterized, or substituted.

For a narrow existing-PPTX fill, inspect first, create a plan with one-based slide numbers, and always write to a different output path:

```json
{
  "replacements": [
    {"slide": 1, "shape_id": 2, "text": "New title", "lang": "en-AU"},
    {"slide": 2, "shape_name": "Subtitle 3", "text": "Plain text"}
  ],
  "notes": [
    {"slide": 1, "text": "Updated speaker notes", "lang": "en-AU"}
  ]
}
```

```bash
node bin/open-kimi-ppt-skill.js inspect-pptx ./source.pptx --pretty
node bin/open-kimi-ppt-skill.js fill-pptx ./source.pptx ./fill-plan.json \
  --output ./filled.pptx --pretty
node bin/open-kimi-ppt-skill.js validate-pptx ./filled.pptx --pretty
```

Each replacement must select its target with exactly one of `shape_id` or `shape_name`. The sidecar creates neither objects nor notes parts and does not edit tables, charts, pictures, animations, transitions, or arbitrary OOXML. It is neither a PPTX-to-PPTD importer nor a general PowerPoint editor. See [project metadata](skills/open-kimi-ppt/reference/deck-metadata.md) for `deck.meta.json`, and [optional asset packs](skills/open-kimi-ppt/reference/asset-packs.md) for P3 admission rules and the audit command.

### Edit online and export manually

Prefer asking your agent to start the local editor, for example:

```text
From the current source root, run node bin/open-kimi-ppt-skill.js serve for me.
```

Or run it yourself in a terminal:

```bash
node bin/open-kimi-ppt-skill.js serve
```

Then open <http://127.0.0.1:55173/> and choose a complete project folder containing the `.pptd` manifest, `pages/`, and `media/` to view, edit, and export PPTX in the browser. The repository's [example/dji-pocket4](https://github.com/FeidaWang/Open-Deck-Skill/tree/main/example/dji-pocket4) project — a complete 18-page deck — is ready to open for a quick tour.

To establish the minimum file capability before reading any page, the local editor uses a restricted YAML subset for the **`.pptd` manifest**: it requires exactly one top-level `pages` array of string paths and rejects block scalars, anchors/aliases, tags, merge keys, complex keys, and duplicate keys. Individual `.page` files still use the full PPTD page format consumed by the writer. Rewrite an existing manifest's advanced YAML constructs into equivalent simple keys and values before opening it locally.

```bash
# Open the browser after startup
node bin/open-kimi-ppt-skill.js serve --open

# Use another port
node bin/open-kimi-ppt-skill.js serve --port 56000
```

These commands use the repository CLI. A Skill-only copy under `~/.agents/skills` does not include the top-level CLI. Public-package users may instead use `npx open-deck-skill@latest serve`; alternatively, run `npm install --global .` in an audited source checkout and then use `open-deck-skill serve`.

Writable folder access requires a Chromium-based browser with the File System Access API. Other browsers fall back to read-only folder upload. Press `Ctrl+C` to stop the server.

### Windows: a persistent debug browser

On Windows, exporting PPTX or page-QA images automatically starts a **persistent debug browser**. This is by design, not a stray process:

- **Why it's needed**: agent-browser cannot launch Chrome by itself on Windows (the Chrome launcher hands off to a child process and exits immediately, which is misread as a crash), so the export drives an externally started browser instead.
- **What it is**: your installed Chrome (falling back to Edge), using a dedicated profile at `%TEMP%\okp-cdp-profile`, a free debugging port selected by Chrome, and an off-screen window. The exporter reuses an instance only when that profile's ownership marker and `DevToolsActivePort` jointly verify it; it never takes over an unknown browser merely because a common port such as `9337` is alive.
- **Why it persists**: the instance intentionally keeps running after export, and later exports normally reuse it instead of piling up processes. This is not a cross-process singleton lock; do not run two exporters concurrently against the same dedicated profile. To remove it, kill the browser process; the next export starts a fresh one.
- **Take full control**: start your own browser with `--remote-debugging-port=<port>` and set the `AGENT_BROWSER_CDP` environment variable to that port; the script prefers your instance.

macOS and Linux are unaffected.

### Signed-in session for PPTX export

The current public writer may require a Kimi login session to obtain its PPTX signature. Use a dedicated browser profile for export rather than allowing the script to inspect your everyday Chrome profile:

```bash
export AGENT_BROWSER_PROFILE="$HOME/.open-kimi-ppt-browser"
agent-browser --profile "$AGENT_BROWSER_PROFILE" open https://www.kimi.com
```

On Windows, agent-browser cannot reliably launch the profile for this pre-login step; explicitly start a dedicated debug browser instead. PowerShell example (adjust the Chrome path, or use Edge):

```powershell
$env:AGENT_BROWSER_PROFILE = "$env:USERPROFILE\.open-kimi-ppt-browser"
$env:AGENT_BROWSER_CDP = "9223"
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --user-data-dir="$env:AGENT_BROWSER_PROFILE" --remote-debugging-port=$env:AGENT_BROWSER_CDP https://www.kimi.com
```

Windows Command Prompt:

```bat
set "AGENT_BROWSER_PROFILE=%USERPROFILE%\.open-kimi-ppt-browser"
set "AGENT_BROWSER_CDP=9223"
"%ProgramFiles%\Google\Chrome\Application\chrome.exe" --user-data-dir="%AGENT_BROWSER_PROFILE%" --remote-debugging-port=%AGENT_BROWSER_CDP% https://www.kimi.com
```

Sign in manually in the opened browser. On macOS/Linux you may then close it and retain the profile variable; on Windows keep that debug browser running and retain both `AGENT_BROWSER_PROFILE` and `AGENT_BROWSER_CDP` while running `export_pptx.py`. The Windows profile must be an absolute dedicated user-data directory; the exporter rejects everyday `Default`, `Profile N`, and `User Data` roots. Profile directories and state files contain credentials: restrict their permissions, never commit them, and never print them in logs.

## Features

- PPTD generation: let your agent generate complete, editable PPTD projects, from scratch, with style transfer, PPTD template reuse, or replication from images/PDFs.
- Quality and provenance: export is gated at warning severity and binds `deck.meta.json`, `media/sources.json`, pages, and local-media hashes; any source change invalidates the old report.
- PPTX sidecar: inspect/validate an existing PPTX and conservatively replace existing plain text or an existing notes body. This is neither a PPTX-to-PPTD importer nor a general PowerPoint editor.
- Preset themes: 30 bundled design-system presets you can name to apply; full list with previews in [theme_EN.md](theme_EN.md).
- Element animations: off by default. Add `Require element entrance animations` to the prompt and the agent picks suitable on-slide effects per page.
- PPTX generation: the supported PPTD subset uses the offline DrawingML backend by default; advanced semantics use the public-writer fallback only after a local fail-closed rejection and user authorization. The remote writer requests font embedding, while results report only actual font parts.
- Visual QA: when the active agent supports image input, the skill workflow requires it to run `export_images.py`, export every page, stitch an overview, and then inspect, fix, and re-check distortion, occlusion, bounds, contrast, layout, and overflow before PPTX export. The script provides deterministic rendering and page mapping; it does not judge design quality or enforce the workflow by itself. Without image input, the agent can perform structural review only and must disclose that image QA was skipped.
- Online editing: view and edit local PPTD projects in a browser, with autosave and configurable slide transitions.
- Manual export: export PPTX manually from the editor at any time.
- Native PPTD editing: continue editing complete PPTD projects. PPTX-only input may be inspected, conservatively filled, or used as a recreation reference; this package does not provide PPTX-to-PPTD import.
- Secure by design: local editing only reads and writes project directories explicitly authorized by the user.

## Why open-kimi-ppt

Most PPT skills fall into three buckets: assemble OOXML / pptxgenjs in code, render each slide as a full-bleed image, or ship a swipeable HTML deck. open-kimi-ppt takes a different path: a PPTD intermediate layer plus real editable PPTX output, meant to be easy for agents to write and still editable in PowerPoint.

| | open-kimi-ppt | Code-built PPTX (e.g. pptxgenjs) | Full-slide image PPT | Web HTML PPT |
| --- | --- | --- | --- | --- |
| Deliverable | PPTD project + PPTX | Usually PPTX only | Usually PPTX only | Single HTML file |
| Agent-friendly | Clear per-page YAML | Lots of coordinates/API detail | Depends on image models & prompts | Strong HTML/CSS template constraints |
| Editable in PowerPoint | Example artifacts keep text, shapes, and images editable; the eight-slide animation sample passed a PowerPoint open/save/render round-trip | Editable, but hard to refine later | Flat bitmaps — hard to reword | Not native PPTX |
| Visual quality | Real layouts + agent QA when image input is available | Relies on agent layout tuning | Cohesive, poster-like | Strong motion; great for live demos |
| Re-editing | Browser visual editor + autosave | Mostly re-run code | Usually regenerate images | Edit HTML source |
| Best for | Formal PPTX you still need to tweak | Structured reports / template fills | Visually unified poster decks | In-browser talks / launches |

Specifically:

- PPTD describes theme, layout, and elements in YAML, which is more stable than raw OOXML / pptxgenjs and easier to edit locally than full-slide images.
- The default delivery is the complete PPTD project, a source-bound quality report, and the matching PPTX. Unsupported local semantics are rejected explicitly; if the remote fallback also fails, delivery is reported as partial.
- Add `Require element entrance animations` to the prompt and the agent chooses effects and timing for you.
- The repository animation sample completed a real PowerPoint round-trip: editable DrawingML shapes, on-slide animation, and fade survived on all eight slides, and the re-render had no overflow. The live writer signature still returned HTTP 401 in this audit, so no new writer artifact was available and the sample round-trip must not be presented as proof that the current external service passed.
- You can preview, tweak, set transitions, and re-export in the browser without rerunning the whole agent flow.
- When an image-capable agent follows the skill workflow, it uses full-page screenshots plus an overview sheet to catch occlusion, overflow, contrast, and layout issues before export. This is agent orchestration, not an automatic aesthetic scorer inside `export_images.py`.
- It is not locked to the official model, so it costs less. Unlike official Kimi Slides, you can run this in any compatible agent with cheaper models such as DeepSeek. Even without multimodal vision, a model that follows the PPTD spec can still produce decent decks; with a multimodal model you additionally get the visual QA pass.

[![DeepSeek generating a Liquid Glass-style PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-deepseek-liquid-glass.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-deepseek-liquid-glass.png)

*Above: an Apple Liquid Glass-style deck generated with DeepSeek-V4-Flash in WorkBuddy.*

[![Reasonix + DeepSeek generating DJI Pocket 4 Pro PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-reasonix-deepseek.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-reasonix-deepseek.png)

*Above: a DJI Pocket 4 Pro deck generated with DeepSeek-V4-Flash in Reasonix.*

[![ChatGPT / Codex with 5.6 Luna generating an iPhone 17 Pro PPT](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-codex-iphone17pro.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/example-codex-iphone17pro.png)

*Above: an iPhone 17 Pro deck generated with the 5.6 Luna model in ChatGPT / Codex.*

### Style and themes

By default the agent **does not** auto-apply a fixed theme: without a style cue it follows the scenario guides. The skill ships 30 design-system presets, used **only when you name one** (e.g. “use pine-green-strategy”).

Browse theme IDs, descriptions, and preview images in **[theme_EN.md](theme_EN.md)**.

> [!TIP]
> It helps to state a PPT style in the prompt, name a preset, provide a complete PPTD template, or attach inspectable PPT / PPTX pages as visual references. With a style constraint or reference to follow, output is noticeably more consistent. Topic-only prompts leave the agent to invent a look, so results vary more.

Common approaches:

1. **Describe the style in the prompt** — e.g. dark tech, magazine layout, Apple liquid glass, minimal big-type poster slides;
2. **Name a preset theme** — e.g. “use `pine-green-strategy`”; see the catalog in [theme_EN.md](theme_EN.md);
3. **Provide a reference** — upload a complete PPTD template for native reuse; use an existing PPT / PPTX / screenshot only as an inspectable visual reference for transferring colors, layout, and overall style.

You can combine these: lock the look with a preset or template, then add one line about the style you want to emphasize.

## Screenshots

| Edit PPTD online | Export PPTX |
| :---: | :---: |
| [![Edit PPTD online](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/editor-overview.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/editor-overview.png) | [![Export PPTX](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/export-pptx.png)](https://raw.githubusercontent.com/FeidaWang/Open-Deck-Skill/main/docs/images/export-pptx.png) |

## What is PPTD

PPTD is a YAML-based presentation DSL — a simplified abstraction layer over OOXML. It preserves the essentials (theme, page layout, element positions) while dropping complex nesting such as Masters; every page is self-contained — what you see is what you get. See [reference/pptd.md](skills/open-kimi-ppt/reference/pptd.md) for the complete definition.

A complete PPTD project looks like this:

```text
deck/
  deck.pptd             # manifest
  deck.meta.json        # optional audience, intent, language, rhythm, app, and transition lock
  quality-report.json   # strict source-bound report generated after final source changes
  pages/                # one .page file per slide
  media/
    sources.json        # source, license, SHA-256, and derivation records for local media
    *                   # local media assets (if any)
  deck.pptx             # PPTX the workflow attempts by default
```

## How it works and security boundaries

- The CLI serves static files on `127.0.0.1` only and does not listen on LAN interfaces.
- The browser reads a complete PPTD project directory only after explicit user authorization.
- Save callbacks may only modify `.pptd` and `.page` files; absolute paths and `..` traversal are rejected.
- Writable multi-file saves first persist a private recovery journal in the project root. The host stores an opaque project-scope mapping to the directory handle in IndexedDB, serializes tabs for the same directory and browser origin with Web Locks, and keeps transaction ID, exact journal byte length, and SHA-256 evidence in a separate `localStorage` key for that scope; writes fail closed when those browser capabilities are unavailable. Files commit in the fixed order “new pages → manifest → obsolete-page deletes.” An interruption while `armed` restores the complete old version only when journal and authorization match exactly; after all deck writes are confirmed, the transaction first becomes `committed`, so an interruption during cleanup preserves the complete new version and only finishes journal cleanup. Forged, tampered, cross-project, unauthorized, or externally conflicting journals fail closed before any deck file is changed; read-only opening cannot bypass a pending recovery.
- Autosave uses stable snapshots and SHA-256 before and after writes to detect external changes; a conflict or uncertain commit freezes further saves until an explicit reload. The browser File System Access API cannot provide a truly atomic cross-process CAS against native editors, so do not edit the same PPTD files in two programs at once.
- On POSIX, the Python exporters constrain project input and publication with persistent directory handles, component-wise `openat`/`O_NOFOLLOW`, and descriptor-relative operations. On Windows they retain a directory `HANDLE`, verify each opened input's final handle remains beneath the anchored root, and perform kernel no-replace rename relative to the destination `RootDirectory`. Platforms without reliable `dirfd` or Windows HANDLE capabilities do not fall back to path-only publication; they fail closed.
- The local host passes PPTD content to code running in the public Kimi web editor, which itself crosses a network trust boundary. Remote images, fonts, and editor resources may also be fetched from their respective servers. Do not use this flow for confidential or regulated content unless the user authorizes it and Kimi/Moonshot's data-handling policy is acceptable.
- This project does not provide, inject, print, or export Kimi login tokens, and its exporter does not intentionally enumerate or open private Kimi documents. If the user explicitly selects `AGENT_BROWSER_PROFILE` / `AGENT_BROWSER_STATE`, that session is handed to the public Kimi page and writer-signature flow. Same-origin site code still has the authority granted to that signed-in session; the exporter cannot cryptographically restrict its cookies to one endpoint. Use a dedicated least-privilege account/profile and do not reuse a daily browser session that contains sensitive private decks.

## Compatibility

This is a compatibility host for the current public implementation, not a stable official SDK. Updates to Kimi frontend asset hashes, the PPTD format, or the iframe/RPC protocol may require a corresponding project update. Successfully generating a PPTX does not guarantee identical animation playback in PowerPoint, WPS, and Keynote.

## Local development

`npm test` runs both the Node and Python suites. CI additionally performs an npm package dry-run and covers Node 18 and 24 on Ubuntu, macOS, and Windows; Windows regressions cover path-`stat` versus descriptor-`fstat` file identities and writable-handle `fsync`. Tests assume that the selected Python environment already contains PyYAML, Pillow, and websocket-client. If the default `python3` is not that environment, point the test-only `OPEN_KIMI_PYTHON` variable to the absolute Python executable in an isolated virtual environment. The deck CLI uses the separate `OPEN_DECK_PYTHON` override.

```bash
npm install --global .
OPEN_KIMI_PYTHON=/absolute/path/to/venv/bin/python npm test
npm run pack:check
```

In Windows PowerShell, first run `$env:OPEN_KIMI_PYTHON = "C:\absolute\path\to\venv\Scripts\python.exe"`, then run `npm test`.

## Legal

Kimi, Kimi Slides, and related trademarks belong to their respective owners.

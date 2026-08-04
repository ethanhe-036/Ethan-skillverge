---
name: customize-codex-theme
description: Use when a user wants a Codex desktop theme on Windows or macOS; provides an image/style prompt; imports or migrates an existing skin; asks for raster previews, presets, switching, repair, restore, multiple themes, or asks to check, detect, review, score, or audit an existing theme's visual quality, usability, readability, accessibility, consistency, or interface coverage; needs API-key/proxy raster generation for that theme; or reports theme disappearance, stale icons, blank panels, overlap, low contrast, or incomplete coverage. Do not use for general image generation, terminal color schemes, or editor tmTheme files.
---

# Customize Codex Theme

Create reversible themes outside signed Codex. Leave running tasks untouched without interruption authorization.

## Safety contract

- Run read-only `doctor` before proposing or changing anything.
- Defaults to `prepare-only`: stage the next launch; do not inject, restart, reload, close, or relaunch Codex.
- Restart, reload, or close Codex only after explicit user authorization in the current turn. A confirmed **Codex Themes** selection is an explicit themed-restart request: it may close only the exact official Codex PIDs captured before the confirmation and re-verified immediately before termination.
- “I will restart it later” authorizes preparation only.
- Never edit `config.toml`, official shortcuts, app binaries, `app.asar`, or signatures; never broad-kill Codex processes.
- Never patch installed MSIX/app packages or register a scheduled task/login hook.
- A theme loads only through the themed launcher. Official, taskbar, and internal restart paths remain native. On Windows, the theme-owned launcher opens a visual selector backed only by the validated catalog; optional series/variant grouping and validated local previews are presentation metadata, while state and trust remain keyed by the immutable theme ID. Selecting a series node, closing, or cancelling is a no-op and leaves theme state byte-for-byte unchanged.
- Bind CDP to `127.0.0.1`; verify its owner and renderer. Stop only the recorded injector.
- The sole canonical installation is
  `${CODEX_HOME:-<user-profile>/.codex}/skills/customize-codex-theme`, resolved by the
  portable policy in `skill.manifest.json`. Check it before mutating launchers or runtime
  state. Refuse to run from, copy fixes into, or leave active references to a second
  `customize-codex-theme` directory.
- Treat the theme files on disk, the CSS active in the renderer, and the watcher's cached payload as three independent states. Compare their hashes after a repair; a successful one-shot injection does not prove reload persistence.
- Diagnostic route changes must refuse to start while the composer contains any draft or
  attachment. Never read or retain private draft content merely to make restoration possible.
- On Windows, execute only a validly Authenticode-signed Codex-bundled or `PATH` Node.js runtime. Check the signature before every version or WebSocket probe; never accept an environment-variable runtime override.
- Native Codex selectors belong only to `assets/selectors.json` and the semantic renderer. Theme
  packs should target `data-ct-part` surfaces and tokens; do not teach each pack a new copy of
  Codex DOM topology.
- Keep `header[data-app-shell-application-menu-bar="true"]` transparent. The shared runtime alone
  owns the semantic header's two-layer continuous-canvas material (edge control protection plus
  vertical veil); packs must not target the semantic `header` part. They may tune only the bounded
  `--ct-header-material-*` properties on their theme root. Static audit and live verification must
  enforce the outer transparency, inner material, responsive projection, and opaque accessibility
  fallbacks for every current and future catalog theme.
- The Browser guest `<webview>` is runtime-owned. Theme packs must not target the semantic
  `browser` part, native `webview`, or `data-browser-*` selectors. Only the shared runtime may
  snapshot and restore native Browser foreground, background, and color scheme; visibility,
  opacity, pointer events, and geometry remain app-owned.
- Artwork layout is creatively free; `dual-anchor`, edge focal, centered emblem, texture,
  portrait, bounded hero, or another deliberate raster composition are all valid. The safety
  invariant is narrower: each semantic viewport has one coordinate owner (`workbench` for
  raster themes), while sidebar, header, main, settings, and right panel use semantic
  tint/scrim layers over it. Independent per-panel raster copies and crops are forbidden.
- A settings navigation marker identifies the route, not the paint surface. The semantic
  `settings` owner must be the large settings content root derived from stable structure; a
  navigation button must never own it. Clear nested structural `main-surface` paint while keeping
  settings cards, fields, controls, menus, and dialogs opaque.
- The shared runtime alone owns the large semantic settings material. Its normal canvas veil is
  52% at wide widths, 60% at split widths, and 68% at narrow widths; reduced transparency,
  increased contrast, and forced colors use an opaque native-safe fallback. Artwork packs must
  not set `--ct-settings-tint` or paint the large semantic settings root. Static audit and live
  verification must enforce both visible workbench continuity and protected opaque descendants
  for every current and future catalog theme.
- Before any confirmed Windows themed restart, read [launch-transaction.md](references/launch-transaction.md). Treat confirmation, controlled restart, state selection, injection, and verification as one transaction with explicit rollback boundaries.
- If the recorded port/PID differs from the verified listener or watcher command, report stale runtime state and stop. Never kill an unrecorded process or start a second watcher to compensate.
- On an unknown DOM or missing required anchor, fail closed, keep the native interface, and report `UNSUPPORTED`.
- After a Codex update, an `UNSUPPORTED` doctor result with a verified local renderer automatically
  triggers the read-only native structure probe. It may inspect only selector-relevant structure,
  visibility, counts, geometry, overflow, and media preferences; it must never capture task text,
  titles, URLs, input values, file paths, or HTML. A successful probe can support an exact-build
  `PARTIAL` evidence record only after selector review and hash validation; it can never auto-promote
  a build or theme to `PASS`. `start` and runtime refresh must check the matrix before any close,
  launch, watcher change, or injection.
- Adapter `verify` is an engine gate, not a full-surface `PASS`. A verified injection may update `loadedTheme`, but `loadedTheme` does not mean the theme passed full QA.
- Treat subject/art accuracy and palette usability as independent gates. Artwork hues may guide accents, but they never replace a neutral surface hierarchy, distinct semantic colors, or actual rendered contrast checks.
- Treat `creativeBrief`, `experience`, compiled `layoutPlan`, `aestheticProfile`,
  `compositionProfile`, and `materialGrammar` as one authoring contract. Read
  [composition-authoring.md](references/composition-authoring.md) and
  [aesthetic-direction.md](references/aesthetic-direction.md) before new or materially
  revised visual work. Its per-axis judgments are advisory; never combine them into a
  beauty score or let them override behavior, accessibility, or runtime gates.
- Treat [theme-governance.md](references/theme-governance.md) as the production and
  open-source boundary. Canonical presets are registry-hashed; external packs are untrusted
  until an approved Ed25519 publisher signature verifies; user-local copyrighted art remains
  distribution-excluded even when its theme code is reusable.
- Treat [workbench usability](references/workbench-usability.md) as a release gate. Preserve native typography, geometry, density, interaction, and task completion; visual preference never overrides a usability failure.
- Before importing or porting an existing skin, read [theme-migration.md](references/theme-migration.md). Require a preserve/translate/drop migration ledger and native-versus-themed computed-style differential; copied CSS or screenshot similarity is not migration evidence.

## Workflow

Before first guidance, read [onboarding.md](references/onboarding.md); default to Simplified Chinese unless the user requested another language.

### Natural-language quality audit

When the user selects this Skill or says “检测主题质量”, “检查这个主题”, “帮我
review 主题”, “这个主题做得怎么样”, or an equivalent request, treat it as a
read-only quality-audit request:

- Do not ask the user to open a terminal, copy a command, understand contract
  versions, or collect raw JSON. Commands documented below are agent
  implementation details.
- Resolve the target from an explicit theme name/path, attached pack, or verified
  `loadedTheme`. If no single target is identifiable, audit all validated
  installed themes and say which target assumption was used.
- Run `doctor`, including its automatic read-only compatibility probe when a newly updated Codex
  build is outside the matrix, then run the runtime/catalog contracts and summarized deterministic
  quality audit automatically. When the requested theme is currently loaded and
  the exact renderer is verified, also run read-only adapter verification; never
  click, switch, refresh, restart, or mutate theme files merely to complete an
  audit.
- Return a concise user-facing report: overall status, strongest qualities,
  blocking errors, important warnings, affected surfaces, contrast/art-signal
  evidence, declared design thesis, hierarchy/composition/material risks, and the
  safest next action. Translate codes into plain language; keep hard usability
  failures separate from advisory aesthetic findings, and include raw commands
  or JSON only when the user asks for developer details.
- Detection does not authorize repair. Offer a prioritized repair plan, but edit,
  apply, switch, or restart only when the user separately asks for those actions.

1. **Choose input:** accept a user image, text/style prompt, recommendation request, or named built-in theme. For ordinary users, ask at most three creative questions: the desired feeling, the first visual signature they want noticed, and how much artwork should remain visible while working. Do not ask them for schema fields or commands. Read [composition-authoring.md](references/composition-authoring.md), [workbench-usability.md](references/workbench-usability.md), [palette-design.md](references/palette-design.md), and [aesthetic-direction.md](references/aesthetic-direction.md), then compile those answers into the detailed authoring contract, topology-specific `layoutPlan`, executable background scope, responsive policy, and native-safe runtime projection. Keep subject identity separate from the functional UI palette and native workbench constraints. Only load and show the preset catalog when the user asks to browse, try, or preview built-in themes; then read [theme-packs.md](references/theme-packs.md). When generated raster previews or artwork are requested, read [image-generation.md](references/image-generation.md), route between built-in `image_gen` and the portable `$image2-generate` proxy method by actual capability, and produce real image files. Never substitute HTML/SVG for a requested image preview. Show three previews unless the user already chose a named preset; their palette strategies and composition topologies must materially differ, not only hue, crop, or pose, and each must demonstrate the same representative Codex scenes. Redact private screenshots and record copyright/licence/provenance.
2. **Confirm direction:** summarize the three ordinary-language answers first, then internally confirm the chosen `experience.mode`, `artTopology`, background scope, reading strategy, responsive policy, decor budget, palette strategy, light/dark/adaptive mode, design thesis, anti-goals, material grammar, focus hierarchy, quiet zones, neutral surface ladder, semantic colors, actual-surface contrast risks, native typography/geometry boundaries, `prepare-only` versus later authorized apply, and rollback through **Codex Original**. Density and motif counts are maximum budgets, not a checklist that forces decoration. Complete the direction review checkpoint before implementation.
3. **Prepare:** for any imported or legacy skin, first complete [theme-migration.md](references/theme-migration.md) and keep pack-specific rules out of shared CSS. Read [upstream-provenance.md](references/upstream-provenance.md) before changing the runtime contract, and [theme-governance.md](references/theme-governance.md) before registering, signing, distributing, or accepting a pack. Compile the authoring intent with `scripts/theme-tool.mjs compile`; schema-v1 packs are inferred in memory and migrate only to a new output file. Then run `node scripts/contract-tool.mjs`, `node scripts/theme-tool.mjs audit --catalog assets/presets`, `node scripts/theme-tool.mjs registry --catalog assets/presets`, and `node scripts/theme-quality.mjs audit --catalog assets/presets --theme <theme-id> --summary`; deterministic quality errors block staging and warnings keep QA `PARTIAL`. Require the selector contract, surface matrix, compatibility matrix, trust registry, shared base CSS, every declared PNG/JPEG/WebP dimension to stay within governed limits, the compiled renderer payload to parse, and every pack to pass before live work. New schema-v2 packs must declare `creativeBrief` and `experience` in addition to the design profiles; artwork packs must produce a topology-specific `layoutPlan`, compatible focal/quiet zones and crop behavior, executable route/panel scope, monotonic wide/split/narrow intent, and exactly one semantic coordinate owner. Complete the contract and implementation review checkpoints before live work. Atomically maintain v2 state, including launch history and a staged rollback; selection never implies loading. On request, create upgrade-stable **Codex Themes** and **Codex Original** launchers without overwriting unknown entries. Preserve **Codex Original** byte-for-byte. Report paths/state and state that Codex was not restarted.
4. **Apply/switch:** re-run `doctor` and require the compatibility result to be `PASS` or explicitly reported `PARTIAL`; `UNSUPPORTED` fails closed. If a verified renderer is already available, let doctor run the structure-only compatibility probe automatically, translate its result for the user, and—when it passes—review the new stable anchors, update only `assets/selectors.json`, retain the privacy-safe evidence under `assets/compatibility-evidence`, bind its SHA-256 and selector hash to one exact build, and re-run contracts before apply. Never broaden a family range or reuse evidence after the selector hash changes. On a confirmed **Codex Themes** selection, acquire the shared per-user theme-state/start mutex before displaying the visual selector, show current `nextLaunchTheme` separately from verified `loadedTheme`, and keep the same lock through selection and launch. Windows `prepare`, `switch`, `rollback`, and `restore` use that same mutex so they cannot race a confirmed selection. `rollback` only stages a trusted previous theme and never restarts by itself. Cancelling makes no state change. Before showing the selector or closing Codex, the adapter enforces the compatibility gate again. After a valid confirmation but before `select`, Windows performs a controlled restart: request graceful close for the exact Store Codex PIDs captured at confirmation, re-verify executable path and start time, force-stop only remaining captured PIDs, and wait for their loopback endpoint to disappear. A PID that changed identity is never terminated. Then write `select`, launch, verify loopback ownership and renderer anchors, inject, and run `verify` before changing `loadedTheme`. On failure, remove partial injection and use **Codex Original**. Switching changes the next-launch state only; do not promise hot switching. Treat any implementation-specific hot reapply as transient until the active watcher payload and a renderer reload both verify the new hash.
5. **Verify:** after first apply, update, new pack, or defect, execute [workbench-usability.md](references/workbench-usability.md), [theme-quality.md](references/theme-quality.md), [composition-authoring.md](references/composition-authoring.md), [aesthetic-direction.md](references/aesthetic-direction.md), and [surface-qa.md](references/surface-qa.md). Re-run compile, registry, diagnostics, and quality audits after every palette, artwork, composition, material, capability, or renderer change; inspect coordinate ownership, critical reading protection, decoration interaction risk, OKLCH ladder, semantic distance, motif/material budget, focal/quiet-zone, edge-density, image-composited contrast, and art-signal evidence instead of relying on token ratios alone. Review each aesthetic axis separately and report the strongest quality, single biggest weakness, and prioritized adjustment; never produce an aggregate beauty score. Capture privacy-safe DOM fixtures with `scripts/capture-dom-fixture.mjs --state <state> --viewport <viewport> --output <file>` for every changed matrix cell, then run `scripts/fixture-tool.mjs audit --theme <theme-id>`; its per-theme state × viewport coverage report is the source of truth for structural evidence, and fixtures from another theme never fill its cells. Every fixture must carry the exact live payload and style revisions and is rejected as stale when CSS, artwork, theme metadata, selector data, engine version, or renderer template changes. Adapter verification must pass semantic-map, outer-header transparency, semantic-header material, semantic-settings continuity/material, native Browser guest-paint isolation, and composition gates: a `backgroundImage` property alone is never evidence that art is visible. The Browser host may inherit only its native foreground, background, and color scheme; theme rules must never override its app-owned visibility, opacity, pointer events, or geometry. Raster themes require exactly one semantic coordinate owner, no repeated panel art or independent panel crop, and no opaque descendant covering more than the contract threshold. Complete the visual and release review checkpoints before calling the work done. Offline review, rejected fixtures, missing matrix cells, or incomplete task QA cannot produce `PASS`; report `PARTIAL` with every unverified surface. Use `PASS`, `PARTIAL`, `UNSUPPORTED`, or `ROLLED_BACK` exactly as defined there.
6. **Repair/restore:** for disappearance, blank surfaces, contrast, overlap, halos, pet corruption, migration regressions, or launcher/icon failures, follow [repair.md](references/repair.md); for imported themes also re-run [theme-migration.md](references/theme-migration.md). `restore` removes injection only from a verified renderer, stops only the matching injector, clears loaded/next state, preserves `previousTheme` and packs, then verifies **Codex Original**.

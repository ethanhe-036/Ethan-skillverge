---
name: customize-codex-theme
description: Use when a user wants a Codex desktop theme on Windows or macOS; provides an image/style prompt; asks for presets, previews, switching, repair, restore, or multiple themes; or reports theme disappearance, stale icons, blank panels, overlap, low contrast, or incomplete coverage. Do not use for terminal color schemes or editor tmTheme files.
---

# Customize Codex Theme

Create reversible themes outside signed Codex. Leave running tasks untouched without interruption authorization.

## Safety contract

- Run read-only `doctor` before proposing or changing anything.
- Defaults to `prepare-only`: stage the next launch; do not inject, restart, reload, close, or relaunch Codex.
- Restart, reload, or close Codex only after explicit user authorization in the current turn.
- “I will restart it later” authorizes preparation only.
- Never edit `config.toml`, official shortcuts, app binaries, `app.asar`, or signatures; never broad-kill Codex processes.
- Never patch installed MSIX/app packages or register a scheduled task/login hook.
- A theme loads only through the themed launcher. Official, taskbar, and internal restart paths remain native.
- Bind CDP to `127.0.0.1`; verify its owner and renderer. Stop only the recorded injector.
- Treat the theme files on disk, the CSS active in the renderer, and the watcher's cached payload as three independent states. Compare their hashes after a repair; a successful one-shot injection does not prove reload persistence.
- If the recorded port/PID differs from the verified listener or watcher command, report stale runtime state and stop. Never kill an unrecorded process or start a second watcher to compensate.
- On an unknown DOM or missing required anchor, fail closed, keep the native interface, and report `UNSUPPORTED`.
- Adapter `verify` is an engine gate, not a full-surface `PASS`. A verified injection may update `loadedTheme`, but `loadedTheme` does not mean the theme passed full QA.

## Workflow

Before first guidance, read [onboarding.md](references/onboarding.md); default to Simplified Chinese unless the user requested another language.

1. **Choose input:** accept a user image, text/style prompt, recommendation request, or named built-in theme. Only load and show the preset catalog when the user asks to browse, try, or preview built-in themes; then read [theme-packs.md](references/theme-packs.md). Show three previews unless the user already chose a named preset. Redact private screenshots and record copyright/licence/provenance.
2. **Confirm direction:** mode, artwork/crop at wide/split/narrow/high-DPI sizes, surface treatment, accessibility, native UI boundaries, `prepare-only` versus later authorized apply, and rollback through **Codex Original**.
3. **Prepare:** validate with `scripts/theme-tool.mjs`, then read [platforms.md](references/platforms.md) and use the detected adapter. Atomically maintain `selectedTheme`, `nextLaunchTheme`, `loadedTheme`, and `previousTheme`; selection never implies loading. On request, create upgrade-stable **Codex Themes** and **Codex Original** launchers without overwriting existing entries. Report paths/state and state that Codex was not restarted.
4. **Apply/switch:** re-run `doctor`. After separate authorization and a graceful user exit, launch through **Codex Themes**, verify loopback ownership and renderer anchors, inject, then run `verify` before changing `loadedTheme`. On failure, remove partial injection and use **Codex Original**. Switching changes the next-launch state only; do not promise hot switching. Treat any implementation-specific hot reapply as transient until the active watcher payload and a renderer reload both verify the new hash.
5. **Verify:** after first apply, update, new pack, or defect, execute [surface-qa.md](references/surface-qa.md). Adapter verification proves active injection only. Offline review or incomplete surface QA cannot produce `PASS`; report `PARTIAL` with every unverified surface. Use `PASS`, `PARTIAL`, `UNSUPPORTED`, or `ROLLED_BACK` exactly as defined there.
6. **Repair/restore:** for disappearance, blank surfaces, contrast, overlap, halos, pet corruption, or launcher/icon failures, follow [repair.md](references/repair.md). `restore` removes injection only from a verified renderer, stops only the matching injector, clears loaded/next state, preserves `previousTheme` and packs, then verifies **Codex Original**.

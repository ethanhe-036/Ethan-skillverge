---
name: customize-codex-theme
description: Design, preview, prepare, apply, switch, audit, repair, or restore full Codex desktop themes on Windows and macOS. Use when a user provides an image or style prompt, asks for recommendations or built-in presets, wants a safe custom Codex launcher or multiple themes, reports that a theme disappeared after restart, or needs full-surface theme QA. Do not use for terminal-only color schemes or editor tmTheme files.
---

# Customize Codex Theme

Create reversible, launcher-based Codex desktop themes without modifying the signed app, `app.asar`, application signatures, or the user's Codex configuration. Keep a running Codex task untouched until the user explicitly authorizes interruption.

## Safety contract

- Run a read-only `doctor` before proposing or changing anything. Detect the operating system, Codex install/channel/version, running state, loopback debugging state, runtime availability, current theme state, and existing launchers.
- Defaults to `prepare-only`: validate and stage the selected pack, record it as the next-launch choice, and stop. Do not inject, restart, reload, close, or relaunch Codex.
- Restart, reload, or close Codex only after explicit user authorization in the current turn.
- Treat “I will restart it myself later” as permission to prepare only, never as permission to restart now.
- Never edit `config.toml`, the official Codex shortcut, app binaries, `app.asar`, or signatures. Never terminate Codex by a broad process-name kill.
- Bind Chrome DevTools Protocol access to `127.0.0.1` only. Confirm the endpoint belongs to Codex before injecting and stop only an injector whose recorded PID, command, and start time match.
- On an unknown DOM or missing required anchor, fail closed, keep the native interface, and report `UNSUPPORTED` with the failed probe. Do not guess selectors or partially theme the page.
- Keep an original-path escape hatch available before applying a theme.

## Choose the input path

Start with one of these four paths. Do not force users through a preset gallery.

1. **User image**: inspect composition, contrast, crop safety, privacy, and usage rights. Redact sensitive information before sharing or generating previews. Produce three treatments that vary crop, density, and palette while preserving recognizability.
2. **Text theme or style prompt**: turn the prompt into three distinct visual directions. State palette, typography character, surface treatment, background strategy, and accessibility trade-offs for each.
3. **Recommendation**: ask only for missing high-impact preferences such as light/dark, restrained/expressive, and whether decorative artwork is acceptable. Then recommend three directions with one concise rationale each.
4. **Built-in theme**: if the user names one preset, load only that preset. Only load and show the preset catalog when the user asks to browse, try, or preview built-in themes. Read [theme-packs.md](references/theme-packs.md) only in that case.

Do not create three alternatives when the user has already selected a specific built-in pack and asks to proceed. Otherwise, present three previews before implementation. A preview may be a generated image, a faithful mockup, or a compact visual specification when image generation is unavailable. Never expose private screenshot content in a preview.

## Confirm one direction

Before preparing files, show the selected direction and confirm:

- light, dark, or adaptive behavior;
- background art and crop at wide, split-pane, narrow, and high-DPI sizes;
- treatment of sidebar, conversation, composer, menus, dialogs, code, diff, terminal, browser, and secondary panels;
- any unsupported native-menu boundary;
- the apply mode: `prepare-only` now, or apply after a separate explicit restart authorization;
- the rollback path through **Codex Original**.

Record copyright, licence, or provenance for supplied and generated assets. Do not redistribute copyrighted characters, logos, fonts, or photographs without permission; offer a style-inspired original instead.

## Prepare the theme

1. Validate the theme pack and assets with `scripts/theme-tool.mjs`.
2. Use the adapter for the detected platform. Read [platforms.md](references/platforms.md) before running a platform command.
3. Record four separate state fields:
   - `selectedTheme`: the user's current choice;
   - `nextLaunchTheme`: the theme requested for the next themed launch;
   - `loadedTheme`: the theme verified in the current renderer;
   - `previousTheme`: the last verified theme, used for recovery and reporting.
4. Write state atomically only after validation. A selection must not claim that a theme is loaded.
5. Offer launcher creation only after explaining it. Create **Codex Themes** for the selected next-launch theme and **Codex Original** for the untouched native launch path. Never replace an existing shortcut with the same name without explicit approval.
6. End a prepare-only run with the exact staged theme, launcher paths, current loaded theme, and the sentence that Codex was not restarted.

## Apply or switch

Apply only after the restart gate is satisfied:

1. Re-run `doctor`; if Codex is busy or the environment changed, stop and ask again.
2. Prefer a user-performed graceful exit. If the user authorizes Codex to close, request a normal close and fail safely if it does not exit; never force-kill it.
3. Launch through **Codex Themes**, verify the loopback endpoint and Codex renderer anchors, inject the selected pack, and keep the injector scoped to that renderer.
4. Run `verify` before setting `loadedTheme`. If verification fails, remove the partial injection, preserve state for diagnosis, and direct the user to **Codex Original**.
5. For theme switching, update `selectedTheme` and `nextLaunchTheme` first. Keep `loadedTheme` unchanged until the new theme is verified after the next authorized restart.

Do not promise hot switching. Use next-launch switching as the reliable default.

## Verify every surface

Read and execute [surface-qa.md](references/surface-qa.md) after a first apply, a Codex update, a new theme pack, or a reported visual defect. Verify both appearance and function; a pretty home page is not sufficient.

Classify the result as:

- `PASS`: required surfaces, controls, and rollback work;
- `PARTIAL`: only an explicitly documented native or optional surface remains native, while all required surfaces pass;
- `UNSUPPORTED`: renderer identity or required anchors cannot be verified;
- `ROLLED_BACK`: themed launch failed and the native path was restored.

Native Windows/macOS menu bars, title-bar controls, file pickers, permission prompts, and OS-owned context menus may remain native. Theme web-rendered menus and popovers, but never obscure the distinction.

## Restore

Run `restore` to remove live injection only from a verified renderer, stop only the recorded matching injector, clear `loadedTheme` and `nextLaunchTheme`, and preserve `previousTheme` for reporting. Keep theme packs unless the user separately asks to delete them. Verify that **Codex Original** opens the untouched native interface.

If a Codex update breaks anchors, do not patch selectors blindly. Capture the version and failed probes, keep the original interface active, then repair the shared renderer detection and re-run the complete QA checklist.

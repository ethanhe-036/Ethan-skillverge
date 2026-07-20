# Theme repair

Use this reference for disappearance after restart/update, incomplete surfaces, low contrast, overlap, white bands/halos, distorted pet windows, or broken launcher icons.

## Root-cause loop

1. Run `doctor`; capture Codex version, actual launch path, selected/next/loaded theme, launcher metadata, and renderer identity.
2. Select the exact CDP target by verified URL and renderer anchors; never use the first `app://` target. Reproduce the exact surface, state, and width. Inspect the actual component root, every opacity-bearing ancestor, representative descendants, pseudo-elements, siblings, gradients, and native fades. Valid selectors use stable `data-*`, role/state attributes, verified ancestry, or a native ARIA relationship such as trigger `aria-controls` to popup ID. Localized visible text or localized `aria-label`, `nth-child`/position, screen coordinates, screenshot geometry, and guessed shortcuts are not stable selectors; if no stable anchor exists, stop with `UNSUPPORTED`.
3. Add the smallest failing automated guard. Fix the narrow shared root cause; never compensate with a broad global selector.
4. Exclude avatar, pet, browser, login, and auxiliary renderers before injection. Re-run the complete surface QA.
5. If the user has not authorized a themed restart, prepare only and report `PARTIAL`; do not claim the live defect is fixed.

If CDP disconnects during diagnosis, re-verify the exact owner, target ID, renderer anchors, and theme hash before restoring the captured UI snapshot. If that identity cannot be recovered, stop automation, report `PARTIAL` with the unrestored fields, and do not connect to another target merely to finish cleanup.

Rollback is required when renderer identity is wrong; permission, destructive, send, or stop controls become unreadable/inoperable; decoration intercepts input; task access regresses; an unexpected listener/config/app mutation appears; or verified cleanup cannot complete. A non-critical decorative defect may remain loaded as `PARTIAL` only when navigation, task access, controls, readability, and rollback remain safe.

## Failure map

| Symptom | Root check | Response |
| --- | --- | --- |
| Theme disappears after restart | Which launcher was used; `nextLaunchTheme` versus `loadedTheme` | Direct the user to **Codex Themes**. Official/taskbar/app-internal restart stays native. |
| Launcher is blank or broken after an update | Target and `IconLocation` contain a versioned package path | Use the stable platform entry and a per-user cached icon; edit only verified theme-owned launchers. |
| Stable icon exists and matches, but Explorer still shows a blank icon | Shell cache is stale | Refresh or notify Windows Shell only; do not rewrite the shortcut or restart Codex. |
| Home is themed but another pane is blank/native | Missing surface or state coverage | Keep `PARTIAL` and run the full matrix. |
| Text is faint | Foreground token versus actual artwork | Derive and validate a stable contrast token with an opaque fallback. |
| Cards overlap | Geometry at the current width | Assert a positive gap and repair the shared layout rule. |
| Composer has a white band or halo | Parent/sibling fade, pseudo-element, gradient, or inset shadow | Inspect outside the composer before changing the composer itself. |
| Settings or a side pane stays native | The surface uses a different stable root | Target that root; do not broaden selectors globally. |
| Rich `role="tooltip"` plan panel is unreadable | Capture computed styles for the root, token-colored descendants, and SVG; the role may also serve small tooltips | Use one surface compatible with both forms, or narrow by a verified stable child anchor; never judge contrast from the root alone. |
| Terminal is white only in one mount, or its glyph grid is misaligned | Compare both mount roots, inherited variables, Codex's official code-font setting, xterm viewport/rows, glyphs, and cursor | Put palette variables on the stable terminal root, not the mount container. Change text size through Codex's official **Code font size** so xterm refits its grid; never resize `.xterm-rows` in CSS. |
| A terminal tab stays narrow after raising `max-width` | Inspect the terminal tab item, shrink-wrapped tablist, computed `flex-basis`, and `max-width` | On a terminal-specific stable root, set both `flex-basis` and `max-width`; do not broaden the rule to all tabs. |
| Utility cards look right but tabs or close buttons change | A broad `aside button` rule leaked across roles | Scope the rule to the launcher rows and verify tabs, selected state, and close controls separately. |
| A launcher menu shows padded strips around otherwise-correct rows | A broad menu/popup rule painted the native outer shell and exposed its layout padding | Link the stable tab-strip trigger to its menu through `aria-controls`; mark only that menu, make the shell transparent/padding-free, keep artwork on its owning large surface, and leave row styling scoped to the rows. |
| A side-panel tab title stays faint after header fixes | The tab strip is a separate app-shell root, or an ancestor controller still applies opacity | Inspect `[data-app-shell-tabs]`, the tab-strip/controller roots, disabled state, nested token-colored spans, SVG, close, and add buttons; set readable tokens at the real root instead of adding more `header` rules. |
| Suggestion circles are centered but all glyphs lean the same direction | The native icon wrapper still applies layout such as `justify-start`; SVG transforms are not the shared cause | Measure container and glyph centers, then center the direct wrapper with flex/grid alignment. Do not replace or hand-offset individual SVGs. |
| A file edit or hot apply looks fixed but reverts after reload | The live watcher cached an older payload, or the renderer CSS hash differs from disk | Compare disk, live style, and watcher hashes; replace only the recorded watcher through the authorized workflow or wait for the next themed launch. Never run two watchers. |
| The recorded port/PID differs from the verified listener or watcher | Runtime state is stale | Report `PARTIAL`/`NOT_READY`; do not kill the unrecorded process. Reconcile through verified recorded identity or an authorized themed restart. |
| Pet/avatar window is distorted | Wrong renderer accepted | Tighten renderer identity and remove partial injection. |

Never broaden main-renderer matching to clean an auxiliary window. Cleanup-only removal is allowed only when the exact CDP target ID, signed Codex owner, recorded injector/session, and theme hash all match; it must not inject or apply styles. Otherwise stop and wait for a normal close or the next **Codex Original** launch.

After a Codex update, capture the new version and failed probe. If anchors changed, keep the native interface active until the adapter and full QA pass again.

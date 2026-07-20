# Full-surface QA

Run this checklist at wide, split-pane, and narrow widths. Test light/dark behavior when the pack claims it, 100% and a high-DPI scale, keyboard-only navigation, and reduced motion. Keep every unredacted screenshot in a temporary directory and delete it in the restoration `finally` block. Preserve only user-approved redacted evidence after removing account names, private tasks, repository names, messages, file paths, tokens, and notification content; never copy those values into logs.

## Renderer identity and resilience

- Confirm the page is the main Codex renderer, not an avatar, pet, browser, login, or auxiliary window.
- Confirm avatar and pet renderers are excluded before injection, even when they expose generic `main`, `header`, or composer-like nodes.
- Confirm required sidebar, main content, and composer anchors before injection.
- Confirm a single active theme marker and a matching theme hash.
- Confirm removing the marker restores native styles without reloading the app.
- Simulate or detect an unknown DOM/missing anchor and confirm fail-closed behavior.

## Shell and navigation

- OS title bar, window controls, menu bar, resize, minimize, maximize, and split-pane controls.
- Codex/ChatGPT product switcher, back/forward controls, top navigation, active task title, overflow, and sidebar toggle.
- Measure active-task, disabled-control, and placeholder contrast against the actual header artwork; do not rely on inherited opacity.
- For app-shell tabs, inspect the tab root, controller wrapper, nested title/icon nodes, close button, add button, selected state, and disabled ancestor opacity. A readable normal task header does not prove side-panel tabs are readable.
- Left sidebar navigation, project groups, selected/hovered tasks, truncated names, badges, scrollbars, collapse controls, account footer, and help button.
- Empty, loading, disabled, offline, update, error, and long-list states.

## Main task surfaces

- New-task home hero, suggested action cards, project picker, and responsive crop; assert the suggested action cards do not overlap the hero or each other at every width.
- Measure each suggestion glyph center against its colored-circle center and each circle against its card. A centered wrapper or unchanged SVG transform alone is not evidence of visual centering.
- Conversation text, user/assistant bubbles, reasoning/progress, citations, links, tables, lists, inline code, code blocks, and selection colors.
- Tool calls, permission requests, approvals, plans, status chips, edited-file summaries, diff additions/deletions, review cards, and error banners.
- Composer empty/focused/typing/disabled/sending/stopping states; attachments, previews, remove buttons, add menu, approval mode, model, reasoning, speed, context meter, microphone, and send/stop controls. Inspect the composer, its parent/sibling fade, pseudo-elements, gradients, inset shadows, and page edge for white halos.

## Secondary panels and overlays

- Files, browser, terminal, side tasks, review/diff, plugins, schedules, pull requests, and any right-hand empty state.
- Mount the terminal in both right-panel and bottom-panel; compare the terminal root, inherited variables, official code-font setting, xterm viewport/rows, glyph/cursor alignment, terminal-tab `flex-basis`/`max-width`, and ANSI readability.
- Settings navigation and content panes together: search, selected row, cards, controls, scroll gutters, headers, and the otherwise-empty right side must share the intended theme.
- Account menu, help menu, settings, model/reasoning/speed menus, product switcher, add/attachment menu, permission menu, tooltips, popovers, dialogs, toasts, context menus, and command palette. For a rich or multi-row `role="tooltip"`, verify representative token-colored descendants and SVG, not only the root.
- For side-panel launchers, inspect the portal shell, owning artwork surface, and action rows separately. Verify the outer shell does not reveal native padding as themed side strips, the artwork remains present, and focus, clipping, and row hit targets remain intact.
- Backdrops, focus traps, clipping, stacking order, shadows, borders, scrollbars, and click-through prevention.
- Avatar/pet windows: remain isolated from the main renderer theme unless a separate compatible pet theme is explicitly selected.

## Readability and accessibility

- Meet WCAG AA contrast for body text and controls; keep focus rings obvious on every interactive item.
- When artwork affects a header or control background, derive one stable contrast token during theme generation, validate it against the worst sampled area, and provide an opaque fallback; do not repeatedly sample pixels at runtime.
- Do not encode success, warning, error, diff, or approval state by color alone.
- Keep code, terminal, diff, permission, and destructive-action surfaces opaque enough for legibility.
- Preserve visible hover, pressed, selected, disabled, checked, and keyboard-focus states.
- Respect reduced motion; avoid continuous decorative animation and parallax.
- Keep all controls reachable and labelled after visual overrides; test zoom and text expansion.

## Background and responsive behavior

- Give side tasks and all large empty panes a deliberate background rather than a flat accidental blank.
- Keep artwork subordinate to text with stable overlays; avoid faces or focal objects behind dense controls.
- Verify `cover`/`contain` decisions across aspect ratios and keep critical artwork inside safe crop zones.
- Disable expensive blur on large scrolling surfaces; constrain translucency to chrome, menus, or small cards and provide an opaque fallback.
- Prevent decorative layers from intercepting clicks or covering content.
- Capture computed style for the failing element and its immediate parent, siblings, and pseudo-elements before changing selectors; a visually white band may come from a native fade outside the themed component.

## Functional regression and rollback

- Before diagnostic interaction, take one restoration snapshot containing the active task and route; window geometry and maximized state; zoom; sidebar and panel open/closed state, width, height, split ratio, and selected tabs; scroll positions; the focused element; open menus/dialogs; and composer draft, attachments, and sending state. Use stable native controls rather than guessed shortcuts. Run diagnostic interaction as a restoration transaction and restore the snapshot in `finally`, including after a failed probe.
- Composer sending state and task progress are volatile observation-only fields: record them for change attribution, but never attempt to restore them. If diagnosis would touch their controls or content, stop interaction and continue with read-only DOM inspection or wait for the user.
- If restoration fails or the exact renderer cannot be re-verified, keep the result `PARTIAL`, list every unrestored state, and ask the user to restore it with native controls. Never restart Codex merely to repair diagnostic state.
- Create and select a task; open/close sidebar and every tested panel.
- Attach and remove files; type and submit only in a disposable task when the user authorizes it.
- Open menus, change a reversible option, and confirm keyboard Escape/Enter behavior.
- Verify no unexpected network listener, config edit, app-file edit, or additional Codex process.
- After any hot reapply, compare the disk pack hash, live style hash, recorded runtime port/PID, and active watcher command; reload once when authorized and confirm the watcher reapplies the same hash.
- Exit via **Codex Themes**, relaunch it, and verify persistence only when restart testing was authorized.
- Launch **Codex Original** and verify the untouched native interface and normal task access.
- Run `restore`, confirm `loadedTheme` and `nextLaunchTheme` are cleared, and confirm the recorded injector is gone.

Report each failed item with surface, state, width, Codex version, expected result, actual result, and screenshot path if privacy-safe. Do not call the theme complete while a required item fails.

## Result classification

- `PASS`: every required runtime surface, control, persistence check, and rollback path passed; adapter `verify` or `loadedTheme` alone is insufficient.
- `PARTIAL`: preparation succeeded but required runtime checks remain unverified, or an explicit defect remains.
- `UNSUPPORTED`: renderer identity or a required anchor cannot be verified; keep the native interface.
- `ROLLED_BACK`: partial injection was removed and the native interface was verified.

# Full-surface QA

Run this checklist at wide, split-pane, and narrow widths. Test light/dark behavior when the pack claims it, 100% and a high-DPI scale, keyboard-only navigation, and reduced motion. Preserve screenshots only after redacting account names, private tasks, repository names, messages, file paths, tokens, and notification content.

## Renderer identity and resilience

- Confirm the page is the main Codex renderer, not an avatar, pet, browser, login, or auxiliary window.
- Confirm required sidebar, main content, and composer anchors before injection.
- Confirm a single active theme marker and a matching theme hash.
- Confirm removing the marker restores native styles without reloading the app.
- Simulate or detect an unknown DOM/missing anchor and confirm fail-closed behavior.

## Shell and navigation

- OS title bar, window controls, menu bar, resize, minimize, maximize, and split-pane controls.
- Codex/ChatGPT product switcher, back/forward controls, top navigation, active task title, overflow, and sidebar toggle.
- Left sidebar navigation, project groups, selected/hovered tasks, truncated names, badges, scrollbars, collapse controls, account footer, and help button.
- Empty, loading, disabled, offline, update, error, and long-list states.

## Main task surfaces

- New-task home hero, suggested action cards, project picker, and responsive crop.
- Conversation text, user/assistant bubbles, reasoning/progress, citations, links, tables, lists, inline code, code blocks, and selection colors.
- Tool calls, permission requests, approvals, plans, status chips, edited-file summaries, diff additions/deletions, review cards, and error banners.
- Composer empty/focused/typing/disabled/sending/stopping states; attachments, previews, remove buttons, add menu, approval mode, model, reasoning, speed, context meter, microphone, and send/stop controls.

## Secondary panels and overlays

- Files, browser, terminal, side tasks, review/diff, plugins, schedules, pull requests, and any right-hand empty state.
- Account menu, help menu, settings, model/reasoning/speed menus, product switcher, add/attachment menu, permission menu, tooltips, popovers, dialogs, toasts, context menus, and command palette.
- Backdrops, focus traps, clipping, stacking order, shadows, borders, scrollbars, and click-through prevention.
- Avatar/pet windows: remain isolated from the main renderer theme unless a separate compatible pet theme is explicitly selected.

## Readability and accessibility

- Meet WCAG AA contrast for body text and controls; keep focus rings obvious on every interactive item.
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

## Functional regression and rollback

- Create and select a task; open/close sidebar and every tested panel.
- Attach and remove files; type and submit only in a disposable task when the user authorizes it.
- Open menus, change a reversible option, and confirm keyboard Escape/Enter behavior.
- Verify no unexpected network listener, config edit, app-file edit, or additional Codex process.
- Exit via **Codex Themes**, relaunch it, and verify persistence only when restart testing was authorized.
- Launch **Codex Original** and verify the untouched native interface and normal task access.
- Run `restore`, confirm `loadedTheme` and `nextLaunchTheme` are cleared, and confirm the recorded injector is gone.

Report each failed item with surface, state, width, Codex version, expected result, actual result, and screenshot path if privacy-safe. Do not call the theme complete while a required item fails.

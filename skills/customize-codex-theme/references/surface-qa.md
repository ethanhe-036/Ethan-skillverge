# Full-surface QA

Run this checklist at wide, split-pane, and an equivalent 320 CSS px narrow width. Test 100%, 200%, and 400% zoom, high DPI, keyboard-only navigation, light/dark behavior when claimed, increased contrast, reduced motion, and reduced transparency. Keep every unredacted screenshot in a temporary directory and delete it in the restoration `finally` block. Preserve only user-approved redacted evidence after removing account names, private tasks, repository names, messages, file paths, tokens, and notification content; never copy those values into logs.

## Renderer identity and resilience

- Confirm the page is the main Codex renderer, not an avatar, pet, browser, login, or auxiliary window.
- Confirm avatar and pet renderers are excluded before injection, even when they expose generic `main`, `header`, or composer-like nodes.
- Confirm required sidebar, main content, and composer anchors before injection.
- After an app update, run the structure-only compatibility probe before changing a selector. A
  passing workbench/sidebar/main probe qualifies only the exact build for `PARTIAL`; missing anchors
  may emit tag/class/role/data-attribute-name fingerprints for selector repair, never values or text.
- Confirm the retained compatibility evidence SHA-256 and selector-contract hash still match before
  treating an exact-build matrix entry as valid.
- Confirm a single active theme marker and a matching theme hash.
- Confirm removing the marker restores native styles without reloading the app.
- Simulate or detect an unknown DOM/missing anchor and confirm fail-closed behavior.

## Shell and navigation

- OS title bar, window controls, menu bar, resize, minimize, maximize, and split-pane controls.
- Codex/ChatGPT product switcher, back/forward controls, top navigation, active task title, overflow, and sidebar toggle.
- Measure active-task, disabled-control, and placeholder contrast against the actual header artwork; do not rely on inherited opacity.
- For app-shell tabs, inspect the tab root, controller wrapper, nested title/icon nodes, close button, add button, selected state, and disabled ancestor opacity. A readable normal task header does not prove side-panel tabs are readable.
- Verify the outer `header[data-app-shell-application-menu-bar="true"]` remains transparent while
  its semantic main-header child has transparent background color, two gradient layers, no hard
  divider/shadow/blur, and the same underlying canvas as the workbench. Under reduced
  transparency, increased contrast, or forced colors, verify the semantic child becomes opaque
  without repainting the normal outer drag layer. If tab computed styles look correct but its
  exact pixels are blank, scan geometrically intersecting stacking layers including
  `pointer-events: none`; supplement computed-style evidence with a privacy-safe aggregate pixel
  sample of the tab rectangle.
- Left sidebar navigation, project groups, selected/hovered tasks, truncated names, badges, scrollbars, collapse controls, account footer, and help button.
- Empty, loading, disabled, offline, update, error, and long-list states.

## Main task surfaces

- New-task home hero, suggested action cards, project picker, and responsive crop; assert the suggested action cards do not overlap the hero or each other at every width.
- Measure each suggestion glyph center against its colored-circle center and each circle against its card. A centered wrapper or unchanged SVG transform alone is not evidence of visual centering.
- Conversation text, user/assistant bubbles, reasoning/progress, citations, links, tables, lists, inline code, code blocks, and selection colors.
- Tool calls, permission requests, approvals, plans, status chips, edited-file summaries, diff additions/deletions, review cards, and error banners.
- Composer empty/focused/typing/disabled/sending/stopping states; attachments, previews, remove buttons, add menu, approval mode, model, reasoning, speed, context meter, microphone, and send/stop controls. Inspect the composer, its parent/sibling fade, pseudo-elements, gradients, inset shadows, and page edge for white halos.

## Secondary panels and overlays

- Files, browser, terminal, side tasks, review/diff, plugins, schedules, pull requests, and any right-hand empty state. Treat the Browser as two renderers: theme only the main-renderer host chrome, keep the guest page uninjected, and verify the host `<webview>` retains its native computed background and color scheme before and after theme application. Never change Browser visibility, opacity, pointer events, or geometry.
- A Browser fixture counts only while its child `<webview>` is visibly active with positive geometry. The always-mounted hidden host must not satisfy the Browser matrix cell.
- `scripts/theme-tool.mjs audit` must reject any theme-pack selector that targets the semantic `browser` part, native `webview`, or `data-browser-*`. The shared base contract must require native Browser foreground/background/color-scheme restoration and reject visibility, opacity, pointer-event, overflow, positioning, transform, stacking, or geometry changes on that guest surface.
- Mount the terminal in both right-panel and bottom-panel; compare the terminal root, inherited variables, xterm viewport/rows, active tab, and ANSI readability.
- Settings navigation and content panes together: search, selected row, cards, controls, scroll gutters, headers, and the otherwise-empty right side must share the intended theme. Treat `[data-settings-panel-slug]` as a route/navigation marker only; the semantic `settings` surface must own the large content root, not one navigation button. In raster themes, nested structural `.main-surface` layers must be transparent to the single workbench coordinate owner while cards, fields, controls, menus, and dialogs remain opaque.
- Account menu, help menu, settings, model/reasoning/speed menus, product switcher, add/attachment menu, permission menu, tooltips, popovers, dialogs, toasts, context menus, and command palette. For a rich or multi-row `role="tooltip"`, verify representative token-colored descendants and SVG, not only the root.
- For side-panel launchers, inspect the portal shell and action rows separately. Verify the outer shell does not reveal native padding as themed side strips while focus, clipping, and row hit targets remain intact.
- Backdrops, focus traps, clipping, stacking order, shadows, borders, scrollbars, and click-through prevention.
- Avatar/pet windows: remain isolated from the main renderer theme unless a separate compatible pet theme is explicitly selected.

## Readability and accessibility

- Meet WCAG AA contrast for body text and controls; keep focus rings obvious on every interactive item.
- Treat controls below 24 by 24 CSS px as an audit signal, not automatic permission to resize native Codex UI. Check the WCAG spacing, equivalent-control, inline, user-agent, and essential exceptions; the theme must preserve native geometry.
- When artwork affects a header or control background, derive one stable contrast token during theme generation, validate it against the worst sampled area, and provide an opaque fallback; do not repeatedly sample pixels at runtime.
- Do not encode success, warning, error, diff, or approval state by color alone.
- Keep code, terminal, diff, permission, and destructive-action surfaces opaque enough for legibility.
- Preserve visible hover, pressed, selected, disabled, checked, and keyboard-focus states.
- Respect reduced motion; avoid continuous decorative animation and parallax.
- Under reduced transparency, remove theme art, blur, and translucent reading layers while preserving opaque native controls.
- On Windows, test all four built-in contrast themes plus customized system colors. Let `Canvas`/`CanvasText`, `ButtonFace`/`ButtonText`, `Highlight`/`HighlightText`, and `LinkText` inherit as intended; do not preserve hard-coded character colors in forced-color mode.
- Simulate protanopia, deuteranopia, tritanopia, and achromatopsia; semantic state must remain distinguishable without hue alone.
- Keep all controls reachable and labelled after visual overrides; test zoom and text expansion.

## Background and responsive behavior

- Run `scripts/contract-tool.mjs` before live QA. Every state and viewport in
  `fixtures/surface-matrix.json` must resolve its required `data-ct-part` surfaces.
- Label each structure fixture with one declared state and viewport using
  `scripts/capture-dom-fixture.mjs --state <state> --viewport <viewport> --output <file>`.
  Run `scripts/fixture-tool.mjs audit --theme <theme-id>` after capture. The tool must reject unknown fields,
  text-bearing attributes, stale contract versions, stale payload/style revisions, mislabeled
  viewport geometry, duplicate matrix cells, truncated trees, inactive themes, and
  missing/cardinality-violating semantic parts. A fixture is valid only for the exact canonical
  CSS, artwork, theme metadata, selector data, engine version, and renderer template that
  produced its live revision. Full structural evidence is the state × viewport cross-product; missing cells keep QA
  `PARTIAL` and must be listed instead of inferred from a nearby route or zoom. Record the
  audit's `evidenceHash` separately from the static Skill `runtimeHash`; adding a validated live
  fixture must not invalidate the installed runtime package. Coverage is isolated per validated
  preset theme; evidence from one theme can never fill another theme's matrix cells.
- For any compiled raster topology, require exactly one direct coordinate owner:
  `workbench`. Header, main, sidebar, settings, and right panel may add gradients
  or translucent colors but may not contain a second `url(...)`,
  `--ct-user-art`, or `--ct-workbench-art` background. Topology describes the
  asset; it does not create panel ownership.
- On the settings route, require exactly one large semantic `settings` root with at least 35% of
  the viewport width and 50% of its height. Reject a navigation marker as the owner and reject any
  opaque descendant covering more than 92% of that root.
- For an artwork settings route in normal transparency, require the semantic root to have no
  background image or blur and a computed surface alpha matching the configured shared
  projection: 52% wide, 60% split, and 68% narrow. Under reduced
  transparency, increased contrast, or forced colors, require an opaque native-safe root instead.
  Cards, fields, controls, menus, and dialogs remain protected opaque descendants.
- Fail verification when another large semantic surface has an opaque color alpha of 0.97 or
  greater, when the settings material exceeds its bounded normal alpha, or when an opaque
  descendant covers more than 92% of a large root. `backgroundImage !== none` is implementation
  evidence only and cannot satisfy the art-visibility gate.
- Structural continuity is proven only when all visible large surfaces participate in the
  same semantic workbench coordinate system. Pixel/screenshot comparison may supplement this gate but cannot
  replace semantic mapping, blocker detection, or contrast checks.
- Give side tasks and all large empty panes a deliberate background rather than a flat accidental blank.
- Keep artwork subordinate to text with stable overlays; avoid faces or focal objects behind dense controls.
- Verify `cover`/`contain` decisions across aspect ratios and keep critical artwork inside safe crop zones.
- Disable expensive blur on large scrolling surfaces; constrain translucency to chrome, menus, or small cards and provide an opaque fallback.
- Prevent decorative layers from intercepting clicks or covering content.
- Capture computed style for the failing element and its immediate parent, siblings, and pseudo-elements before changing selectors; a visually white band may come from a native fade outside the themed component.

## Functional regression and rollback

- For migrated themes, compare the privacy-safe native baseline and themed computed styles defined in [theme-migration.md](theme-migration.md). Explicitly cover sidebar scroll/selection, composer and its fade/gradient siblings, dialog action states, side-panel tabs/close controls, and both terminal mounts. Paint may change; native geometry and interaction must not.
- On Windows, verify the selector lists exactly the validated manifest catalog in manifest order, highlights `nextLaunchTheme`, and labels `loadedTheme` separately without claiming a live change.
- Snapshot the state file bytes, then verify Cancel, Escape, and window close produce no mutation and no Codex launch. Confirm Enter and double-click each cause exactly one validated selection followed by one launch attempt under the same start mutex.
- Exercise shortcut ownership boundaries: an unknown same-name shortcut is refused, an exact legacy managed **Codex Themes** shortcut upgrades transactionally, and **Codex Original** remains byte-for-byte unchanged.
- Before diagnostic interaction, take one restoration snapshot containing the active task and route; window geometry and maximized state; zoom; sidebar and panel open/closed state, width, height, split ratio, and selected tabs; scroll positions; the focused element; open menus/dialogs; and composer draft, attachments, and sending state. A route-changing probe must refuse to start while the composer contains any draft or attachment; do not read, copy, or temporarily retain private draft content as a workaround. Use stable native controls rather than guessed shortcuts. Run diagnostic interaction as a restoration transaction and restore the snapshot in `finally`, including after a failed probe.
- Composer sending state and task progress are volatile observation-only fields: record them for change attribution, but never attempt to restore them. If diagnosis would touch their controls or content, stop interaction and continue with read-only DOM inspection or wait for the user.
- If restoration fails or the exact renderer cannot be re-verified, keep the result `PARTIAL`, list every unrestored state, and ask the user to restore it with native controls. Never restart Codex merely to repair diagnostic state.
- Create and select a task; open/close sidebar and every tested panel.
- Attach and remove files; type and submit only in a disposable task when the user authorizes it.
- Open menus, change a reversible option, and confirm keyboard Escape/Enter behavior.
- Verify no unexpected network listener, config edit, app-file edit, or additional Codex process.
- After any hot reapply, compare the complete payload hash (including artwork), expected/live CSS hashes, disk pack hash, recorded runtime port/PID, and active watcher command; reload once when authorized and confirm the watcher reapplies the same hashes.
- Exit via **Codex Themes**, relaunch it, and verify persistence only when restart testing was authorized.
- Launch **Codex Original** and verify the untouched native interface and normal task access.
- Run `restore`, confirm `loadedTheme` and `nextLaunchTheme` are cleared, and confirm the recorded injector is gone.

Report each failed item with surface, state, width, Codex version, expected result, actual result, and screenshot path if privacy-safe. Do not call the theme complete while a required item fails.

Injector evidence is privacy-safe style and geometry telemetry only: semantic-part counts, composition ownership, opaque-cover ratios, computed colors, opacity, background-image presence, element bounds, document overflow, visible control counts, active animation count, and media preferences. It must not include task text, titles, URLs, repository names, file paths, or message content. Structure fixtures captured by `scripts/capture-dom-fixture.mjs` follow the same boundary and must pass `scripts/fixture-tool.mjs audit`. This evidence can locate risks but remains `PARTIAL` until the complete matrix and authorized task flow pass.

## Result classification

- `PASS`: every required runtime surface, control, persistence check, and rollback path passed; adapter `verify` or `loadedTheme` alone is insufficient.
- `PARTIAL`: preparation succeeded but required runtime checks remain unverified, or an explicit defect remains.
- `UNSUPPORTED`: renderer identity or a required anchor cannot be verified; keep the native interface.
- `ROLLED_BACK`: partial injection was removed and the native interface was verified.

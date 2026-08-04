# Workbench usability contract

Read this file before designing, generating, validating, or repairing a theme. Codex is a dense workbench, not a campaign surface. Visual quality cannot compensate for impaired task completion, unreadable state, slower scanning, or unfamiliar interaction.

## Priority rule

Preserve Codex behavior first, accessibility second, visual identity third. A direction that fails either of the first two gates must be revised or rejected even when the user prefers its artwork.

## Paint-only default

Theme native UI through a paint-only policy. Preserve native typography, native geometry, information density, keyboard order, hit targets, scrolling, focus, clipping, and component state. Prefer color, opaque background, border color, outline, restrained shadow, and theme-owned background art.

Pack CSS must not change native workbench structure or metrics with properties such as:

- `display`, `position`, inset properties, `z-index`, grid/flex properties, or `transform`;
- `width`, `height`, min/max sizes, margin, padding, gap, or `overflow`;
- `pointer-events`, visibility, resize, order, or content visibility;
- `font-family`, `font-size`, `line-height`, `letter-spacing`, white space, or text overflow;
- ancestor opacity, continuous animation, layout animation, or transition of geometry.

A theme-owned `::before` or `::after` decoration may use positioning only when every selector targets a pseudo-element and the rule includes `pointer-events: none`. It must stay behind content, contain no required information, and disappear under forced colors, reduced transparency, narrow layouts, or a missing stable anchor.

Do not replace native controls, invent interaction, move buttons, change labels, or use localized text, positional selectors, or screen coordinates as ownership anchors. Unknown DOM or a required structural override is `UNSUPPORTED`, not a reason to broaden selectors.

## Shared header material

Every theme inherits one runtime-owned top-header recipe. The outer application-menu drag surface stays transparent. The semantic header uses a horizontal edge-protection gradient over a vertical material veil, both mixed from the theme surface token, so the same canvas or artwork remains visually continuous beneath it. The shared runtime strengthens the veil responsively and replaces it with an opaque native-safe surface for reduced transparency, increased contrast, and forced colors.

Theme packs must not select `[data-ct-part~="header"]`, add a header background, blur, divider, or shadow, or reproduce the recipe locally. Optional identity tuning is limited to the bounded `--ct-header-material-surface`, `--ct-header-material-top-tint`, `--ct-header-material-mid-tint`, `--ct-header-material-bottom-tint`, and `--ct-header-material-control-tint` properties on the exact theme root. New themes get the approved treatment without declaring any of them.

## Shared settings material

Every artwork theme also inherits one runtime-owned material for the large semantic settings
content root. It reveals the same workbench canvas through a 52% surface veil at wide widths,
60% at split widths, and 68% at narrow widths. Nested structural `.main-surface` layers remain
transparent; cards, fields, switches, buttons, menus, dialogs, and other actual reading/control
surfaces remain opaque. Reduced transparency, increased contrast, and forced colors replace the
large veil with an opaque native-safe surface.

Artwork theme packs must not set `--ct-settings-tint` or paint the semantic `settings` root with
`background`, `background-color`, or `background-image` outside an accessibility fallback. The
shared material is deliberately independent from right-panel tint so a pale raster cannot be
washed out by a panel-oriented opacity. New themes inherit this rule automatically.

## Typography and density

- Keep the native product font stack and all native font metrics. Theme font tokens are compatibility metadata, not permission to restyle native labels or prose.
- Do not shrink controls, code, terminal rows, composer content, task lists, or secondary labels.
- Do not make information quieter by reducing opacity. Use a validated explicit foreground color.
- Preserve long labels, CJK text, paths, hashes, tables, code wrapping, and native truncation behavior.

## Color and critical surfaces

Critical surfaces are conversation reading areas, composer, code, terminal, diff, permission and approval UI, dialogs, menus, tooltips, settings cards/fields/controls, and destructive actions. Keep these critical content surfaces opaque and free of detailed artwork or backdrop blur; the large structural settings root may use only the bounded shared settings material above.

- Normal text, muted text, placeholders, links, and semantic text need at least 4.5:1 against every actual background where they appear.
- Meaningful icons, component boundaries, selected states, and focus indicators need at least 3:1 against adjacent colors.
- Treat thresholds as floors; do not round a failing ratio up.
- Keep success, warning, danger, and info distinct. Diff, approval, selection, and error states must retain native text, icon, shape, border, or pattern cues and never rely on color alone.
- Resolve transparency, color mixing, background images, pseudo-elements, and ancestor opacity before claiming a ratio. An unresolved image-backed text surface remains unverified.

## Motion, transparency, and performance

- Allow 150-250 ms state feedback only. Do not add page-load choreography, parallax, background drift, shimmer, bounce, or continuous decoration.
- Reduced motion must disable only theme-owned nonessential motion; never stop native progress, loading, sending, or recording feedback.
- Reduced transparency must remove theme art, blur, and translucency while preserving stable opaque surfaces.
- Avoid fixed scrolling backgrounds, large-area blur, filters on scrolling containers, and paint-heavy animation.
- Compare a privacy-safe baseline with theme off/on during a long conversation, scrolling, composer input, terminal output, and panel resizing. Any repeatable input lag, dropped-frame regression, or layout shift is a blocker until explained and fixed.

## Static gate

Run `scripts/theme-tool.mjs audit --catalog <catalog>` before staging. It must validate the shared `assets/base.css` and every pack CSS against the paint-only policy, plus the declared `native` typography and geometry policy, `opaque` critical surfaces, `state-only` motion, expanded contrast contexts, and adaptive palette variants. `validate` and `audit` failures block the selector catalog.

Workbench policy version 8 also rejects theme paint on the runtime-owned semantic header, artwork-theme paint or tint ownership on the shared semantic settings material, theme paint on an unscoped native `button`/`[role="button"]`, a button reached only through an unqualified layout root such as `aside button`, and any `form:has(...)` component-ownership guess. System `prefers-contrast`, `prefers-reduced-transparency`, and `forced-colors` fallbacks are the only broad exceptions.

Static success is necessary but insufficient. Tokens and CSS source cannot prove the computed renderer result.

## Runtime matrix

Use [surface-qa.md](surface-qa.md) on a verified renderer and record Codex version plus theme/payload hashes. At minimum cover:

- wide, split-pane, and an equivalent 320 CSS px narrow viewport;
- 100%, 200%, and 400% zoom, high DPI, long English/CJK labels, and text expansion;
- keyboard-only navigation, visible unobscured focus, pointer input, and native hit targets;
- light, dark, increased contrast, reduced motion, and reduced transparency where supported;
- all four built-in Windows contrast themes and customized system colors on Windows;
- protanopia, deuteranopia, tritanopia, and achromatopsia color vision simulation;
- default, hover, focus, active, selected, checked, disabled, loading, offline, empty, warning, error, and destructive states.

Code and diff may scroll inside their own established container at narrow widths. The app shell must not gain document-level horizontal scrolling, hide commands, overlap content, or clip focus.

## Task completion gate

In an isolated disposable task and only with authorization for interaction, verify that a user can:

1. create and select a task;
2. read conversation, code, citations, tables, and a diff;
3. type, attach/remove a file, send, and stop from the composer;
4. inspect terminal output and switch/resize panels;
5. open and dismiss menus, dialogs, tooltips, settings, and command surfaces;
6. distinguish and act on permission, approval, warning, error, and destructive controls;
7. return through **Codex Original** without lost task access or changed native settings.

Never interact with a live sending task or private content for QA. Observation-only evidence remains `PARTIAL` when an authorized task flow was not completed.

## Result gate

- `PASS` requires static audit, computed-style evidence, the runtime matrix, task completion, persistence, and rollback.
- `PARTIAL` is required for generated concepts, deterministic fixture previews, offline review, missing surfaces, or incomplete task interaction.
- `UNSUPPORTED` is required when stable anchors or paint-only implementation are impossible.
- `ROLLED_BACK` is required after removing a failed partial injection and verifying the native renderer.

Any unreadable text, obscured focus, hidden command, changed hit target, structural overflow, broken native state, performance blocker, or failed rollback prevents `PASS`.

## Standards baseline

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [WCAG 2.2 target size (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)
- [WCAG reflow at 320 CSS px / 400%](https://www.w3.org/WAI/WCAG21/Understanding/reflow)
- [Windows contrast themes](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/high-contrast-themes)
- [VS Code theme colors](https://code.visualstudio.com/api/references/theme-color)
- [Apple accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility/)

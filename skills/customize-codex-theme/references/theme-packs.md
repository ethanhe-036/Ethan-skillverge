# Built-in theme catalog

Load this file only when the user asks to browse, try, compare, or preview built-in themes. The catalog is optional; a user-provided image, prompt, recommendation request, or named preset does not require displaying all six.

| ID | Name | Mode | Design direction | Guardrail |
| --- | --- | --- | --- | --- |
| `ink-ivory` | Ink & Ivory | Light | Editorial typography, warm ivory paper, restrained dividers, umber accent. | Keep dense work surfaces flat and highly legible. |
| `paper-workshop` | Paper Workshop | Light | Tactile paper, crafted edges, muted marine blue and brick red. | Use texture as atmosphere, never beneath dense text. |
| `neon-workshop` | Neon Workshop | Dark | Graphite workbench, cyan and violet signals, precise luminous controls. | Avoid glow around body text, code, diff, and terminal. |
| `expressive-signal` | Expressive Signal | Adaptive | Bold editorial blocks, compact geometry, high-energy blue and amber. | Preserve hierarchy and avoid decorative shapes over controls. |
| `liquid-focus` | Liquid Focus | Adaptive, experimental | Cool translucent chrome with clear layered depth. | Limit blur to small chrome/overlays; keep work areas opaque. |
| `soft-tactile` | Soft Tactile | Light, experimental | Neumorphic low-density cards and controls on blue-grey canvas. | Keep real borders/focus rings and flatten at narrow or high-contrast layouts. |

## Catalog presentation

When browsing is requested:

1. Show all six as compact cards or previews with mode, personality, and guardrail.
2. Let the user shortlist without applying anything.
3. Generate detailed previews only for the shortlist, unless the user explicitly asks to render all six.
4. Confirm one theme and whether to prepare it for next launch.

When the user asks to “try all six,” prepare separate packs and preserve their IDs. Do not overwrite one pack with another. Keep `loadedTheme` as the currently verified theme while `selectedTheme` and `nextLaunchTheme` track the next choice.

## Custom pack requirements

A custom pack must have:

- a unique lowercase hyphenated ID that cannot escape the theme directory;
- a human-readable name and light/dark/adaptive mode;
- semantic colors for canvas, surface, text, muted text, accent, border, focus, success, warning, error, diff-add, and diff-delete;
- optional background art with a local relative path, declared licence/provenance, crop focal point, and fallback color;
- declared experimental effects and an opaque/reduced-motion fallback;
- no remote font, image, script, stylesheet, tracking pixel, or network dependency.

Validate file existence, MIME type, size, path traversal, JSON shape, contrast, and theme ID before staging. Copy or generate assets only after privacy and copyright review. Prefer system fonts; bundle a font only when its licence explicitly permits redistribution.

## Multi-theme behavior

- `prepare` validates a pack and sets `selectedTheme` plus `nextLaunchTheme`.
- `start` reads `nextLaunchTheme`; it never infers a theme from a shortcut filename.
- `mark-loaded` occurs only after renderer verification and moves the prior verified value to `previousTheme`.
- `switch` changes the next-launch choice without claiming the live renderer changed.
- `restore` clears the live and next-launch values while retaining packs for future switching.

Use **Codex Themes** as the single themed entry so multiple themes do not clutter the desktop. Use **Codex Original** as the stable native escape hatch. Add per-theme launchers only when the user explicitly prefers them.

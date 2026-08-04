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
3. Generate detailed previews only for the shortlist, unless the user explicitly asks to render all six. For raster previews, follow [image-generation.md](image-generation.md); do not use HTML/SVG placeholders as image results.
4. Confirm one theme and whether to prepare it for next launch.

When the user asks to “try all six,” prepare separate packs and preserve their IDs. Do not overwrite one pack with another. Keep `loadedTheme` as the currently verified theme while `selectedTheme` and `nextLaunchTheme` track the next choice.

The public package contains these six redistributable built-ins. User-local and
copyright-restricted packs remain outside the repository and release archives.
When the user asks for all installed themes, show the validated local manifest;
future counts and grouping must come from that manifest, never from hard-coded
shortcut or UI text.

## Custom pack requirements

A custom pack must have:

- a unique lowercase hyphenated ID that cannot escape the theme directory;
- optional validated `collectionId` and `variantLabel` catalog metadata when several packs form one visual series; the theme ID remains the state and trust identity;
- a human-readable name and light/dark/adaptive mode;
- for new schema-v2 packs, a three-answer `creativeBrief` and compiled
  `experience` contract with mode, freely chosen art topology, background scope,
  reading strategy, responsive policy, maximum decor budget, and
  `interactionAuthority: native-only`;
- an `aestheticProfile` that states one concise design thesis, 3–5 mood words,
  one governing motif, 2–5 explicit anti-goals, the palette strategy, and a
  bounded dominant/secondary/accent motif budget;
- a `compositionProfile` that names primary, secondary, and quiet workbench
  zones plus wide, split, and narrow viewport intent;
- a `materialGrammar` with at most four material families, one consistent
  elevation scale, and one lighting direction;
- semantic colors for canvas, surface, text, muted text, accent, border, focus, success, warning, error, diff-add, and diff-delete;
- optional background art with a local relative path, declared
  licence/provenance, crop focal point, fallback color, normalized quiet zone,
  focal zones, and explicit wide/split/narrow crop behavior;
- declared experimental effects and an opaque/reduced-motion fallback;
- no remote font, image, script, stylesheet, tracking pixel, or network dependency.
- one registry trust declaration. Canonical reusable packs may be `builtin`;
  personal/local assets must be `local-private` and distribution-excluded;
  external packs require an approved Ed25519 signature.

Validate file existence, MIME type, size, path traversal, JSON shape, contrast,
perceptual palette separation, composition-zone disjointness, material limits,
artwork quiet-zone noise, and theme ID before staging. Treat those deterministic
signals as guardrails rather than a numerical beauty score. Compile authoring
intent before validation and require one semantic coordinate owner, never one
raster crop per panel. The declared design
thesis and rendered viewport review remain the source of aesthetic intent. Copy
or generate assets only after privacy and copyright review. Prefer system fonts;
bundle a font only when its licence explicitly permits redistribution.

## Multi-theme behavior

- `prepare` validates a pack and sets `selectedTheme` plus `nextLaunchTheme`.
- `start` reads `nextLaunchTheme`; it never infers a theme from a shortcut filename.
- After `restore`, `start` must fail closed until a new `nextLaunchTheme` is selected; it must not fall back to `selectedTheme`.
- `mark-loaded` occurs only after renderer verification and moves the prior verified value to `previousTheme`.
- `switch` changes the next-launch choice without claiming the live renderer changed.
- `rollback` stages a trusted explicit or previous theme, records the action,
  and never restarts or changes `loadedTheme` by itself.
- `restore` clears the live and next-launch values while retaining packs for future switching.
- Each platform serializes `prepare`, `switch`, `rollback`, `start`, and
  `restore` with one per-user state/start lock so a pending choice cannot race
  launch or cleanup.

Use **Codex Themes** as the single themed entry so multiple themes do not clutter the desktop. Use **Codex Original** as the stable native escape hatch. Add per-theme launchers only when the user explicitly prefers them.

On Windows, **Codex Themes** opens a visual selector populated only from the fully validated manifest catalog. It groups packs by validated collection metadata, shows only locally validated preview paths, and falls back to semantic swatches for CSS-only packs. Highlight `nextLaunchTheme` as the pending choice and display `loadedTheme` separately as the last renderer-verified theme; never imply that selecting changed the live renderer. A collection node is browse-only. Confirmation validates the concrete theme ID again, performs `select`, and then continues the safe start workflow under the same single-instance lock. Cancel, Escape, window close, a collection-only selection, or an invalid/stale ID must leave the state file unchanged and must not launch Codex.

# Palette and visual direction

Read this file before proposing, generating, or iterating a custom theme, together
with [aesthetic-direction.md](aesthetic-direction.md). Character or subject accuracy
and color design are separate approval gates: correct artwork does not make an
interface palette usable.

## 1. Analyze the reference before choosing UI colors

Record the subject identity, mood, dominant and supporting hues, lightest and darkest usable values, saturation range, visual density, focal point, and safe crop zones. Separate what belongs to the artwork from what belongs to the work interface.

Do not sample a face, costume, logo, or dramatic highlight and spread that color across every surface. Artwork supplies narrative cues and accent candidates; it does not replace an interface color system.

## 2. Build a functional palette

Define a neutral surface ladder with visibly distinct `canvas`, `surface`, and `surfaceRaised` values. The ladder may be warm, cool, or nearly achromatic, but it must support scanning, grouping, elevation, and long work sessions without becoming a one-note palette or 单一色相浸染.

Define every required role before writing CSS:

| Role | Decision rule |
| --- | --- |
| `canvas` | Quiet app background; never chosen only because it matches the character. |
| `surface` / `surfaceRaised` | Opaque work and control layers with clear luminance separation from the canvas and each other. |
| `text` / `muted` | Validate against every actual surface where each token appears; muted still needs to be readable. |
| `accent` / `accentText` | One primary action signal with controlled saturation and a legible foreground. Do not use it as decoration everywhere. |
| `border` / `focus` | Borders must clarify structure; keyboard focus must remain unmistakable on every neighboring color. |
| `success` / `warning` / `danger` / `info` | Semantic colors stay distinct from one another and from the character palette. Do not recolor all states into the theme accent. |
| `codeSurface` / `terminalSurface` / `terminalText` | Stable, opaque reading surfaces with explicit foreground/background pairs; check syntax and ANSI colors in context. |
| `diffAddSurface` / `diffRemoveSurface` | Addition and removal remain distinguishable by more than hue alone. |
| `approvalSurface` | Permission and approval content remains prominent, opaque, and unambiguous. |

As a minimum, target 4.5:1 for normal text and meaningful icon foregrounds, and 3:1 for large text, focus indicators, component boundaries, and non-text UI states. Treat these as floors rather than design targets. Check disabled and muted content after ancestor opacity is applied.

## 3. Relate artwork to the interface

- Keep faces, eyes, logos, and other focal objects out from under dense text and controls at wide, split, narrow, and high-DPI layouts.
- Choose crop focal points and safe zones deliberately. A desktop composition must still make sense when the sidebar narrows or the window is split.
- Derive one stable overlay or stable contrast token from the worst relevant artwork region during generation. Do not sample pixels repeatedly at runtime.
- Use an opaque fallback wherever artwork, transparency, blur, or a native component can make foreground contrast uncertain.
- Use artwork hues primarily for accents, selected states, restrained highlights, and atmosphere. Keep code, terminal, diff, approval, composer, and long-form reading surfaces opaque and calm.
- Preserve semantic meaning and product hierarchy even when they conflict with literal colors from the reference image.

## 4. Produce three genuinely different directions

When the user has not already selected a named direction, the three previews must use three distinct palette strategies, not the same layout recolored three times:

1. **Narrative / faithful:** closest to the reference mood and artwork, with a neutral work surface protecting readability.
2. **Restrained / work-focused:** reduced artwork coverage, quieter neutrals, and the reference palette concentrated into action and selection states.
3. **Expressive / high-contrast:** stronger structural contrast and bolder accent placement while retaining opaque work surfaces and distinct semantic states.

Each direction must show or state its neutral surface ladder, primary accent pair, semantic colors, artwork crop/overlay treatment, intended light/dark behavior, and likely contrast risks. Swatches alone are insufficient; preview the colors on representative Codex surfaces such as navigation, action cards, composer, code, diff, terminal, and approval UI.

## 5. Confirm a palette contract

Before implementation, record a compact table with token, value, purpose, foreground/background contrast pair, and affected surfaces. Confirm with the user:

- light, dark, or adaptive mode;
- the neutral surface ladder;
- primary accent and accent foreground;
- semantic state colors;
- artwork coverage, crop, stable overlay, and fallback color;
- which of the three direction strategies is selected.

Changing the character, background artwork, mode, or dominant hue invalidates the affected palette pairs and requires this step again.

Also record the perceptual intent: ordered OKLCH lightness steps for
`canvas`/`surface`/`surfaceRaised`, the intended surface chroma ceiling, and why
the accent remains distinct from success, warning, danger, and info. These are
review signals, not permission to force all themes into one numeric palette.

## 6. Validate tokens and the rendered result

Run `scripts/theme-tool.mjs` for schema and token contrast. Token validation is necessary but insufficient: CSS specificity, native component styles, inherited opacity, pseudo-elements, gradients, and artwork can change what the user actually sees.

After injection, inspect the actual computed foreground and background colors on representative elements and their opacity-bearing ancestors. A theme does not pass because its JSON tokens pass. Continue with [surface-qa.md](surface-qa.md), including light cards inside dark themes, overlays, menus, disabled states, code, terminal, diff, approval, and responsive crops.

Color fixes must remain paint-only. Do not repair a contrast failure by changing native font metrics, control geometry, density, wrapping, clipping, opacity, or interaction order. When the native control already supplies its own state and forced-color inheritance, preserve that chain instead of overriding every descendant.

## Rejection patterns

Reject or redesign a direction when it has any of these traits:

- a one-note palette or 单色浸染 that removes hierarchy;
- the accent color applied to most text, borders, buttons, and status states;
- semantic colors collapsed into the character or brand color;
- translucent reading surfaces over detailed artwork;
- text or controls placed across faces and focal objects;
- a dark themed surface with native light controls and no explicit foreground/background fallback;
- passing token ratios but unreadable actual components;
- three previews that differ only by hue, image crop, or character pose.

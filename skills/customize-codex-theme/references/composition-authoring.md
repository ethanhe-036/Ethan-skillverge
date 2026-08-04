# Creative brief and composition compiler

Use this contract for every new theme and every material visual redesign. It separates
creative freedom from the runtime safety boundary.

## Ordinary-user interaction

Ask no more than these three questions unless the user already answered them:

1. What should the workspace feel like?
2. What should be the first visual signature people notice: a character, scene, symbol,
   texture, material, or color atmosphere?
3. While working, should artwork feel immersive, appear mainly in empty space, stay at the
   edges, or disappear in favor of color and material?

Never ask an ordinary user to choose schema fields, run a compiler, or understand DOM
surfaces. The agent translates their answers and runs all checks.

## Authoring pipeline

```text
ordinary-language answers
  -> creativeBrief
  -> experience + existing design profiles
  -> deterministic composition compiler
  -> topology-specific layoutPlan
  -> runtime composition + scope/responsive projection + coordinate-ownership contract
  -> static gates
  -> optional live renderer verification
```

The compiler is deterministic and lives in `scripts/composition-compiler.mjs`. The
injector consumes its runtime projection; it does not interpret artistic meaning.

## Schema v2

New packs set `schemaVersion: 2` and add:

```json
{
  "creativeBrief": {
    "mood": "cinematic, warm, adventurous",
    "visualSignature": "A restrained nautical map with an edge-anchored character silhouette",
    "artPresence": "home-and-empty-space"
  },
  "experience": {
    "mode": "background",
    "artTopology": "edge-focal",
    "backgroundScope": ["home", "thread", "right-panel", "settings"],
    "readingStrategy": "protected-surfaces",
    "responsivePolicy": {
      "wide": "preserve",
      "split": "rebalance",
      "narrow": "reduce"
    },
    "decorBudget": {
      "density": "medium",
      "dominantMotifs": 1,
      "secondaryMotifs": 2,
      "accentAreaPercent": 7
    },
    "interactionAuthority": "native-only"
  }
}
```

Allowed experience modes are `background`, `immersive`, `bounded-showcase`, and
`palette-only`. Allowed topology vocabulary is deliberately broad:

- `ambient-full-canvas`
- `edge-focal`
- `dual-anchor`
- `centered-emblem`
- `pattern-texture`
- `portrait-zone`
- `bounded-hero`
- `none`

These are design grammars, not fixed templates. The compiler produces a distinct
`layoutPlan` for each topology: anchor strategy, allowed focal-zone range, quiet-zone
relationship, and rasterization method. It does not prescribe “character left / text middle /
character right.” A topology describes how the final workbench asset is composed; it does
not grant a panel its own background image.

Examples:

- `dual-anchor` requires two or more separated focal zones, but their subjects, scale,
  asymmetry, and location remain the designer’s choice.
- `pattern-texture` requires no focal zone and bakes the pattern into the shared workbench
  canvas.
- `portrait-zone` accepts a portrait source, then composes it into the workbench canvas; it
  does not stretch or repeat that portrait separately in each panel.
- `bounded-hero` reserves a deliberate hero region while the rest of the canvas protects
  working content.

`decorBudget` is a maximum budget. It never means every available motif must be drawn.
The strongest themes often use less than the budget.

## Single coordinate ownership

The invariant is **one coordinate owner per semantic viewport**, not “every theme must use
one particular 16:9 layout.”

For an artwork theme:

- coordinate space: `semantic-workbench-viewport`
- direct raster owner: `workbench`
- sidebar, main, and right panel: transparent or semantically tinted participants in that space;
  header: the shared runtime-owned continuous material veil; settings: the independent shared
  runtime-owned 52%/60%/68% continuous-canvas material over the same space
- composer, terminal, Browser guest, code, diff, approvals, dialogs, menus, and overlays:
  protected opaque reading surfaces
- independent panel raster copies: forbidden
- independent panel crops: forbidden
- native geometry and interaction: authoritative

The asset itself may place focal content anywhere, use one or several visual anchors, reserve
quiet regions, or use texture only. Separate assets may be authored for genuinely different
responsive contexts, but only one compiled asset owns a given semantic viewport at a time.
Do not solve a crop problem by assigning the same raster separately to top, middle, and bottom
panels.

Do not author header paint in pack CSS. `headerTint` compiles into the shared
`--ct-header-material-*` projection, including responsive strengthening; CSS-only themes use the
same runtime defaults. Theme identity comes from the palette and artwork beneath the material,
with optional bounded root-variable tuning, not a separate opaque top slab.

CSS-only and palette-only themes compile to `owner: none`.

## Responsive contract

Declare intent for `wide`, `split`, and `narrow`:

- `preserve`: retain the primary focal relationship.
- `rebalance`: move or recrop emphasis without changing reading hierarchy.
- `reduce`: lower art signal before readability is lost.
- `hide`: remove art and retain the palette/material identity.

Responsive intent must be monotonic: artwork may stay equally strong or reduce as the
viewport narrows, but it may not disappear at split width and return at narrow width. The
compiler emits deterministic wide/split/narrow focus, scrim, and semantic-tint rules for
compiler-owned Schema-v2 compositions. `hide` removes only the shared workbench raster.

The current runtime projects every artwork topology into one `continuous` semantic-workbench
coordinate owner. This is an implementation detail, not a visual template: topology lives in
the compiled asset and `layoutPlan`, while scope, focus, tints, and responsive behavior are
compiled separately. A declared low-level `composition` remains an advanced compatibility
override and must supply its own responsive CSS. The legacy low-level `portrait-zone` mode has
a runtime-owned single-workbench compatibility projection; it does not authorize portrait
copies on individual panels.

`backgroundScope` is executable, not documentation. `home`, `thread`, and `settings` govern
the whole shared workbench route. `right-panel` controls whether that panel reveals the same
root raster or stays opaque. A theme cannot request only `right-panel`, because that would
silently turn a panel into an independent coordinate owner.

## Reading strategy

Artwork is never the contrast surface for dense content. Use the declared strategy to decide
where semantic tints are enough and where an opaque surface is mandatory. Always keep:

- composer and input controls opaque
- code, diff, approval, and terminal surfaces opaque
- dialogs, menus, tooltips, and popovers opaque
- Browser guest paint native and isolated
- actual computed foreground/background contrast above the applicable threshold

Settings retain ambient art in unused space through the shared runtime-owned material: 52% at
wide widths, 60% at split widths, and 68% at narrow widths. Fields, cards, switches, menus, and
dialogs stay opaque. Artwork packs may not set `--ct-settings-tint` or paint the large semantic
settings root; the compiler and shared runtime own that projection independently from the right
panel.

## Compatibility and migration

Schema-v1 packs remain valid. The compiler infers `creativeBrief` and `experience` in memory,
preserves their declared low-level `composition` byte-for-byte, and emits
`LEGACY_AUTHORING_INFERRED`. It must not silently rewrite a live pack.

Migration writes only to an explicitly different output file. Review the output and its
licence/provenance before replacing a source pack.

## Review checkpoints

Before implementation:

- Can the user recognize their desired feeling and signature?
- Is the topology a deliberate choice rather than a stock left/middle/right template?
- Is there a real reading sanctuary at wide, split, and narrow sizes?
- Is decor a budget rather than a checklist?

Before staging:

- Does compilation produce exactly one expected coordinate owner?
- Does theme CSS avoid `--ct-user-art` and `--ct-workbench-art` ownership?
- Does an artwork pack avoid `--ct-settings-tint` and large semantic settings paint ownership?
- Are critical reading surfaces protected?
- Are decorations non-interactive and visually distinct from native controls?
- Do responsive declarations agree with the runtime projection?

After live application:

- Verify the actual semantic owner and visible raster, not merely a CSS
  `backgroundImage` declaration.
- Verify no opaque shell descendant hides the coordinate owner.
- Verify settings, right panel, new-task, thread, and narrow states independently.

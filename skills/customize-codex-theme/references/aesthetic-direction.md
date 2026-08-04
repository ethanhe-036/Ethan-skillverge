# Aesthetic direction and composition contract

Read this file before creating or materially revising a theme. Accessibility,
behavior, and runtime integrity remain hard gates. Aesthetic evidence is
advisory and may never override a hard failure.

## Contents

1. Translate intent before implementation
2. Declare the authoring envelope and three-part design contract
3. Generate comparable directions
4. Review with anchored judgments
5. Separate deterministic, vision, and user authority
6. Stop at explicit review checkpoints

## 1. Translate intent before implementation

Convert the user's ordinary-language request, reference image, or named preset
into a short art-direction brief before choosing CSS:

- one sentence explaining why the theme should exist;
- three to five mood keywords;
- one dominant signature motif;
- two to five anti-goals describing what the theme must not become;
- a palette strategy: `narrative`, `restrained`, or `expressive`;
- the intended visual authority order;
- the quiet work zones that must remain calm.

Do not require the user to know design vocabulary. “A calm nautical workbench”
is enough input; the Skill owns the translation into material, hierarchy,
palette, and composition decisions.

## 2. Declare the authoring envelope and three-part design contract

New schema-v2 presets first declare `creativeBrief` and `experience` as described
in [composition-authoring.md](composition-authoring.md), then declare all three
objects below. Existing schema-v1 presets infer the envelope in memory. Every
field is design intent, not permission to change Codex layout, typography,
density, or interaction.

The authoring envelope is intentionally topology-neutral. A `dual-anchor`,
`edge-focal`, `centered-emblem`, `pattern-texture`, or bounded composition can
all be excellent. The hard rule is single coordinate ownership, not a mandatory
left/middle/right composition.

### `aestheticProfile`

```json
{
  "aestheticProfile": {
    "designThesis": "A calm navigation desk where paper history surrounds focused engineering work.",
    "moodKeywords": ["warm", "adventurous", "handmade"],
    "signatureMotif": "restrained nautical paper marks",
    "antiGoals": ["scrapbook clutter", "poster behind an IDE", "accent on every control"],
    "paletteStrategy": "narrative",
    "motifBudget": {
      "dominantMotifs": 1,
      "secondaryMotifs": 2,
      "accentAreaPercent": 7
    }
  }
}
```

Keep at most one dominant motif. Secondary motifs support it; they do not
establish competing identities. `accentAreaPercent` is a maximum design budget,
not a measured approval score or a checklist that must be filled.

### `compositionProfile`

```json
{
  "compositionProfile": {
    "primaryFocus": ["composer", "active-task", "selected-navigation"],
    "secondaryFocus": ["artwork"],
    "quietZones": ["main", "code", "terminal", "settings"],
    "viewportIntent": {
      "wide": "preserve-hierarchy",
      "split": "rebalance-focus",
      "narrow": "reduce-decoration"
    },
    "artwork": {
      "narrativeAnchor": "crew collage guarding the reading sanctuary",
      "workspaceQuietZone": {"x": 0, "y": 0, "width": 58, "height": 100},
      "focalZones": [
        {"name": "crew-collage", "x": 78, "y": 50, "radius": 18}
      ],
      "cropBehavior": {
        "wide": "preserve-focal",
        "split": "rebalance-focal",
        "narrow": "reduce-art-before-readability-loss"
      }
    }
  }
}
```

Use percentage coordinates only as inspectable intent. They do not authorize
positioning native controls. The asset may use any deliberate topology, while
the raster still belongs to the single semantic workbench coordinate owner.

For CSS-only themes, omit `compositionProfile.artwork`. Still declare focus,
quiet zones, and viewport intent so material effects cannot become the visual
authority.

### `materialGrammar`

```json
{
  "materialGrammar": {
    "canvas": "weathered-parchment",
    "navigation": "weathered-parchment",
    "controls": "inked-paper",
    "reading": "clean-paper",
    "transient": "inked-paper",
    "elevationLevels": 3,
    "lightingDirection": "top-left"
  }
}
```

Use no more than four material families, no more than three elevation levels,
and one lighting direction. Glass, paper, ink, or metal is a medium; the design
thesis explains the theme's identity.

## 3. Generate comparable directions

When the user has not selected a named preset, keep the three palette
strategies from [palette-design.md](palette-design.md), but compare them on the
same representative scenes:

1. new task;
2. active conversation with code/diff;
3. composer focused;
4. settings;
5. right-panel launcher and side tasks;
6. terminal;
7. dialog/menu;
8. wide, split, and narrow views.

For each direction, state:

- creative brief, experience mode, and art topology;
- design thesis and anti-goals;
- signature motif and motif budget;
- material grammar;
- primary/secondary focus and quiet zones;
- wide/split/narrow behavior;
- strongest quality, biggest risk, and safest adjustment.

Do not compare directions using different screenshots or different content
density. A palette swatch or isolated wallpaper is not a workbench preview.

## 4. Review with anchored judgments

Review each axis separately; never sum them into a beauty score.

| Axis | 1 — unresolved | 3 — coherent | 5 — authored |
| --- | --- | --- | --- |
| Identity | Generic material or wallpaper | Thesis and motif are recognizable | Every paint decision reinforces a distinctive thesis |
| Hierarchy | Decoration competes with work | Task/composer remain primary | Focus order stays deliberate in every state |
| Composition | Crops and seams feel accidental | Wide and split views preserve intent | Wide/split/narrow each feel intentionally composed |
| Color harmony | Tokens pass but feel unrelated | Neutral ladder and accent are coherent | Perceptual steps and semantic colors feel inevitable |
| Material coherence | Too many unrelated effects | Roles share a small material family | Depth, light, edges, and fallbacks form one grammar |
| Restraint | Motifs or accents appear everywhere | Decoration has a clear budget | Negative space is purposeful and recognizable |
| Craft | Visible seams or generic defaults | No obvious visual defects | Small edges, transitions, and states feel finished |
| Responsive continuity | Identity disappears or collapses | Identity survives with safe reduction | Each viewport rebalances without losing its story |

A 1 or 2 is a review finding, not an automatic runtime failure. Explain the
visible evidence and proposed adjustment. Usability failures remain separate
hard blockers.

## 5. Separate deterministic, vision, and user authority

Deterministic checks may report:

- authoring compilation, coordinate ownership, and responsive projection;
- interactive-looking decoration or missing critical reading protection;
- schema completeness and contradictory focus declarations;
- OKLCH surface lightness steps and semantic-color distance;
- material-family and elevation counts;
- accent references across semantic surfaces;
- artwork quiet-zone size, focal-zone collision, and quiet-zone edge density;
- viewport/crop metadata completeness;
- runtime state × viewport evidence.

Vision or expert review may judge:

- whether artwork fights the composer;
- whether negative space feels deliberate rather than empty;
- whether seams, crops, and visual weight feel balanced;
- whether a material effect looks generic or authored.

The user remains the authority for emotional fit, desired intensity, and
whether the theme feels premium, calm, dramatic, or too busy. Deterministic or
vision evidence may point to risks; neither may approve a theme by score.

## 6. Stop at explicit review checkpoints

Review before advancing through each checkpoint:

1. **Direction review:** thesis, motif, anti-goals, material grammar, hierarchy,
   and viewport behavior agree with the user's intent.
2. **Contract review:** schema, catalog, CSS policy, perceptual signals, and
   composition metadata pass without hard errors.
3. **Implementation review:** inspect the changed diff for paint-only scope,
   semantic selectors, fallback behavior, and accidental motif/material drift.
4. **Visual review:** compare the standardized scenes and report the strongest
   quality, single biggest weakness, and prioritized adjustment.
5. **Release review:** run full deterministic and runtime evidence gates; keep
   the result `PARTIAL` wherever live matrix or task-flow evidence is missing.

Do not silently continue past a failed checkpoint. Repair the current stage,
review it again, and only then proceed.

# Theme quality audit

Read this file before staging a changed preset, changing the renderer contract,
or claiming that a visual defect is fixed.

## User interaction contract

The commands in this reference are agent implementation details. A user may
simply select `$customize-codex-theme` or say “检测当前主题质量”. The agent must
resolve the target, run the read-only checks, and explain the result in plain
language. Never require a non-technical user to run a command or interpret raw
JSON. An audit request authorizes detection and reporting only, not repair,
application, switching, refresh, or restart.

## Commands

Run the catalog gate first, then the quality audit:

```text
node scripts/theme-tool.mjs compile --theme-file assets/presets/<theme-id>/theme.json
node scripts/theme-tool.mjs audit --catalog assets/presets
node scripts/theme-tool.mjs registry --catalog assets/presets
node scripts/theme-tool.mjs diagnose --catalog assets/presets --theme <theme-id>
node scripts/theme-quality.mjs audit --catalog assets/presets --theme <theme-id> --summary
```

Omit `--summary` when you need pixel samples and full evidence for every
finding. The summary still includes the first missing matrix cell plus copyable
fixture capture and fixture audit commands.

Use `--strict` only for a release candidate. It treats warnings such as missing
runtime matrix cells as a failing command.

`qualityContractVersion` versions deterministic preset checks in
`theme-quality.mjs`. `runtimeQaContractVersion` is the broader live-renderer
contract pinned by `skill.manifest.json`; the two numbers are intentionally
independent and both appear in audit output.

## Deterministic checks

The quality command verifies:

- every preset declares `aestheticProfile`, `compositionProfile`, and
  `materialGrammar` without contradictory hierarchy or material roles;
- schema-v2 packs declare the three-answer `creativeBrief` and flexible
  `experience` contract, while legacy packs compile in memory without mutation;
- artwork compiles to one semantic workbench coordinate owner, with no
  independent panel raster or crop authority;
- generated decoration cannot intercept interaction or imitate a native
  control, and critical reading surfaces cannot be transparent or own artwork;
- OKLCH surface lightness steps, surface chroma, semantic-color distance, and
  accent-to-status distance and accent spread as advisory perceptual signals
  rather than a beauty score;
- artwork quiet-zone area, declared focal collisions, crop-intent completeness,
  runtime narrow-mode consistency, and quiet-zone edge density;
- the declared motif budget, material-family count, elevation count, hierarchy,
  and viewport intent are available in the summarized audit;
- composition defaults are compiled into a stylesheet before pack CSS, so
  responsive media queries are not suppressed by inline variables;
- preset-specific selectors stay out of shared `base.css`;
- the shared outer application-menu-bar stays transparent, the semantic header inherits the
  two-layer continuous-canvas material and accessible fallbacks, and no theme pack targets either
  native app-shell topology or the semantic header paint owner;
- the semantic settings root inherits the shared 52%/60%/68% continuous-canvas material, nested
  structural layers stay transparent, accessibility fallbacks become opaque, and artwork packs
  cannot own its tint or large-area paint;
- `glassChrome` and `tactileControls` declarations have matching semantic CSS;
- real blur never reaches composer, dialog, menu, terminal, or Browser surfaces;
- Browser guest paint remains native;
- non-ASCII Windows PowerShell scripts carry a UTF-8 BOM;
- token contrast has usable headroom above the WCAG floor;
- compiled PNG artwork retains a measurable signal after workbench and surface
  tints;
- text and semantic colors are sampled against the image-composited background;
- settings uses a two-part contrast model: only primary structural text may sit directly on the
  bounded ambient material, while muted/semantic text is also checked against the opaque raised
  surface required for cards, fields, and controls; live QA still inspects every visible text node;
- retained artwork has responsive tint rules;
- runtime state × viewport fixture coverage is reported per theme.

The PNG sampler supports non-interlaced 8-bit RGB/RGBA PNG. Unsupported artwork
formats produce a warning and require runtime computed-style or screenshot
evidence; they must never be treated as a silent pass.

## Interpreting results

- `error`: deterministic contract violation; block staging and repair it.
- `warning`: unresolved visual or runtime evidence; keep the result `PARTIAL`
  until it is mitigated or verified on the live matrix.
- `pass: true`: the deterministic quality gate passed. It does not mean the
  theme achieved full runtime `PASS`.

The `aestheticAudit` object reports inspectable signals and named risks. It
must not expose or imply an aggregate `beautyScore`. A low lightness step,
large accent spread, focal/quiet-zone collision, or high quiet-zone edge
density points the reviewer to a risk; it does not prove that the user's taste
is wrong. Use the anchored per-axis review in
[aesthetic-direction.md](aesthetic-direction.md) for identity, hierarchy,
composition, color harmony, material coherence, restraint, craft, and
responsive continuity.

For `RUNTIME_MATRIX_INCOMPLETE`, execute the reported `captureCommand` only
after the named theme and state are visibly active, then execute
`auditCommand`. Repeat for the remaining `missingCells`; never capture private
text, input values, or URLs outside the structure-only fixture contract.

For artwork warnings, prefer lowering large-area veils while protecting actual
text rows and controls with local opaque paint. Do not solve art visibility by
placing detailed raster content beneath code, terminal, diff, approval, menu,
dialog, or composer reading surfaces.

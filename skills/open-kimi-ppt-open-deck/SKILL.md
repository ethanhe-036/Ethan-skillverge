---
name: open-kimi-ppt-open-deck
description: Maintained Open-Deck variant of Open Kimi PPT for PPTD creation and editing, native PPTX inspection or conservative filling, quality-gated offline export, and authorized remote fallback. Use when the user explicitly requests Open-Deck or the maintained Open Kimi PPT workflow.
---

# Open Kimi PPT

> SkillVerge packaging note: this installable copy is derived from upstream commit `415727e9ea80d3fd8cea22c30c234577ce4cf2f3`. Its Skill ID was adapted to avoid colliding with the original 1.3.0 variant. See `reference/upstream-provenance.md`.

Open Kimi PPT uses PPTD v2, a page-oriented YAML DSL, as its editable authoring format. It also ships read-only PPTX inspection, a deliberately narrow native PPTX fill sidecar, a deterministic PPTD quality gate, and a fail-closed offline PPTX exporter.

For create, edit, or visual-replication work, the default deliverables are BOTH:

1. the complete editable PPTD project directory (`.pptd`, `pages/`, referenced `media/`, and optional sidecars);
2. the matching locally generated `.pptx`, plus the source-bound quality report used to authorize export.

This skill does not bundle a PPTX-to-PPTD importer. An inspectable PPTX can be inventoried, filled within the narrow native contract, or used as a visual reference for a newly authored PPTD project. Visual recreation is replication, not conversion.

## Resolve paths first

Resolve the **skill root** as the absolute directory containing this `SKILL.md`. Use that root for every `scripts/` and `reference/` path; never assume a fixed install location. Quote absolute paths containing spaces. Resolve `python3` first, then `python` when needed, and keep using the same executable.

The P0-P2 local tools use Python and perform no implicit package installation, browser action, or network request. Run each script with `--help` before relying on an unfamiliar installed version. The optional browser writer has separate prerequisites described under “Remote writer fallback.”

## Route the task before editing

Choose exactly one primary route. A task may use read-only inspection before another route, but do not silently change formats.

| Route | Use when | Contract |
| --- | --- | --- |
| **PPTD create/edit** | Creating a deck, editing a complete PPTD project, using a complete PPTD template, or visually recreating inspectable references | Author PPTD, run the strict quality gate, then export. Do not call recreation an import. |
| **PPTX inspect** | The user needs structure, text, notes, shape, layout/master, table, or chart inventory from an existing PPTX | Read-only OPC/OOXML inspection. No source mutation and no PPTD conversion. |
| **Conservative native fill** | The request is limited to replacing plain text in existing text shapes and/or an existing notes-body placeholder | Write a new validated PPTX. Preserve slide animation timing fingerprints. Reject object edits, new notes parts, and transition edits. |
| **Offline local export** | A PPTD v2 project fits the local DrawingML support set | Build PPTX without a browser or network. Reject unsupported semantics instead of rasterizing, omitting, or substituting them. |

If a PPTX-only edit exceeds native fill, request the source PPTD or an explicitly available external conversion capability. If the user accepts visual recreation, inspect rendered pages and author a new PPTD project; never invent a conversion command or claim a conversion occurred.

## Project contract and design lock

Read `reference/pptd.md` before authoring or editing PPTD. Keep the project self-contained:

```text
deck/
  deck.pptd
  deck.meta.json          # optional design/intent lock
  quality-report.json     # generated after all source changes
  pages/
    *.page
  media/
    sources.json          # required by the workflow when local media is referenced
    *
  deck.pptx
```

Use `deck.meta.json` beside the manifest for stable project intent: audience, objective, core message, primary language, consumption mode, page rhythm, target apps, media policy, and requested transitions. Read `reference/deck-metadata.md` and its schema. A backend may reject a transition it cannot preserve; it must not substitute a different effect.

For each referenced local media file, record the exact project-relative path, SHA-256, license, source, and derivation chain in `media/sources.json` according to `reference/schemas/media-sources.schema.json`. Do not treat a reachable URL as proof of reuse rights.

The local editor accepts a more restricted `.pptd` manifest than the full page YAML grammar: use a simple top-level `pages` string array and avoid anchors, aliases, tags, merge keys, complex keys, and duplicate keys in the manifest.

## PPTD create/edit workflow

### 1. Understand the request

Read all user-provided files and URLs. Determine the audience, objective, core message, source completeness, desired page count, consumption mode, target apps, required media, and whether element animation is actually needed. Use reasonable assumptions when they do not materially change the result; ask only when a missing choice would.

Classify the design direction:

- self-directed: read `reference/slides_categories.md` and the relevant scenario file;
- named preset: use only the requested preset under `reference/design_system/` and its `design.md` when present;
- PPTD template: inspect the complete template project and reuse its actual structures;
- style transfer: extract hierarchy, spacing, type, color, crop, and component rules from the supplied visual references.

The canonical preset, layout, and chart IDs live in `reference/registries/`. Use their Pick/Skip guidance instead of guessing from duplicated legacy filenames. Do not auto-pick a preset when the user requested self-directed design.

### 2. Author the content and pages

Make each page communicate one clear conclusion. Use real evidence images or screenshots when the topic concerns products, people, places, interfaces, experiments, or cases. Save permitted assets under `media/` before laying out pages around their natural proportions; do not stretch images or add irrelevant decoration.

For visual replication, estimate and verify element positions, typography, crop, hierarchy, and spacing. Use close-up inspection when details are unclear. Approximate simple vector content with shapes only when that preserves meaning; retain photos and other irreducible imagery as properly licensed media.

Use element animations only when explicitly requested or clearly valuable for a live presentation. Prefer 1-3 simple groups per page. Do not add animations to reading-oriented decks by default. Add speaker notes only when requested.

### 3. Perform structural and visual review

Review every page for out-of-bounds elements, overflow-prone text, occlusion, low contrast, inconsistent alignment, incorrect image proportions, and unnecessary density. When an image-capable agent and the authorized public editor path are available, `scripts/export_images.py` may export page images and an overview for visual review. This optional renderer crosses the Kimi network boundary and must not be used for confidential content without authorization.

If the renderer is used, inspect every page, fix the corresponding `.page`, rerun with `--force`, and repeat until clean. `export_images.py` is a renderer, not an aesthetic judge. When image rendering is unavailable, disclose that review was structural only.

### 4. Run the strict quality gate

Run this **after the final PPTD, `deck.meta.json`, pages, and media files are settled**:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/pptd_quality.py" \
  /abs/path/project/deck.pptd \
  --fail-on warning \
  --output /abs/path/project/quality-report.json
```

The report binds the manifest, pages, `deck.meta.json`, `media/sources.json`, and referenced local media bytes. The gate checks duplicate IDs, bounds, page/animation structure, local paths, hashes, licenses, derivations, and metadata. Any warning or error blocks this workflow. Fix the source and generate a new report; never edit the report to make it pass. Exporters re-run the deterministic checks and reject missing, stale, weak-threshold, incomplete, or tampered reports before artifact work.

## Existing PPTX: inspect and conservative fill

Inspect without mutation:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/pptx_native.py" \
  inspect /abs/path/source.pptx --pretty
```

Use `validate` for OPC/OOXML, relationship, XML, ZIP, and CRC checks. `analyze` is an alias for `inspect`.

For a supported native fill, create a JSON plan using one-based slide numbers:

```json
{
  "replacements": [
    {"slide": 1, "shape_id": 2, "text": "New title", "lang": "en-AU"},
    {"slide": 2, "shape_name": "Subtitle 3", "text": "Plain text"}
  ],
  "notes": [
    {"slide": 1, "text": "Updated speaker notes", "lang": "zh-CN"}
  ]
}
```

Then write a different output path:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/pptx_native.py" \
  fill /abs/path/source.pptx /abs/path/fill-plan.json \
  --output /abs/path/filled.pptx --pretty
```

Select each text target by exactly one of `shape_id` or `shape_name`. The sidecar replaces plain text in an existing `p:sp` text body and plain text in an existing notes-body placeholder. It intentionally rejects tables, charts, pictures, groups, connectors, SmartArt, OLE, new notes parts, transitions, and arbitrary OOXML edits. It validates the candidate and confirms slide timing fingerprints before atomic publication. Never describe this as general native PowerPoint editing.

## Offline local export

Prefer `scripts/export_pptx_local.py` when the project fits its support set. Pass the strict report:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/export_pptx_local.py" \
  /abs/path/project/deck.pptd \
  --output /abs/path/project/deck.pptx \
  --quality-report /abs/path/project/quality-report.json
```

The local backend accepts PPTD v2 with solid page backgrounds; plain text; a documented subset of preset shapes with solid fills and borders; straight two-point lines and supported arrowheads; signature-valid local PNG/JPEG images with supported fit/crop behavior; and plain speaker notes. From `deck.meta.json`, it applies `primaryLanguage` and page/default transitions of `none`, `fade`, `push`, or `wipe`, including supported direction, duration, click, and timed-advance settings. It emits editable DrawingML, package masters/layouts/theme, stable shape IDs, notes parts, and source-bound receipts.

It fails closed on unsupported element types or semantics, including tables, charts, icons, custom paths, remote images, GIF/SVG, element animations, gradients, morph transitions, and unsupported shape/line/crop behavior. Some lower-fidelity properties such as rich-text runs, shadows, custom web fonts, and selected adjustments may be reported as degradations; do not accept a degraded artifact when those properties are material to the user's request. Never claim feature parity with the browser writer.

Validate the generated artifact with `pptx_native.py validate`; for a high-risk deck, render representative slides in the target app and inspect them. ZIP validity alone does not prove identical playback in PowerPoint, WPS, Keynote, or LibreOffice.

## Remote writer fallback

Use `scripts/export_pptx.py` only when the local backend rejects required semantics and the user authorizes the public Kimi editor trust boundary. Pass the same strict quality report:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/export_pptx.py" \
  /abs/path/project/deck.pptd \
  --output /abs/path/project/deck.pptx \
  --quality-report /abs/path/project/quality-report.json
```

Before invoking it, check Python, Node.js 24+, npm, a Chromium-based browser, PyYAML, websocket-client, and the tested `agent-browser` 0.33.2. Image QA additionally needs Pillow. Do not install or upgrade dependencies without user approval. A newer agent-browser is rejected unless the user explicitly sets `OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER=1`; never set that override on the user's behalf.

The remote route loads deck content into code from the public Kimi editor and may fetch remote resources. It may require a user-authorized signed-in Kimi session. Never read the user's normal browser profile, cookies, or tokens without explicit authorization; prefer a dedicated `AGENT_BROWSER_PROFILE` or explicitly supplied state. Stop on 401/403 and ask the user to sign in. Do not use this route for confidential or regulated content unless the user accepts the applicable data-handling policy.

The writer requests font embedding when exposed and can post-process slide transitions, but the result summary—not the request—determines what succeeded. Report `fontEmbeddingFallback`, `fontEmbeddingControlObserved`, actual `fontParts`, transitions, quality receipt, and output validation. Never promise font embedding or cross-app animation fidelity.

## P3 optional asset packs

P3 assets are not part of core and must not delay P0-P2 delivery. The candidate pool of roughly 12,000 icons, roughly 190 sounds, and brand assets may be admitted only as separate opt-in packs after license, attribution, provenance, redistribution, and trademark review. Read `reference/asset-packs.md` and audit a pinned candidate offline:

```text
"/absolute/path/to/python" "/absolute/skill/root/scripts/asset_pack_audit.py" \
  /abs/path/pack/asset-pack.json --root /abs/path/pack --report /abs/path/pack/audit.json
```

Technical audit is not legal advice. Tabler/Phosphor, CHUNK, and verified CC0 sound sources still require their documented notices or attribution. Simple Icons remains conditional on per-brand trademark policy. Logos, proprietary artwork, and full brand asset sets require user-supplied assets, an official redistribution license, or documented written permission. Never copy them into core merely because they are publicly downloadable.

## Delivery

For PPTD creation/editing, link the absolute project directory, manifest, pages/media directories, quality report, and generated PPTX. Report which exporter ran, the quality receipt, any backend warnings, validation result, and whether visual QA was performed. If no exporter can safely preserve required semantics, deliver the complete PPTD project as partial output and state the exact unresolved blocker; never fabricate an artifact or silently drop features.

For inspect-only work, return the inventory and source path without creating a deck. For native fill, link the new PPTX and report the replacement counts, modified parts, validation result, and timing-fingerprint preservation. Never overwrite a user artifact unless the user explicitly requests replacement and the tool's `--force` semantics are acceptable.

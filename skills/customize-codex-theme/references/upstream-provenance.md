# Upstream provenance and adaptation policy

The runtime architecture was reviewed against
[`Fei-Away/Codex-Dream-Skin`](https://github.com/Fei-Away/Codex-Dream-Skin)
at commit `611c101e4c2ee98031476570c54f448edc617b07` on 2026-07-30.
GitHub `main` was rechecked on the same date and was identical to that commit.

## Adopt

- A versioned selector contract owned by the runtime.
- Privacy-safe DOM fixtures that exclude text, input values, URLs, and user content.
- A renderer mapping native Codex DOM to a small semantic surface vocabulary.
- One semantic workbench coordinate owner with no independent panel raster copies.
- A constrained CSS policy and multi-state release matrix.
- Function-based renderer-template substitution plus compile-only payload parsing.
- Exact style/payload revisions that invalidate stale runtime and fixture evidence.
- PNG/JPEG/WebP dimension parsing with 16384 px and 50 MP decode-safety limits.
- Pre-execution Authenticode verification for Windows Node.js candidates.

## Adapt

- Use the local prefix `data-ct-part`, not upstream `data-ds-part`.
- Verify selectors against the installed Windows Codex build; never copy upstream selectors blindly.
- Add a local read-only post-update compatibility probe that compares the centralized selector
  contract against a verified native renderer, emits structure-only evidence, and may qualify only
  one exact build for `PARTIAL`. Bind retained evidence to both its own SHA-256 and the selector
  contract hash so a selector change fails closed instead of inheriting stale approval.
- Keep the existing loopback CDP ownership checks, controlled launcher transactions, contrast
  checks, and last-known-good rollback.
- Add a topology-neutral authoring compiler: ambient, edge-focal, dual-anchor,
  centered, texture, portrait, and bounded assets remain creatively free while
  compiling to the same safe coordinate-ownership invariant.
- Keep the local Node.js minimum at 20 because this runtime requires built-in WebSocket rather
  than upstream's packaging-specific Node.js 22 floor.

## Reject

- Theme packs may not become selector registries for Codex internals.
- Independent header, main, sidebar, and right-panel copies of the same raster are not a supported
  default.
- Screenshot diffs alone cannot produce `PASS`; DOM contract, composition metrics, accessibility,
  and human review remain separate gates.

## MIT notice

`scripts/image-metadata.mjs` is adapted from the upstream file of the same name. It retains the
upstream MIT license and copyright notice in that source file. All other listed mechanisms were
independently integrated into this Skill's existing semantic-surface and launcher architecture.

# Optional asset packs

Use optional packs only after the core deck workflow is complete. Never infer
redistribution or trademark permission from this skill's MIT license.

## Admission workflow

1. Keep external packs outside the core npm artifact.
2. Pin an upstream release, commit, archive digest, or dated snapshot.
3. Record every upstream source once under `sources` and every distributed file
   under `assets` with its SHA-256 digest.
4. Preserve license notices for MIT-like and CC BY sources. Include the exact
   attribution string for CC BY assets. Notice bytes are part of the audit
   tree fingerprint; empty notices fail admission.
5. Mark trademark-bearing sources and provide the owner's current usage-policy
   URL. Keep every external pack opt-in.
6. In a `mixed` pack, classify every source with `content_kind`. For brand
   logos or other brand assets, require user-supplied artwork, an official
   redistribution license, or documented written permission, plus either an
   HTTPS permission record or an in-pack evidence file whose SHA-256 is pinned.
   A style approximation or a public logo download is not sufficient.
7. Declare each immediate `derived_from` asset. CC BY derivatives must record
   non-empty modifications; self-reference, missing parents, and cycles fail.
8. Run `scripts/asset_pack_audit.py` without network access. Admit only a
   `status: pass` report whose file hashes match the candidate pack.

The machine-readable contract is
`reference/schemas/asset-pack.schema.json`.

## P3 source policy

| Candidate | Core package | Optional pack | Required treatment |
|---|---|---|---|
| Tabler / Phosphor icons | No | Eligible | Pin release; retain MIT notices |
| CHUNK icons | No | Eligible | Retain CC BY 4.0 attribution and modification record |
| Simple Icons | No | Conditional | Recheck per-brand metadata and trademark guidance at use time |
| Kenney / verified CC0 sounds | No | Eligible | Pin archive and hashes; retain provenance and CC0 evidence |
| Brand design specifications | No | Conditional | Label official facts vs. approximations; do not imply endorsement |
| Logos and proprietary brand assets | No | User-authorized only | Require explicit permission basis and redistribution evidence |

Passing this technical audit is a release gate, not legal advice. Re-audit
licenses and trademark policies whenever a pack is refreshed.

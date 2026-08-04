# Theme governance

Use this contract for internal production use, external pack intake, release packaging, and
open-source publication. It is independent from visual taste.

## Trust model

The catalog registry has three tiers:

- `builtin`: source-controlled, recursively hashed canonical pack.
- `local-private`: trusted for this user or organization only and always excluded from
  redistribution (`distribution-excluded`).
- `external`: untrusted until its detached Ed25519 signature verifies against an explicitly
  trusted publisher.

Trust is not inferred from a folder name, a successful CSS parse, a GitHub star count, or an
image looking harmless. External signatures cover the deterministic recursive package hash;
symbolic links and the signature descriptor itself are excluded from the signed payload.
Publisher IDs must be unique, public keys must parse as Ed25519 public material, and
signatures must be canonical 64-byte Base64 values.

The trusted-publisher store is intentionally empty by default. Adding a publisher is a separate
security decision and must record the publisher identity and Ed25519 public key. Private keys
never belong in the Skill, catalog, logs, or test fixtures.

## Internal versus open-source distribution

Keep the runtime, compiler, schemas, CSS-only presets, tests, and documentation
redistributable when their licences allow it.

Keep user screenshots, company marks, confidential prompts, internal paths, copyrighted
character art, and assets licensed only for personal/local use outside public release archives.
Registry entries for those packs use:

```json
{
  "tier": "local-private",
  "distribution": "excluded"
}
```

A reusable MIT theme implementation does not make its bundled artwork redistributable. Release
packaging must consume the registry distribution list rather than copy the entire preset
directory.

## Asset governance

Every asset requires explicit licence and provenance. The canonical policy additionally:

- forbids remote resources and CSS imports
- permits only declared CSS, JSON, Markdown, PNG, JPEG, and WebP package files
- rejects symbolic links and excessive directory depth
- enforces per-file, file-count, total-package, dimension, and pixel limits
- records SHA-256, dimensions, aspect class, file count, and package size
- rejects a `redistributable` declaration when licences say personal use only or forbid
  redistribution

Generated or edited art should record the generating tool, date, source relationship, exact
prompt location when retained, and redistribution boundary. Do not expose private absolute
paths in a public package.

## Compatibility matrix

`assets/compatibility.json` separates:

- an exact build with retained evidence (`PASS` or explicitly limited `PARTIAL`)
- a known release family that still requires live semantic verification (`PARTIAL`)
- an unknown/out-of-family build (`UNSUPPORTED`)

`PARTIAL` never silently becomes `PASS`. `UNSUPPORTED` fails closed and keeps Codex native.
Update the matrix only with dated evidence; do not broaden a version range merely to make a
doctor check green.

When an installed Codex build is outside the matrix and a verified loopback renderer already
exists, `doctor` runs the native compatibility probe automatically. This structure-only probe is read-only and
uses the centralized selector contract at L1/L2; it records only semantic-part tiers and counts,
visibility, viewport geometry, overflow, media preferences, and selector-relevant structural
fingerprints when a required anchor is missing. It never records text, titles, URLs, input values,
file paths, HTML, task names, repository names, or message content. Ordinary users do not run a
command or collect JSON—the agent runs the audit and explains the result.

A passing probe is candidate evidence for one exact build and produces `PARTIAL` only. Before
recording it, review every newly discovered stable anchor and keep native topology solely in
`assets/selectors.json`. Store the sanitized evidence in `assets/compatibility-evidence`, bind its
SHA-256 plus the exact selector-topology hash in the build entry, and re-run the complete static
contract. The topology hash covers contract identity, scopes, and parts while excluding only
human provenance metadata; the runtime payload hash still covers the complete selector file.
Governance rejects changed evidence, path traversal, private/unrecognized fields,
non-qualified targets, stale selector hashes, or a probe that claims `PASS`. A selector-contract
change deliberately invalidates all probe evidence captured against the previous hash.

The adapters enforce compatibility before any themed restart or runtime refresh. If the exact
build remains `UNSUPPORTED`, they must stop before showing a restart workflow, closing Codex,
changing a watcher, or injecting CSS. Restore and read-only diagnostics remain available.

The matrix pins runtime, authoring, compiler, governance, selector, compatibility-probe, fixture,
live-QA, and static quality contract versions. A contract bump without a matching matrix and
migration is invalid.

## Schema migration

Theme authoring schema v1 remains readable. Schema v2 adds the creative brief and experience
contract. Migration rules are:

- infer legacy intent in memory for validation and runtime compatibility
- never rewrite a live theme automatically
- write only to a separately named output file
- preserve tokens, profiles, artwork descriptors, low-level composition, licence, and
  provenance
- validate and review the new file before any replacement

Runtime state v1 migrates in memory to state v2. The next authorized state write persists v2,
including bounded launch history and staged rollback metadata.

## Rollback

`rollback` is a non-disruptive staging action:

- choose an explicit trusted theme or the recorded previous theme
- update `selectedTheme` and `nextLaunchTheme`
- record `stagedRollback`
- do not change `loadedTheme`
- do not inject, refresh, close, or restart Codex

The user must separately authorize an apply/start transaction. `Codex Original` remains the
native emergency path.

## Diagnostics

Diagnostics are read-only. They combine:

- runtime and selector contracts
- compiler output and coordinate ownership
- catalog trust/package hashes and distribution classification
- compatibility result
- optional state migration and staged rollback evidence

Static diagnostics always report `PARTIAL`; only live renderer QA across the required
state-by-viewport matrix can produce full `PASS`. Never ask an ordinary user to run these
commands. The agent invokes them and translates results into plain language.

## Portable canonical install

The canonical path is derived from:

```text
${CODEX_HOME:-<user-profile>/.codex}/skills/customize-codex-theme
```

It must not embed an employee username. Windows and macOS adapters refuse runtime mutations
when launched from another copy. Repository worktrees may be used for development and tests,
but they are never runtime authorities and must not leave launchers or watcher records pointing
at them.

## Release checklist

- Canonical runtime hash matches the complete static package.
- Registry, compatibility, and retained compatibility-evidence contracts validate.
- External packs verify an approved publisher signature.
- Local-private packs are absent from public archives.
- Schema migration dry-run is reviewed.
- Static quality has no errors.
- Required live fixtures match the exact engine, payload, style, selector, and Codex versions.
- Rollback and Codex Original paths remain intact.

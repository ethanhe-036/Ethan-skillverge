# Legacy theme migration contract

Read this file before importing, porting, or repairing a theme created for another Codex version or injection engine. Migration preserves visual intent without inheriting unsafe ownership assumptions. Pixel fidelity is subordinate to native behavior, accessibility, and task completion.

## 1. Establish the source and native baseline

Record the source path or commit, license and artwork provenance, source asset hashes, the Codex version it last supported, and privacy-safe screenshots of its known-good surfaces. Separately capture a native baseline from **Codex Original** for geometry and component state. Do not use a themed screenshot as proof of native size, clipping, hit targets, or interaction.

Capture only privacy-safe computed style and geometry evidence: foreground/background, border color and width, radius, shadow, opacity, overflow, element bounds, scroll dimensions, focus outline, and state attributes. Never retain task text, titles, URLs, repository names, drafts, file paths, or message content.

## 2. Create a migration ledger

Create a migration ledger before writing the new pack. Give every source asset, token, selector group, and behavioral rule one disposition:

| Disposition | Use it when | Required evidence |
| --- | --- | --- |
| **preserve** | Artwork, licensed assets, color intent, or a paint rule already uses a stable owned anchor | Source hash/provenance and the target surface |
| **translate** | The intent is valid but the old selector, token, or injector API is obsolete | New stable `data-*`, role/state, verified class, or native relationship plus before/after evidence |
| **drop** | The rule owns native geometry, depends on localized text/position, or has an unbounded blast radius | Reason for removal and the native behavior that replaces it |

Never judge completeness by copied line count. Do not move pack-specific fixes into shared `base.css` merely to make one migration look correct.

The following source rules require **translate** or **drop**, never mechanical preservation:

- a global or unscoped `button`, `[role="button"]`, `aside button`, or similar native-control selector that paints unrelated states;
- `form:has(textarea)` or `form:has([contenteditable])`; `form:has` is not stable composer ownership;
- localized text or `aria-label`, `nth-child`, screen coordinates, screenshot geometry, and generated utility classes without a verified semantic contract;
- native border width, radius, overflow, padding, sizing, positioning, transforms, opacity, or typography overrides;
- a selector that merges primary, secondary, destructive, disabled, selected, close, and icon-only controls into one visual state.

### Authoring-schema migration

Compile a schema-v1 theme in memory before considering source changes. The
compiler must preserve the existing low-level composition while inferring the
three-answer `creativeBrief`, flexible `experience` topology, responsive
policy, and single-coordinate ownership contract.

Migration never rewrites a live pack. It writes a separately named schema-v2
output, preserves licence/provenance and the preserve/translate/drop ledger,
then validates the new file. A clean compiler projection is compatibility
evidence, not permission to replace the source or apply the theme.

## 3. Validate the migrated surface matrix

Run static audit first, then compare the native baseline and migrated theme on the same Codex version, viewport, zoom, and component state. Paint may differ; bounds, scroll behavior, clipping, order, hit targets, focus visibility, and state semantics must remain native.

At minimum inspect:

- sidebar scroll root, scrollbar, selected task, side-task rows, tabs, add/close buttons, and long-list states;
- composer surface, composer fade and neighboring gradient, placeholder, attachments, permission/model controls, and send/stop states;
- dialog surfaces including rename, confirmation, destructive, primary, secondary, disabled, and keyboard-focus buttons;
- menus, popovers, tooltips, settings, code, diff, approval, and both terminal mounts;
- wide, split, narrow, high-DPI, hover, active, selected, disabled, increased-contrast, reduced-transparency, and forced-color states.

For every unexpected difference, inspect the narrow shared root cause. Never repair a missing component style with a global native-control selector. Re-run the complete matrix after changing shared CSS.

## 4. Hot repair eligibility

Treat disk pack, full payload hash, live CSS hash, runtime record, and watcher cache as separate evidence. A hot repair is eligible only when all of these are true:

1. the exact official renderer, loopback owner, target ID, theme ID, and loaded hash verify;
2. the recorded watcher PID, executable, start time, command, port, and theme still match one live watcher;
3. the disk pack hash, generated payload hash, payload revision, and style revision correspond to the intended repair;
4. the live CSS hash and live renderer revisions match the pre-repair recorded payload, so the starting state is known;
5. the user authorized live observation and no interaction with a running task is required.

If the watcher is stale, missing, or mismatched, do not start a second watcher and do not use a one-shot style injection as a hot repair. Validate and save the disk pack, report `PARTIAL`, and require the next authorized **Codex Themes** launch. A one-shot visual change without watcher ownership and reload persistence is not a fix.

When hot repair is eligible, use only the recorded watcher path. After the change, verify the new disk/payload/live hashes, observe one watcher update, and perform one authorized renderer reload to prove persistence. Any mismatch remains `PARTIAL` and triggers the rollback boundary in [repair.md](repair.md).

## 5. Completion record

Retain the migration ledger, static audit result, Codex version, privacy-safe baseline/differential evidence, final disk and payload hashes, runtime status, unverified surfaces, and rollback result. A migrated theme cannot be `PASS` until [surface-qa.md](surface-qa.md) and the authorized task-completion gate pass; screenshot similarity alone is `PARTIAL`.

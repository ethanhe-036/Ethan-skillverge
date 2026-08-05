# Review Checklists

Derive obligations from the Review Basis and change risk. Use the smallest checklist that covers the real risk; do not wait for magic words such as `仔细` or `完整路径`.

## Risk Classification

### Low

Use for local, reversible changes with no external contract or persistent state.

- Keep the Review Basis compact.
- Run targeted evidence.
- Run one adversarial Challenge with a distinct lens.

### Medium

Use when the change crosses modules or affects API/schema, persistent state, configuration, asynchronous behavior, packaging, or a production entry path.

- Build an Impact Map.
- Cover affected callers/consumers and negative paths.
- Verify compatibility and recovery behavior where applicable.

### High

Use for auth/security, tenant isolation, data loss, migrations, billing, concurrency, irreversible side effects, or deploy.

- Require a real independence source for the Challenge.
- Exercise deny paths, failure/recovery, compatibility, and rollback.
- Require explicit user or policy acceptance for residual risk.

## Review Basis

- Requirements and acceptance criteria are explicit.
- Non-goals and invariants are explicit.
- Baseline behavior is reproduced or characterized.
- Open product/API decisions are questions, not hidden assumptions.
- Impact paths include unchanged but affected callers and consumers.
- Every high-value requirement or risk has an obligation and an oracle.
- Expected values do not come only from the implementation under test.

## Full-Path Obligations

Apply the relevant axes:

- **Entry and routing**: real production/test/build/package entry points, flags, providers, and branch conditions.
- **Call and integration path**: callers, callees, consumers, generated artifacts, configuration, and deployment wiring.
- **Data and schema**: persistence, old data, migrations, serialization, validation, and version mixing.
- **State and time**: cache, retries, resume, idempotency, ordering, concurrency, recovery, and cleanup.
- **Authorization and isolation**: allow and deny paths, identity, ownership, tenant boundaries, and secret exposure.
- **Contracts and compatibility**: API/schema/event/error contracts, old/new clients, and rollback behavior.
- **Side effects and operations**: billing, telemetry, logs, DB writes, queues, resource cleanup, rollout, and observability.
- **User-visible behavior**: relevant locales, accessibility, error states, and degraded behavior.
- **Tests and oracle quality**: focused failure, fixed behavior, nearby regression, independent expected values, and consciously documented gaps.

## Implementation Review

- Each changed line traces to a Contract item or confirmed finding.
- The change is the smallest coherent solution.
- New abstractions solve a current requirement rather than speculative future needs.
- New impact paths discovered during implementation are added to the Review Basis.
- Evidence invalidated by a change is marked stale before reuse.
- Relevant staged, unstaged, and untracked files are included in the target `state_id`.

## Challenge Gate

- Use a lens different from the primary review.
- Recheck Contract omissions, unchanged affected paths, negative cases, and oracle correlation.
- Recheck the complete Findings Registry, accepted Residuals, uncovered obligations, and stale evidence.
- For high-risk work, record a genuine independence source.
- For Coda Skill changes, run manifest/alias/state parity and Ralph negative fixtures.
- Treat any new P0/P1/P2 as a new round, not as a clean result.

## Review-Fix Stop Gate

Before `VERIFIED / COMPLETE`:

- No unaccepted P0/P1/P2 remains.
- Required obligations have PASS evidence on the final `state_id`.
- No required evidence is stale.
- The final pass is a clean Challenge, not a fix pass.
- Residual risks name `accepted_by` and rationale.
- Coverage limits are explicit.
- The stop is not caused only by a customary round count.

## Review-Only Stop Gate

Before `FINDINGS_READY / COMPLETE`:

- Review-only protection held: no files were edited and no auto-fix command ran.
- All findings are classified and duplicates reference originals.
- The lossless registry retains every unresolved item.
- The latest clean Challenge added no P0/P1/P2.
- Covered and uncovered obligations and evidence limits are explicit.

## Plan Review

- Requirements map to executable tasks.
- File paths, functions, and interfaces exist or are introduced deliberately.
- Tasks are ordered by dependency.
- The highest-risk behavior has a planned independent oracle.
- Rollout, migration, and rollback tasks exist when relevant.
- Placeholders and unresolved decisions are explicit.

## Diff Review

- Review staged, unstaged, and relevant untracked changes.
- Inspect unchanged affected paths, not only changed files.
- Prioritize behavioral bugs, regressions, unsafe side effects, and missing evidence.
- Ground findings in file/line, diff, state, or log evidence.
- Skip style findings unless they materially affect correctness or maintainability.

## Negative Constraints

- If the user says `只review`, `不要修改`, `先回复我`, or equivalent, do not edit or run auto-fix tools.
- If the user says `先不要commit`, editing and checks may occur only when otherwise authorized; do not commit.
- Never infer commit, push, real merge, deploy, or risk acceptance from a review/workflow operator.

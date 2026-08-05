# Modes

Use [protocol.yaml](protocol.yaml) as the canonical vocabulary for modes, aliases, authority fields, readiness, stop reasons, severity, and lifecycle phases. Load it when changing/validating Coda or resolving a conflict; ordinary runs can follow this file. This file explains behavior and must not create additional states or capabilities.

## Common Invariants

- Calculate authority from the user's explicit grant, the environment, and the mode ceiling. A mode may remove capabilities but never add them.
- Let `no-edit`, `do not commit`, `do not push`, and similar negative constraints dominate conflicting operator text.
- Keep readiness separate from the stop reason:
  - readiness: `NOT_READY | FINDINGS_READY | VERIFIED | DELIVERY_READY`
  - stop reason: `COMPLETE | ASK_USER | BLOCKED | BUDGET`
- Bind evidence to the target `state_id`; invalidate stale evidence after relevant changes.
- Keep a lossless Findings Registry with stable IDs. A five-item limit applies only to the current focus view.
- Require `accepted_by: user | explicit_policy` plus rationale for Residual risk.
- Derive coverage from Contract and change risk. Prompt words can increase coverage but cannot suppress risk-derived obligations.

## review

Use plain `review` for review-only convergence.

The following default to review-only:

- plain `review`
- `REVIEW方案`
- `仔细review`
- `再次review`
- `最终review`
- `review一下上述更改`
- `review-loop`
- `review-only` / `review only`
- `只review`, `先不要修改文件`, `不要改文件`

Do not edit merely because the user omitted no-edit language.

### Fixed review N

Interpret `review N` as a fixed review-lens budget:

- Accept `1-5`.
- Clamp larger values to `5` and report the clamp.
- Default to read-only.
- If the surrounding task independently and explicitly grants fixing/editing, use review-fix with `N` as its round budget.
- Use distinct lenses instead of repeating the same pass.
- Stop early only for `ASK_USER` or `BLOCKED`.
- At the round limit, report the actual readiness and `stop_reason=BUDGET` if required obligations remain. Never turn the round count into `VERIFIED`.

Per round:

1. Read the prior delta and unresolved registry items.
2. Apply a distinct review lens.
3. Add or update findings and coverage obligations.
4. Record only the round delta.

## review-fix

Use `review-fix` only when the user explicitly asks to fix, edit, adjust, implement, execute, or continue until clean. Examples:

- `review-fix loop`
- `review fix-loop`
- `review fix loop`
- `fix-loop`
- `有问题调整直到没问题`
- `修完再review`
- `fix until clean`
- `review -> fix -> test -> review`

If explicit edit authority is absent, downgrade to review-only or stop with `ASK_USER`. If the user also says not to edit, the no-edit constraint wins.

Review-fix is convergence-based:

1. Record the Run Header and Review Basis.
2. Review Contract coverage, affected paths, diff correctness, negative paths, and oracle quality.
3. Classify every finding by stable ID, severity, and status.
4. Fix only confirmed, in-scope findings that edit authority covers.
5. Mark affected prior evidence stale.
6. Run targeted evidence for the changed obligations.
7. Update only the round delta and continue from the registry.
8. Run the risk-appropriate Challenge Gate on the final state.
9. Recompute readiness from final-state evidence.

Do not stop because a customary number of rounds has run. Stop only with:

- `VERIFIED / COMPLETE`: required final-state evidence is valid, no unaccepted P0/P1/P2 remains, and the final Challenge is clean.
- `NOT_READY / ASK_USER`: a product, architecture, scope, or risk-acceptance decision is required.
- `NOT_READY / BLOCKED`: permissions, artifacts, environment, or dependencies prevent required work or evidence.
- `NOT_READY / BUDGET`: the agreed budget ends before convergence.

Any edit after final evidence returns readiness to `NOT_READY` until affected obligations are reverified.

## review-loop

Use `review-loop` for review-only convergence. Never write files or run auto-fix commands.

Loop:

1. Establish the Review Basis and risk-derived obligations.
2. Review the target and update the lossless Findings Registry.
3. Verify or classify Suspected findings.
4. Expand uncovered impact and negative paths.
5. Run a clean Challenge pass.
6. Stop when the inventory is stable and coverage limits are explicit.

Use:

- `FINDINGS_READY / COMPLETE` when all findings are classified, duplicates reference their originals, the latest Challenge adds no P0/P1/P2, and uncovered scope/evidence limits are explicit.
- `NOT_READY / ASK_USER` when scope or risk acceptance needs user judgment.
- `NOT_READY / BLOCKED` when required evidence cannot be accessed.
- `NOT_READY / BUDGET` when the agreed budget ends before findings are ready.

Review-only may establish `FINDINGS_READY`; it must not imply that fixes were applied or that delivery is ready.

## Risk-Driven Challenge

- For `low` risk, use one adversarial same-context Challenge with a lens different from the primary review.
- For `medium` risk, include cross-module, state, compatibility, async, and negative-path obligations as applicable.
- For `high` risk, require at least one genuine independence source: fresh context or reviewer, independent specification/oracle, property/mutation/fault technique, migration/rollback exercise, or old/new compatibility test.

Call a same-context re-scan `Challenge`, not `Independent Challenge`.

For reviews of the Coda Skill itself, run automated manifest/alias/state parity tests instead of manually reproducing an Entry Surface Parity checklist every round.

## x3

Use `x3` for three cumulative refinement passes on the same task:

1. Produce a serious first result.
2. Improve issues visible after pass one.
3. Tighten the final state without restarting.

`x3` does not grant edit authority and is not candidate competition.

## v3

Use `v3` for three distinct candidates:

- summarize each candidate;
- compare them;
- recommend a winner;
- provide candidate-level change summaries.

Do not auto-pick into a working tree or auto-merge without explicit authority.

## vs

Use `vs` for two explicit alternatives. Keep both sides distinct and report differences, tradeoffs, and an optional recommendation if requested.

If two sides are not clear, ask the user unless the split is obvious and low-risk.

## pick

Use `pick` to select one conceptual candidate and continue from it. Do not interpret it as `git cherry-pick`.

## compare

Use `compare` for side-by-side analysis without selection or synthesis.

## merge

Use `merge` to synthesize a hybrid result from candidates. Explain which strengths came from each side. Do not interpret it as `git merge`, branch merging, or working-tree overwrite.

## ralph

Use `ralph` only with an explicit task source.

1. Run `scripts/extract_task_candidates.py <path>`.
2. Stop on `NO_ACTIONABLE_TASK`.
3. Prefer `NEXT`, then `TODO`, then actionable unchecked/numbered items in source order.
4. Execute only the chosen item within granted authority.
5. Report the item result and propose the exact task-source update.

Accept:

- unchecked Markdown checkboxes;
- `NEXT:` and `TODO:` markers;
- numbered items inside an explicit Tasks, Steps, Action Items, or Implementation Plan section.

Ignore:

- checked boxes;
- `DONE:` markers;
- arbitrary numbered prose;
- content inside backtick or tilde fences.

Do not mutate the task source unless the user authorized that update.

## Combined Operators

Compose operators in this order:

1. `ralph` wraps task-source iteration.
2. `review` gates the work strategy.
3. `x3`, `v3`, or `vs` selects the strategy.
4. `pick`, `compare`, or `merge` resolves candidates.

Composition never expands authority.

Examples:

- `review x3`: review-only by default; use three cumulative lenses or refinements within the granted capability set.
- `review v3`: compare candidates inside the review gate.
- `v3 review`: create candidates, recommend one, then review that result.
- `v3 pick review`: select a candidate, then review it.
- `Approach A vs Approach B merge`: synthesize a conceptual hybrid.
- `ralph review`: execute each actionable task item through a review gate without inferring extra permissions.

# Invocation Examples

Use these as golden routing fixtures. Operators describe workflow semantics; they do not grant capabilities.

## Review and Authority

### Plain review

`Use $coda to review the current diff.`

- Mode: `review_only`
- Authority: `edit=false`
- Target: staged, unstaged, and relevant untracked changes

`$coda REVIEW方案 docs/plan.md`

- Mode: `review_only`
- Authority: `edit=false`
- Review the plan against source, tests, feasibility, and rollout risk.

`写完计划以后用 CODA 再次仔细review`

- Mode: `review_only`
- Authority: `edit=false` unless the surrounding request separately and explicitly grants edits.
- Upgrade coverage because the prompt asks for careful review.

`$coda review一下上述更改`

- Mode: `review_only`
- Authority: `edit=false`
- Inspect the current diff and relevant untracked files.

### Explicit review-fix

`Use $coda to review-fix this patch; edits are allowed; do not commit or push.`

- Mode: `review_fix`
- Authority: `edit=true`, `commit=false`, `push=false`
- Fix confirmed in-scope findings, invalidate stale evidence, verify, then Challenge the final state.

`有问题直接修改直到通过，但不要commit`

- Mode: `review_fix`
- Authority: `edit=true`, `commit=false`

`review-fix，但不要修改文件`

- Mode: `review_only` or `ASK_USER`
- The negative edit constraint dominates the edit-capable operator.

### Fixed review budget

`$coda review 3 当前改动，只review`

- Mode: `fixed_review`
- Authority: `edit=false`
- Run three distinct review lenses.
- If blockers or uncovered obligations remain after round three, report `NOT_READY / BUDGET`; do not infer `VERIFIED`.

`$coda review 8 当前改动`

- Clamp the review budget to `5` and report it.

`修复这个问题，然后 review 3`

- Mode: `review_fix` with a three-round budget because fixing is independently explicit.
- Authority: `edit=true`
- If convergence is not reached after round three, report `NOT_READY / BUDGET`.

## Risk-Driven Coverage

`Use $coda to review this authentication change.`

- Mode: `review_only`
- Risk: `high`, even without `仔细` or `完整路径`.
- Include deny paths, tenant/resource ownership, secret handling, and a genuine independence source.

`Use $coda to review this schema migration and rollback.`

- Risk: `high`
- Include old data, old/new version mixing, idempotency, failure recovery, and rollback obligations.

## Work Strategies

`cook "improve this approach" x3`

- Run three cumulative refinements.
- Do not create competing candidates.
- Do not infer edit authority from `x3`.

`cook "build this feature" v3`

- Produce three distinct candidates and recommend a winner.
- Do not auto-merge or write the winner without edit authority.

`review v3`

- Use candidate comparison inside the review gate.
- Plain review remains read-only.

`v3 review`

- Produce candidates, recommend one, then review the selected result.
- Keep this composition distinct from `review v3`.

`cook "Approach A" vs "Approach B" compare`

- Keep both sides separate.
- Report differences and tradeoffs without selecting or synthesizing.

`cook "Approach A" vs "Approach B" pick`

- Choose one conceptual winner and explain why.
- Do not interpret `pick` as `git cherry-pick`.

`cook "Approach A" vs "Approach B" merge`

- Synthesize a conceptual hybrid.
- Do not run `git merge` or write files without separate authority.

## Ralph

`cook @docs/plans/example.md ralph`

- Require an explicit task source.
- Prefer `NEXT`, then `TODO`, then other actionable items.
- Ignore checked items, `DONE`, numbered prose outside task sections, and fenced examples.
- Stop on `NO_ACTIONABLE_TASK`.

`ralph @tasks.md，执行下一个 TODO；允许改代码，不要commit`

- Mode: `ralph` with explicit edit authority for the selected task.
- Authority: `edit=true`, `commit=false`
- Do not mutate the task file unless that update is also authorized.

`cook @docs/plans/example.md review ralph`

- Let `ralph` wrap task iteration and `review` gate each item.
- Review remains read-only unless the task request separately grants editing.

## Compatibility Entry Styles

All of these invoke Coda semantics:

- `Use coda to ...`
- `Use $coda to ...`
- `coda "..." review`
- `cook "..." review`
- `$cook-codex ...`
- `COOK ...`

Do not claim full syntax compatibility with an external cook CLI.

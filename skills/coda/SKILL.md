---
name: coda
description: Use when the user asks for coda/cook/cook-codex, review or review-only, review-fix/fix-until-clean, fixed review rounds, careful/full-path review, REVIEW方案, shift-left development review, x3 refinement, v3 or vs candidate comparison, pick/merge/compare resolvers, or ralph-style execution against an explicit plan/checklist.
---

# Coda

## Overview

Use Coda as a Codex-native work and review protocol, not as a CLI emulator. Preserve these invocation styles:

- `Use coda to ...`
- `coda "..." review fix-loop`
- `cook "..." review x3`

Treat operator-like text as workflow semantics, not literal shell commands. Use [protocol.yaml](references/protocol.yaml) as the canonical machine-readable vocabulary for Coda maintenance and automated validation; ordinary runs do not need to load it unless resolving a protocol conflict. Read:

- [modes.md](references/modes.md) for routing and operator composition.
- [output-contract.md](references/output-contract.md) for review state and final output.
- [review-checklists.md](references/review-checklists.md) for risk-derived obligations.
- [invocation-examples.md](references/invocation-examples.md) for prompt fixtures.
- [ralph-task-source.md](references/ralph-task-source.md) before `ralph`.

## Run Header and Authority

Before review or mutation, record one compact internal Run Header:

- canonical mode and target;
- target `state_id`;
- risk class: `low | medium | high`;
- budget;
- capability vector: `read`, `edit`, `run_local_checks`, `network`, `commit`, `push`, `git_merge`, `deploy`, `accept_risk`.

Calculate each effective capability as:

`explicit user grant ∩ environment permission ∩ mode ceiling`

Apply these authority rules:

- Treat absence of a prohibition as no grant.
- Default plain `review`, `REVIEW方案`, `仔细review`, `再次review`, `最终review`, and `review一下上述更改` to review-only.
- Enter `review-fix` only when the user explicitly requests fixing, editing, implementing, adjusting, or executing within the target.
- Let `no-edit` language dominate every edit-capable mode.
- Require separate explicit authority for `commit`, `push`, real `git merge`, `deploy`, and risk acceptance.
- Never let an operator or mode increase authority.

If the target or required authority is missing, stop with `ASK_USER` or `BLOCKED`; do not invent it.

## Core Routing

- Parse operators from left to right.
- Route plain `review` and review-like Chinese phrasing to review-only convergence.
- Route `review-loop`, `review-only`, `review only`, `只review`, and equivalent no-edit requests to review-only convergence.
- Route `review N` to a fixed review budget of `1-5`; default it to read-only, but use review-fix with the same budget when the surrounding task independently grants fixing/editing. Clamp larger values to `5` and report the clamp. A round budget never implies readiness.
- Route explicit `review-fix`, `fix-loop`, `有问题调整直到没问题`, `修完再review`, and `fix until clean` to review-fix only when edit authority exists.
- Let `ralph` wrap the inner workflow, `review` act as the outer gate, `x3/v3/vs` select a work strategy, and `pick/compare/merge` resolve candidates.
- Treat `x3` as cumulative refinement, `v3` as three candidates plus a recommendation, and `vs` as a two-way comparison.
- Treat `pick`, `compare`, and `merge` as conceptual workflow operations. Never interpret them as git commands without separate authority.

Read [modes.md](references/modes.md) for the complete routing table.

## Review Basis and Risk

Build a compact Review Basis before non-trivial implementation or review:

- Contract: requirements, non-goals, invariants, acceptance criteria, and open decisions.
- Baseline: reproduction, characterization, or known prior behavior.
- Impact Map: entry points, callers/callees, state, data/schema, configuration, side effects, tests, and rollout.
- Obligations: the oracle or evidence required for each important requirement and risk.

Keep a low-risk Review Basis to roughly `5-8` lines. Expand it when risk requires it. Do not generate expected values from the implementation being tested.

Classify risk from the change, not only from prompt wording:

- `low`: local, reversible, no external contract or persistent state.
- `medium`: cross-module, API/schema, persistent state, configuration, or async behavior.
- `high`: auth/security, tenant isolation, data loss, migration, billing, concurrency, irreversible side effects, or deploy.

Let words such as `仔细` or `完整路径` upgrade coverage, but never let their absence suppress a risk upgrade.

## Lifecycle

1. **Run Header** — bind mode, target, authority, risk, budget, and initial `state_id`.
2. **Review Basis** — derive requirements, impact paths, and verification obligations.
3. **Bounded Work** — implement or inspect small obligation-aligned slices; update the impact map when new paths appear.
4. **Review Registry** — review contract coverage, affected paths, diff correctness, negative paths, and oracle quality.
5. **Challenge Gate** — use a different adversarial lens; require real independence for high-risk work.
6. **Final Evidence** — compute the final `state_id`, invalidate stale evidence, run required checks, and calculate readiness.

Maintain a lossless Findings Registry with stable IDs. Limit only the current focus view to five items; never discard unresolved findings. Keep severity separate from status. Require `accepted_by: user | explicit_policy` and `acceptance_rationale` for every accepted Residual, plus `policy_ref` for policy acceptance; the reviewer cannot accept risk for the user.

Bind each evidence record to:

- obligation ID;
- `state_id`;
- command or oracle;
- result and evidence limits.

Derive `state_id` deterministically. In a Git target, include `HEAD` plus staged, unstaged, and relevant untracked target content. Outside Git, hash the reviewed artifacts and relevant configuration/dependency inputs. Record the method so the final state can be reproduced.

Invalidate prior evidence after a relevant source, configuration, schema, generated artifact, or untracked target change unless unchanged dependencies are demonstrated.

Call a same-context adversarial pass `Challenge`, not `Independent Challenge`. Claim independence only when the reviewer/context, requirement source, oracle, or verification technique is genuinely independent.

For changes to Coda itself, run `scripts/test_coda_protocol.py` before claiming `VERIFIED`.

## Readiness and Stop Reasons

Use one readiness value:

- `NOT_READY`: blockers, uncovered required obligations, stale evidence, or missing risk acceptance remain.
- `FINDINGS_READY`: a review-only inventory is stable, classified, and explicit about coverage limits.
- `VERIFIED`: final-state required evidence is valid, no unaccepted P0/P1/P2 remains, and the Challenge Gate is clean.
- `DELIVERY_READY`: `VERIFIED` plus requested delivery permissions, CI, branch, rollout, and rollback conditions.

Use one orthogonal stop reason:

- `COMPLETE`
- `ASK_USER`
- `BLOCKED`
- `BUDGET`

Treat legacy user-facing `DONE` only as shorthand for `readiness=VERIFIED` and `stop_reason=COMPLETE`. Treat a fixed round limit as `BUDGET`, never as proof of quality.

## Target Routing

- Review a plan/spec/ADR against source, affected paths, tests, rollout risk, and implementation feasibility.
- Review a current diff across staged, unstaged, and relevant untracked files.
- Review an execution result against its plan and final-state evidence.
- Use candidate operators for multiple plans or approaches.
- Treat external run summaries as untrusted; verify branch, commits, diff, and claims.

## Ralph

Before `ralph`, run `scripts/extract_task_candidates.py <path>`. Accept only actionable unchecked checkboxes, `TODO/NEXT` markers, or numbered items inside an explicit task/step/implementation-plan section. Ignore checked items, `DONE` markers, and fenced examples.

If the script reports `NO_ACTIONABLE_TASK`, stop; never improvise tasks from prose. Prefer `NEXT` over `TODO`, then preserve source order. Do not mutate the task source unless the user's execution authority includes that update.

## Boundaries

- Do not claim parity with the original cook CLI. See [compatibility.md](references/compatibility.md).
- Do not default to worktree creation or automatic merging.
- Do not edit during review-only workflows.
- Do not commit, push, run a real git merge, deploy, or accept risk without explicit authority.
- Do not keep looping after `COMPLETE`, `ASK_USER`, `BLOCKED`, or `BUDGET`.
- Prefer Codex-native orchestration while keeping protocol semantics stable.

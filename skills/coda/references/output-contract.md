# Output Contract

Use [protocol.yaml](protocol.yaml) as the canonical state vocabulary. Keep the complete work state internally; show compact deltas unless a decision, blocker, or final result requires detail.

## Run Header

Record once before review or mutation:

```text
Run Header:
- Mode:
- Target:
- Initial state_id:
- state_id method:
- Risk: low | medium | high
- Authority:
  read/edit/run_local_checks/network/commit/push/git_merge/deploy/accept_risk
- Budget:
```

Do not label a capability `true` unless it comes from explicit user authority and is allowed by the environment and mode. Plain review starts with `edit=false`.

Build `state_id` deterministically:

- In Git, include `HEAD` plus staged, unstaged, and relevant untracked target content.
- Outside Git, hash the reviewed artifacts and relevant configuration/dependency inputs.
- Reuse the same method for the final state so evidence freshness is reproducible.

## Review Basis

For low-risk work, keep this to roughly `5-8` lines. Expand only for real risk.

```text
Review Basis:
- Contract:
  - R1 requirement / acceptance criterion
  - invariants:
  - non-goals:
  - open decisions:
- Baseline:
- Impact Map:
- Obligations:
  - O1 requirement or risk -> required oracle/evidence
```

Do not hide unresolved product decisions as assumptions. Use `ASK_USER` when an answer materially changes the implementation or correctness criterion.

## Severity and Finding State

Use:

- `P0`: immediate catastrophic impact, data loss, critical security breach, or system-wide outage.
- `P1`: major correctness, security, or compatibility failure with no safe ordinary workaround.
- `P2`: meaningful defect or regression with bounded impact or a practical workaround.
- `P3`: minor issue, maintainability concern, or optional improvement.

Keep severity separate from status:

`Confirmed | Suspected | Fixed | Dismissed | Duplicate | Residual`

Maintain a lossless registry:

```text
Finding:
- id: F-001
- severity: P0 | P1 | P2 | P3
- status:
- title:
- evidence: file:line | diff hunk | log | explicit missing evidence
- impact:
- obligation:
- fix:
- duplicate_of:
- accepted_by: user | explicit_policy
- acceptance_rationale:
- policy_ref:
```

Rules:

- Never delete an unresolved finding because the current focus list is full.
- Limit the displayed focus list to five items, not the registry.
- Require a referenced original for `Duplicate`.
- Require `accepted_by` and rationale for `Residual`.
- Require `policy_ref` when `accepted_by=explicit_policy`.
- Do not let the reviewer accept P0/P1/P2 risk on the user's behalf.

## Evidence

Record evidence against obligations:

```text
Evidence:
- obligation: O1
- state_id:
- command_or_oracle:
- result: PASS | FAIL | NOT_RUN | STALE
- coverage:
- limits:
```

Invalidate evidence after a relevant source, configuration, schema, generated artifact, dependency, or untracked target changes. Carry evidence forward only when its dependency set is demonstrably unchanged. Final readiness may reference only evidence valid for the final `state_id`.

## Round Delta

Do not repeat the full Run Header, Review Basis, registry, or continuation rule every round. Report only:

```text
Round Delta:
- State: previous_state_id -> current_state_id
- New findings:
- Finding status changes:
- Fixes applied:
- Evidence added/invalidated:
- New or completed obligations:
- Next uncovered obligation:
- Current readiness:
```

Start each round from the persistent registry and obligation table. If a fix changes the target, invalidate affected evidence before continuing.

## Challenge Gate

Run the Challenge after the final fix and its targeted verification:

```text
Challenge:
- Lens:
- Independence source: none | fresh_context | second_reviewer | independent_spec | independent_oracle | property | mutation | fault | compatibility | rollback
- Contract/impact/negative paths rechecked:
- Stale evidence check:
- New findings:
- Result: CLEAN | NEW_FINDING | ASK_USER | BLOCKED
```

Use a different adversarial lens from the primary review. Do not claim `Independent` when `Independence source` is `none`. For high-risk work, require at least one real independence source.

If the Challenge produces a new P0/P1/P2, update the registry and continue. A pass that applies a fix is not the final clean pass.

For Coda Skill changes, run automated public-entry, authority, state, and fixture parity tests as part of the Challenge.

## Readiness

Calculate readiness independently from why execution stops:

- `NOT_READY`: an open blocker, uncovered required obligation, stale evidence, or missing legal risk acceptance exists.
- `FINDINGS_READY`: review-only findings are stable and classified; uncovered scope and evidence limits are explicit.
- `VERIFIED`: final-state required evidence passes, no unaccepted P0/P1/P2 remains, and the Challenge is clean.
- `DELIVERY_READY`: `VERIFIED` plus requested delivery authority, CI, branch, rollout, and rollback requirements.

Use exactly one stop reason:

- `COMPLETE`
- `ASK_USER`
- `BLOCKED`
- `BUDGET`

`review-only` is a mode, not a result. A fixed-round limit produces `BUDGET` when required work remains; it never implies `VERIFIED`.

Treat user-facing `DONE` only as the compatibility shorthand:

```text
DONE := readiness=VERIFIED and stop_reason=COMPLETE
```

## Final Output

End every review workflow with:

```text
Result:
- Readiness: NOT_READY | FINDINGS_READY | VERIFIED | DELIVERY_READY
- Stop reason: COMPLETE | ASK_USER | BLOCKED | BUDGET
- Final state_id:
- Mode / Target / Risk:

Contract and coverage:
- Covered obligations:
- Uncovered obligations:
- Evidence limits:

Findings:
- Open P0/P1/P2:
- Residual and accepted_by:
- Fixed:

Evidence:
- Final-state checks:
- Stale or not run:

Challenge:
- Lens:
- Independence source:
- Result:

Authority and delivery:
- Actions performed:
- Actions not authorized or not requested:

Recommended next step:
```

Additional rules:

- For review-only, use `FINDINGS_READY / COMPLETE` only after all findings are classified and the clean Challenge adds no P0/P1/P2.
- For review-fix, use `VERIFIED / COMPLETE` only when all required final-state evidence is valid and no unaccepted P0/P1/P2 remains.
- Use `DELIVERY_READY` only when delivery was requested and its separate conditions pass. It does not mean commit, push, real git merge, or deploy already happened.
- Use `NOT_READY` with `ASK_USER`, `BLOCKED`, or `BUDGET` when the corresponding condition prevents completion.
- Name the exact acceptor for every accepted risk.

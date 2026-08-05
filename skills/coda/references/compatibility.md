# Compatibility

## What Matches Cook

- Supports cook/cook-codex compatibility aliases and cook-like phrasing
- Provides `review`, `x3`, `v3`, `vs`, `pick`, `merge`, `compare`, and `ralph` semantics
- Treats these operators as composable workflow controls
- Keeps fixed-round and convergence-based review semantics distinct

## What Does Not Match Cook

- This is not a full CLI parser.
- This does not promise parity with original cook command syntax.
- This does not default to automatic worktree creation.
- This does not default to automatic merging of winning candidates.
- This treats `merge` as a synthesis resolver, not as an implicit `git merge`.
- This does not infer `ralph` task sources from arbitrary docs.
- This does not treat plain `review` as edit permission.
- This does not infer commit, push, real merge, deploy, or risk acceptance from any operator.

## Why the Differences Exist

The goal of Coda is stable Codex-native orchestration, not a fragile imitation layer. A thin wrapper around the original cook CLI would add double orchestration and make debugging harder.

## Safe Interpretation Rule

When there is tension between:

- literal CLI imitation, and
- stable Codex-native behavior

prefer stable Codex-native behavior and say so explicitly.

## Stable Safety Semantics

- Plain `review` and review-like Chinese phrasing are review-only by default.
- `review-fix` requires explicit edit authority.
- Mode composition can only preserve or reduce authority.
- `DONE` is only a compatibility label for `VERIFIED / COMPLETE`; a round limit is not a quality result.

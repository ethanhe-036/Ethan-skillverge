# Ralph Task Source

## Principle

Run `ralph` only against an explicit, structured task source. Extract actionable items deterministically and fail closed when none exist.

## Actionable Structures

Accept:

- unchecked Markdown checkboxes such as `- [ ] Step`;
- `NEXT:` and `TODO:` markers;
- numbered items such as `1. Step` only while inside an explicit task section.

Recognize task-section headings such as:

- Tasks
- Steps
- Action Items
- Implementation Plan / Execution Plan
- 任务 / 步骤 / 待办 / 实施计划 / 执行计划

Treat nested headings under a task section as part of that section until a heading at the same or higher level closes it.

## Non-Actionable Content

Ignore:

- checked checkboxes;
- `DONE:` markers;
- arbitrary numbered architecture/product prose;
- content inside backtick or tilde code fences;
- indirect work suggestions in free-form notes.

Do not run `ralph` merely because a README or spec contains an ordinary numbered list.

## Extraction

Run:

```text
scripts/extract_task_candidates.py <path>
```

The JSON result contains:

- `status: READY | NO_ACTIONABLE_TASK`;
- `candidate_count`;
- actionable `candidates`;
- `next_candidate`, preferring `NEXT`, then `TODO`, then source order.

Exit codes:

- `0`: actionable candidate found;
- `1`: source cannot be read;
- `2`: no actionable task.

If the result is `NO_ACTIONABLE_TASK`, stop and ask the user to provide or normalize a real task source. Never synthesize a task from prose.

## Execution and Status

Execute only the selected item and only within granted authority. After the iteration:

- name the selected task item;
- report its result;
- report whether another actionable item remains;
- propose the exact task-source update.

Do not silently mutate the task source. Editing code for the selected item does not automatically grant permission to update the task file, commit, push, merge, or deploy.

---
description: Run the full MoonSpec lifecycle for one preselected story.
---

## User Input

```text
{ARGS}
```

You **MUST** consider the user input before proceeding.

## Goal

Coordinate the MoonSpec lifecycle for one independently testable story:

1. Create or select a one-story spec with `moonspec-specify`.
2. Produce planning artifacts with `moonspec-plan`.
3. Generate TDD tasks with `moonspec-tasks`.
4. Align artifacts with `moonspec-align`.
5. Implement through `moonspec-implement`.
6. Verify with `moonspec-verify`.
7. Reconcile canonical docs with `moonspec-doc-reconcile` when verification passes and a canonical source document exists.

Do not split broad designs in this command. If the input contains multiple independent stories, stop and route it to `moonspec-breakdown` before orchestration.

## Rules

- Use MoonSpec skill IDs and command IDs: `moonspec-*` and `/moonspec.*`.
- Preserve one story per `spec.md`.
- Keep TDD as the default strategy.
- Require unit and integration test evidence unless the project explicitly has no relevant integration boundary.
- Treat final verification as the authoritative completion gate.
- Do not claim completion unless `moonspec-verify` returns `FULLY_IMPLEMENTED`.

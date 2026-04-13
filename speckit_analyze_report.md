## Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| I1 | Inconsistency | MEDIUM | `tasks.md`:46-49, 73-74, 96-99, 163-173 | Several tasks marked `[P]` modify the same file (`frontend/src/entrypoints/task-create.test.tsx`). The task notes say `[P]` means different files or no conflicts, while the parallel opportunities section partially walks this back by requiring coordination. | Remove `[P]` from same-file test tasks or split them so only one owner edits `task-create.test.tsx` at a time. |
| C1 | Coverage Gap | MEDIUM | `spec.md`:59, 98; `tasks.md`:116-124 | The spec includes Jira browser failure fallback and manual task creation availability (`SC-005`), but the tasks do not include a dedicated regression task for that behavior. Full-suite validation may cover existing tests, but the feature task list does not make the coverage explicit. | Add a story or polish task that explicitly verifies Jira browser failure remains local and manual creation remains available in `frontend/src/entrypoints/task-create.test.tsx`. |

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 detect-applied-preset-import-change | Yes | T006, T010, T014 | Covered by test-first and implementation tasks for US1. |
| FR-002 exact-reapply-message | Yes | T006, T010, T014 | Exact message is called out in test and implementation tasks. |
| FR-003 no-hidden-step-rewrites | Yes | T007, T011, T014 | Expanded-step preservation is explicitly tested and implemented. |
| FR-004 explicit-reapply | Yes | T015, T017, T018, T019 | Covered by explicit reapply action tests and implementation. |
| FR-005 clear-reapply-action | Yes | T015, T017, T019 | Covered through `Reapply preset` label assertions. |
| FR-006 clear-reapply-state | Yes | T008, T012, T016, T019 | Covered by restoring last applied instructions and label reset. |
| FR-007 unchanged-import-no-op | Yes | T009, T013, T014 | Covered by no-op regression test and implementation. |
| FR-008 detect-template-bound-target | Yes | T020, T024, T025, T028 | Covered by predicate and target-derived warning state. |
| FR-009 warn-template-bound-step | Yes | T020, T026, T028 | Covered by exact warning task. |
| FR-010 allow-template-bound-import | Yes | T021, T027, T028 | Covered by import after warning. |
| FR-011 update-only-targeted-step | Yes | T022, T027, T028 | Covered by targeted-step assertion. |
| FR-012 detach-template-identity | Yes | T021, T023, T027, T028 | Covered by warning disappearance and submit assertion. |
| FR-013 production-runtime-delivery | Yes | T010-T013, T017-T018, T024-T027, T029-T031 | Production code and validation tasks are explicit. |
| FR-014 validation-tests | Yes | T006-T009, T014-T016, T019-T023, T028-T031 | Automated validation tasks exist for each runtime story. |

## Constitution Alignment Issues

None detected. The plan includes a Constitution Check and post-design recheck, runtime work is scoped to production Create page code plus validation tests, and no docs-only substitution is present.

## Unmapped Tasks

No problematic unmapped tasks detected. Setup tasks T001-T005 and polish tasks T029-T033 are cross-cutting support/validation tasks rather than single-requirement implementation tasks.

## Metrics

- Total Requirements: 14
- Total Tasks: 33
- Coverage: 100% of functional requirements have at least one task
- Ambiguity Count: 0
- Duplication Count: 0
- Critical Issues Count: 0
- Medium Issues Count: 2

## Next Actions

- No CRITICAL issues block `speckit-implement`.
- Before implementation, consider remediating I1 by removing `[P]` from same-file test tasks to avoid merge conflicts.
- Before implementation or during task execution, remediate C1 by adding an explicit Jira failure fallback regression task for `frontend/src/entrypoints/task-create.test.tsx`.

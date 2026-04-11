# Prompt B: Remediation Application

**Scope**: Prompt A findings in `specs/154-jira-create-page/remediation-a.md`

## Remediations Completed

| Severity | Artifact | Location | Remediation Applied | Rationale |
| --- | --- | --- | --- | --- |
| MEDIUM | `tasks.md` | T029, T051 | T029 now requires an SC-003 responsiveness assertion that import controls are usable immediately after issue detail load. T051 now requires the focused frontend verification to confirm that SC-003 coverage executes. | This makes the measurable under-30-second import expectation explicit in the test and verification plan without changing the runtime scope or user-story ordering. |

## Remediations Skipped

None.

## Runtime And Traceability Recheck

- Production runtime code tasks remain present in `tasks.md`.
- Validation tasks remain present in `tasks.md`.
- `DOC-REQ-001` through `DOC-REQ-014` still have implementation and validation task coverage.
- Runtime task-scope validation passes after remediation.

## Residual Risks

- The task plan now requires responsiveness validation, but the exact test implementation will still need to choose a deterministic assertion that avoids flaky wall-clock timing. Prefer asserting immediate control availability after mocked issue-detail resolution, with any manual timing check documented separately if needed.

## Application Notes

- The task count and ordering remain unchanged in runtime mode.

# Prompt B: Remediation Application

**Scope**: Prompt A findings in `specs/171-jira-rollout-hardening/remediation-a.md`  
**Mode**: Runtime

## Remediations Completed

| Severity | Artifact | Location | Remediation Applied | Rationale |
| --- | --- | --- | --- | --- |
| MEDIUM | `tasks.md` | US1 tests and implementation tasks | Added T019 for frontend tests covering session-only last project/board restoration when `rememberLastBoardInSession` is enabled, disabled, and browser storage is unavailable. Added T022 for sessionStorage-backed last project/board persistence gated by `rememberLastBoardInSession`, with safe no-op behavior when browser storage is unavailable. Renumbered downstream tasks deterministically through T066 and updated US1 parallelization references. | This gives FR-023 explicit implementation and validation coverage instead of relying on general browser state tasks. |
| LOW | `spec.md`, `plan.md` | `spec.md` SC-001; `plan.md` Performance Goals | Bounded the "ordinary Jira boards" responsiveness expectation to boards with up to 10 columns and 100 issues in the first loaded issue page. | This converts the vague performance wording into a deterministic validation scenario while preserving the existing bounded-pagination design. |

## Remediations Skipped

None.

## Runtime And Traceability Recheck

- Production runtime code tasks remain present in `tasks.md`.
- Validation tasks remain present in `tasks.md`.
- Runtime task-scope validation passes after remediation with `runtime tasks=29` and `validation tasks=29`.
- `DOC-REQ-*` identifiers remain absent from the active feature artifacts, so traceability mapping is not applicable.

## Files Changed

- `specs/171-jira-rollout-hardening/spec.md`
- `specs/171-jira-rollout-hardening/plan.md`
- `specs/171-jira-rollout-hardening/tasks.md`
- `specs/171-jira-rollout-hardening/remediation-b-summary.md`

## Residual Risks

- The latest `speckit_analyze_report.md` remains a pre-Prompt-B analysis snapshot. Re-run `speckit-analyze` if a fresh post-remediation analysis report is required before implementation.
- Implementation still needs to choose deterministic mocked frontend assertions for sessionStorage behavior to avoid browser-environment flakiness.

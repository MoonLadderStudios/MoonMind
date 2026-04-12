# Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| I1 | Inconsistency | MEDIUM | `spec.md`:102, `tasks.md`:37-38, `tasks.md`:119-129 | FR-015 requires permanent invalid input, unsupported state, and stale locator failures to be classified as non-transient. The task plan covers permanent failure classification in `activity_runtime.py` and stale/idempotent controller cases, but the wording does not explicitly require a stale-locator or unsupported-state assertion at the workflow/controller boundary. | During implementation, make T010/T011/T020/T031-T038 include at least one controller or workflow-boundary assertion for stale locator and unsupported-state non-retryable classification, not only activity-wrapper unit behavior. |
| U1 | Underspecification | LOW | `tasks.md`:139 | T039 says to re-run the contract checklist against the phase-2 contract, but the artifact is a Markdown contract rather than an executable checklist. | Treat T039 as a manual trace pass against `contracts/managed-session-phase2-controls.md`, or add a short checklist section to that contract before marking T039 complete. |

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 terminate-invokes-managed-runtime | Yes | T012, T015, T016, T017, T018 | Covered through workflow, controller, and runtime termination tasks. |
| FR-002 terminate-waits-for-cleanup-finalization | Yes | T012, T013, T015, T016, T018 | Cleanup ordering and supervision finalization are directly covered. |
| FR-003 terminate-clears-active-turn-and-final-state | Yes | T012, T015, T016, T018 | Covered at workflow and controller state surfaces. |
| FR-004 terminate-does-not-swallow-cleanup-failure | Yes | T012, T015, T018 | Explicit workflow cleanup-failure visibility task exists. |
| FR-005 cancel-distinct-from-terminate | Yes | T019, T021, T023 | Cancel-vs-terminate tests and workflow implementation cover distinction. |
| FR-006 cancel-stops-active-work | Yes | T019, T020, T021, T022, T023 | Covered through workflow cancel and controller interrupt behavior. |
| FR-007 cancel-safe-without-active-turn | Yes | T019, T021, T023 | Covered by cancel workflow tests and focused validation. |
| FR-008 steer-real-runtime-support | Yes | T006, T024, T027, T030 | Fake app-server support and runtime protocol implementation cover this. |
| FR-009 steering-preserves-active-turn | Yes | T024, T025, T027, T029, T030 | Covered by runtime and workflow state update tests. |
| FR-010 controls-update-workflow-visible-state | Yes | T012, T019, T025, T026, T029, T030 | Covered across termination, cancel, and steer state tasks. |
| FR-011 launch-idempotent | Yes | T031, T034, T038 | Duplicate launch controller tests and implementation cover this. |
| FR-012 clear-idempotent | Yes | T031, T034, T038 | Duplicate clear controller tests and implementation cover this. |
| FR-013 interrupt-idempotent | Yes | T020, T022, T035, T038 | Duplicate interrupt and durable proof tasks cover this. |
| FR-014 terminate-idempotent | Yes | T013, T016, T035, T038 | Duplicate terminate and terminated-state proof tasks cover this. |
| FR-015 permanent-failure-classification | Yes | T010, T011, T033, T036, T038 | Covered, with I1 noting that boundary-level stale/unsupported assertions should be explicit during implementation. |
| FR-016 heartbeat-and-timeout-for-blocking-controls | Yes | T007, T008, T009, T032, T033, T036, T037, T038 | Strong route-policy, wrapper, and validation coverage. |
| FR-017 production-runtime-code-required | Yes | T015, T016, T017, T021, T022, T027, T028, T029, T034, T035, T036, T037, T040 | Runtime implementation tasks and runtime scope gate cover this. |
| FR-018 automated-validation-tests-required | Yes | T006, T007, T010, T012, T013, T014, T019, T020, T024, T025, T026, T031, T032, T033, T041, T042 | Tests are present before implementation tasks and final validation is required. |

## Constitution Alignment Issues

No constitution conflicts found.

- Principle IX resiliency requirements are reflected in idempotency, failure classification, heartbeat/cancellation, and workflow-boundary validation tasks.
- Principle XI spec-driven development requirements are satisfied by `spec.md`, `plan.md`, `tasks.md`, constitution checks in `plan.md`, and explicit validation gates.
- Principle XIII pre-release clean-break expectations are aligned with the feature request: no compatibility aliases or fallback-only legacy control surface are introduced in these artifacts.
- Principle XII documentation separation is not implicated by this runtime-mode feature; tasks explicitly guard against docs-only scope drift.

## Unmapped Tasks

These tasks do not map to a single functional requirement, but they are acceptable setup or validation tasks:

- T001-T005: setup/review tasks for active runtime surfaces and current test coverage.
- T039: cross-checks the generated contract artifact against the implementation plan.
- T043: final worktree review for unrelated changes.

## Metrics

- Total Requirements: 18
- Total Tasks: 43
- Coverage %: 100%
- Ambiguity Count: 0
- Duplication Count: 0
- Critical Issues Count: 0

## Next Actions

- No CRITICAL or HIGH issues block implementation.
- Address I1 while implementing failure-classification tests so stale locator and unsupported-state failures are verified at the boundary where they originate.
- Clarify the manual meaning of T039 when executing polish tasks, or add a compact checklist to the contract artifact before marking it complete.
- Proceed to `speckit-implement` after acknowledging the two non-blocking findings.

## Optional Remediation

Concrete remediation edits can be prepared for I1 and U1 before implementation, but they are not required to proceed because all functional requirements have task coverage and no constitution violations were found.

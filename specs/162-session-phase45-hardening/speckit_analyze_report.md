# Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| U1 | Underspecification | HIGH | spec.md:L6, spec.md:L92-L111, tasks.md:L50-L170 | The original feature input includes "metrics/tracing/log correlation", but no functional requirement or task explicitly preserves that Phase 4 deliverable. Existing visibility, activity-summary, routing, and reconcile work is covered, but telemetry/log-correlation implementation and validation can be skipped without violating the current tasks. | Add a functional requirement for metrics/tracing/log correlation or explicitly mark it out of scope. Add paired validation and implementation tasks, likely near US1/US3 or polish, covering worker/runtime telemetry correlation without leaking sensitive data. |

## Coverage Summary Table

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 implement-only-missing-behavior | Yes | T001, T002, T003, T004, T008 | Audit and quickstart tasks establish the missing-versus-complete baseline. |
| FR-002 runtime-code-plus-validation | Yes | T013-T016, T021-T023, T030-T036, T045-T050, T017, T024, T037, T051, T054-T056 | Production runtime and validation tasks are both present. |
| FR-003 bounded-session-details | Yes | T009, T011, T013, T017 | Covers current details and transition updates. |
| FR-004 bounded-indexed-fields | Yes | T009, T010, T013, T014, T017 | Covers exact Search Attribute keys and initial metadata. |
| FR-005 forbidden-metadata-exclusion | Yes | T005, T012, T020, T025, T038, T052, T057 | Covered across workflow metadata, summaries, reconcile output, fixtures, and final review. |
| FR-006 readable-control-summaries | Yes | T018, T019, T020, T021, T022, T023, T024 | Covers launch and controls. |
| FR-007 runtime-worker-separation | Yes | T007, T027, T028, T030, T033, T037 | Covers catalog/routing/worker registration. |
| FR-008 durable-recurring-reconcile | Yes | T029, T032, T034, T037 | Covers schedule and workflow target. |
| FR-009 bounded-reconcile-outcome | Yes | T006, T025, T026, T031, T035, T036, T037 | Covers bounded outcome and controller/supervisor behavior. |
| FR-010 lifecycle-integration-coverage | Yes | T038, T051 | Covers real workflow lifecycle path. |
| FR-011 clear-session-invariants | Yes | T039, T045, T048, T051 | Covers clear state and controller behavior. |
| FR-012 interrupt-turn-end-to-end | Yes | T040, T045, T048, T049, T051 | Covers workflow and runtime/controller behavior. |
| FR-013 terminate-session-cleanup | Yes | T041, T045, T047, T048, T051 | Covers workflow, parent coordination, and controller cleanup. |
| FR-014 cancel-distinct-from-terminate | Yes | T041, T045, T048, T051 | Covers workflow and controller semantics. |
| FR-015 steer-turn-contract | Yes | T040, T045, T048, T049, T051 | Covers unavailable and success-path behavior. |
| FR-016 restart-and-reconcile | Yes | T025, T026, T031, T035, T036, T037 | Covers recovery and degraded marking. |
| FR-017 race-and-idempotency | Yes | T042, T045, T047, T051 | Covers duplicate controls, stale epochs, early updates, and shutdown races. |
| FR-018 continue-as-new-carry-forward | Yes | T043, T046, T051 | Covers carry-forward state. |
| FR-019 replay-validation | Yes | T044, T051 | Covers replay validation task. |
| FR-020 test-first-development | Yes | T009-T012, T018-T020, T025-T029, T038-T044 | Each runtime story has test tasks before implementation tasks. |

## Constitution Alignment Issues

None detected. The artifacts include spec, plan, and tasks; runtime validation is required; canonical docs are not used as migration backlogs; and tasks include removal of obsolete internal compatibility paths when found.

## Unmapped Tasks

No problematic unmapped tasks. Setup, audit, quickstart-update, final verification, and cleanup tasks are intentionally cross-cutting and support FR-001, FR-002, and final validation rather than a single user story.

## Metrics

- Total Requirements: 20
- Total Tasks: 57
- Coverage %: 100% for listed functional requirements
- Ambiguity Count: 0
- Duplication Count: 0
- Critical Issues Count: 0
- High Issues Count: 1

## Next Actions

- Resolve U1 before `speckit-implement` if metrics/tracing/log correlation is still in scope for this feature.
- If telemetry correlation is intentionally deferred, update `spec.md`, `plan.md`, and `tasks.md` to say so explicitly.
- After remediation, rerun `speckit-analyze` to confirm the high-severity gap is closed.

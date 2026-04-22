# Tasks: Remediation Lifecycle Audit

**Input**: `specs/232-remediation-lifecycle-audit/spec.md`, `specs/232-remediation-lifecycle-audit/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/remediation-lifecycle-audit.md`, `quickstart.md`

## Validation Commands

- Unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Router/read-model unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py`
- Final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration: `./tools/test_integration.sh` if artifact lifecycle or API routes change

## Source Traceability Summary

- MM-456 is preserved in `spec.md`, `plan.md`, this task file, quickstart, implementation notes, verification output, commit text, and pull request metadata.
- The input is classified as one runtime story. `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements.
- Requirement status from `plan.md`: FR-008 and FR-031 are implemented/verified by existing evidence and traceability; FR-005, FR-011, FR-012, FR-017, FR-022, and FR-026 need verification with fallback implementation; the remaining rows are missing or partial and require tests plus implementation.

## Phase 1: Setup

- [X] T001 Confirm active feature locator points to `specs/232-remediation-lifecycle-audit` in `.specify/feature.json`
- [X] T002 Review existing remediation context/action/link fixtures in `tests/unit/workflows/temporal/test_remediation_context.py`
- [ ] T003 [P] Review target-side execution/task-run serialization tests in `tests/unit/api/routers/test_executions.py` and `tests/unit/api/routers/test_task_runs.py`

## Phase 2: Foundational

- [X] T004 Define the lifecycle evidence extension points in `moonmind/workflows/temporal/remediation_context.py`, `moonmind/workflows/temporal/remediation_actions.py`, and `api_service/db/models.py` (FR-001 through FR-031)
- [X] T005 Identify whether compact remediation audit events can reuse an existing control-event/memo path or need a bounded artifact-backed event surface in `moonmind/workflows/temporal/service.py` (FR-020 through FR-022, DESIGN-REQ-019)
- [X] T006 Confirm no new persistent table is required, or document the migration if a compact audit event table becomes unavoidable in `specs/232-remediation-lifecycle-audit/plan.md` (DESIGN-REQ-019)

## Phase 3: Story - Inspect Remediation Lifecycle Evidence

**Summary**: Operators and reviewers can inspect remediation phase, artifacts, final summary, target-side linkage, compact audit events, cancellation/failure behavior, and Continue-As-New state for one remediation run.

**Independent Test**: Run or fixture a remediation execution through evidence collection, diagnosis, approval/action, verification, and terminal outcome; inspect artifacts, summary, target-side metadata, and audit events to confirm every required field and bounded failure case is visible.

**Traceability IDs**: FR-001 through FR-031, SC-001 through SC-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-022, DESIGN-REQ-023

**Unit Test Plan**: Add focused tests for phase validation, artifact metadata, summary block serialization, audit event boundedness, continuation payload preservation, and degraded/fallback outcomes.

**Integration Test Plan**: Add service-boundary tests proving remediation artifacts are linked to executions, target-side summaries are exposed, and read-models can surface compact remediation evidence without parsing artifact bodies.

### Unit Tests

- [X] T007 [P] Add failing unit tests for allowed `remediationPhase` values and lifecycle outcome normalization in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-001 through FR-004, SC-001, DESIGN-REQ-017)
- [X] T008 [P] Add failing unit tests for required remediation artifact names, artifact types, bounded metadata, redaction, and no raw access values in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-008 through FR-015, SC-002, DESIGN-REQ-018)
- [X] T009 [P] Add failing unit tests for remediation run summary block fields, degraded evidence flags, unavailable evidence classes, fallback evidence, and resulting target run ID in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-014, FR-016, FR-024, FR-028 through FR-030, SC-003, SC-008)
- [X] T010 [P] Add failing unit tests for compact remediation audit event fields and bounded metadata redaction in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-018, FR-020 through FR-022, SC-005, DESIGN-REQ-019)
- [X] T011 [P] Add failing unit tests for cancellation/failure finalization and Continue-As-New preservation payloads in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-005 through FR-007, FR-027, SC-006, SC-007, DESIGN-REQ-022, DESIGN-REQ-023)
- [X] T012 Add verification tests for already-present `remediation.context`, action request/result payloads, target control artifacts, redacted audit fields, and precondition/no-op outcomes in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-008, FR-011, FR-012, FR-017, FR-022, FR-026)

### Integration Tests

- [X] T013 [P] Add service-boundary artifact publication test for the full remediation artifact set in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-008 through FR-015, SC-002)
- [ ] T014 [P] Add target-side linkage summary read-model test in `tests/unit/api/routers/test_executions.py` or `tests/unit/api/routers/test_task_runs.py` (FR-019, FR-023, SC-004, DESIGN-REQ-019)
- [X] T015 [P] Add compact audit trail read-model or artifact-backed event test in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-020 through FR-022, SC-005)
- [ ] T016 Add cancellation/failure and Continue-As-New service-boundary tests in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-005 through FR-007, FR-027, SC-006, SC-007)

### Red-First Confirmation

- [X] T017 Run targeted remediation tests and confirm T007 through T013/T015/T016 fail for the expected missing lifecycle evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- [ ] T018 Run router/read-model tests and confirm T014 fails for the expected missing target-side summary behavior: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py`

### Conditional Verification Fallbacks

- [ ] T019 If T012 shows action request/result or audit payloads are not persisted as artifacts, implement artifact publication fallback in `moonmind/workflows/temporal/remediation_actions.py` or `moonmind/workflows/temporal/remediation_context.py` (FR-011, FR-012, FR-022)
- [ ] T020 If T012 shows target control artifacts are not preserved for remediation-mutated targets, add linkage preservation in the relevant managed-session/workload publication path in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` or `moonmind/workflows/temporal/remediation_tools.py` (FR-017)
- [X] T021 If T012 shows precondition/no-op outcomes are not included in lifecycle summaries, add summary mapping in `moonmind/workflows/temporal/remediation_actions.py` (FR-026)

### Implementation

- [X] T022 Add remediation phase, resolution, artifact type, summary, audit event, and continuation payload models/helpers in `moonmind/workflows/temporal/remediation_context.py` (FR-001 through FR-007, FR-014, FR-016, FR-020 through FR-022, FR-024 through FR-030)
- [X] T023 Implement remediation artifact publication helpers for plan, decision log, action request/result, verification, and summary artifacts in `moonmind/workflows/temporal/remediation_context.py` (FR-009 through FR-015, DESIGN-REQ-018)
- [ ] T024 Integrate action authority request/result output with remediation artifact publication or lifecycle evidence helpers in `moonmind/workflows/temporal/remediation_actions.py` (FR-011, FR-012, FR-018)
- [X] T025 Implement remediation summary block assembly including target identity, mode, authorityMode, actionsAttempted, resolution, lock conflicts, approval count, evidence degraded, escalated, unavailable evidence classes, and fallbacks in `moonmind/workflows/temporal/remediation_context.py` (FR-014, FR-016, FR-024, FR-028 through FR-030)
- [X] T026 Implement compact remediation audit event assembly and bounded metadata validation in `moonmind/workflows/temporal/remediation_context.py` or `moonmind/workflows/temporal/service.py` (FR-020 through FR-022, DESIGN-REQ-019)
- [ ] T027 Extend target-side remediation linkage summary generation using `execution_remediation_links` compact fields in `api_service/api/routers/executions.py` or `api_service/api/routers/task_runs.py` (FR-019, FR-023)
- [ ] T028 Implement cancellation/failure finalization and Continue-As-New preservation helpers in `moonmind/workflows/temporal/remediation_context.py` or `moonmind/workflows/temporal/service.py` (FR-005 through FR-007, FR-027, DESIGN-REQ-022, DESIGN-REQ-023)
- [ ] T029 Preserve MM-456 traceability in code comments or test names only where useful and in verification artifacts in `specs/232-remediation-lifecycle-audit/verification.md` (FR-031)

### Story Validation

- [X] T030 Run targeted remediation tests until they pass: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- [ ] T031 Run router/read-model tests until they pass if target-side summary files changed: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py`
- [X] T032 Update `specs/232-remediation-lifecycle-audit/tasks.md` checkboxes for completed implementation and validation work

## Final Phase: Polish And Verification

- [X] T033 Review `specs/232-remediation-lifecycle-audit/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-lifecycle-audit.md`, and `quickstart.md` for MM-456 and DESIGN-REQ traceability
- [X] T034 Run full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T035 Run hermetic integration verification if artifact lifecycle/API routes changed: `./tools/test_integration.sh`
- [ ] T036 Run `/moonspec-verify` read-only verification for `specs/232-remediation-lifecycle-audit/spec.md`

## Dependencies And Order

- T001 through T006 must complete before story tests.
- T007 through T016 must be written before T017 and T018.
- T017 and T018 must confirm red-first failure before T019 through T028.
- T019 through T028 must complete before T030 and T031.
- T030 and T031 must pass before T032 through T036.

## Parallel Examples

- T007, T008, T009, T010, T011, and T012 can be drafted in parallel if coordinated in the same test file.
- T013, T014, T015, and T016 touch different verification surfaces and can be drafted in parallel.
- T023, T026, and T027 can be implemented in parallel if write ownership is kept separate across artifact helpers, audit helpers, and API read models.

## Implementation Strategy

Build the story vertically from bounded models to artifact publication to read-model visibility. Keep deep evidence artifact-backed, keep queryable evidence compact, and preserve top-level run state as the authority while adding remediation-specific lifecycle fields.

# Tasks: Observable Remediation Repair and Prevention Lifecycle

**Input**: Design documents from `/work/agent_jobs/mm:ef43e84e-d4ac-47cb-bb8d-b94b2283ce80/repo/specs/322-remediation-lifecycle-repair-prevention/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: Observable Remediation Repair and Prevention Lifecycle.

**Source Traceability**: MM-622 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-015, acceptance scenarios 1-6, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-009.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel only when tasks touch different files and do not depend on incomplete work.
- Every task includes exact file paths and traceability IDs when applicable.

## Requirement Status Summary

- Code-and-test work: FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-012; SCN-002, SCN-003, SCN-004, SCN-005; SC-002, SC-003, SC-004; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009.
- Verification-first with conditional fallback: FR-004, FR-011, FR-013, FR-014; SCN-006; SC-005; DESIGN-REQ-006, DESIGN-REQ-007.
- Already verified and retained through final validation: FR-001, FR-002, FR-015; SCN-001; SC-001, SC-006; DESIGN-REQ-003.

## Phase 1: Setup

**Purpose**: Confirm the active MoonSpec artifact set and existing remediation test harness before story work starts.

- [X] T001 Confirm `specs/322-remediation-lifecycle-repair-prevention/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/remediation-lifecycle-repair-prevention.md` are present and preserve MM-622 traceability for FR-015 and SC-006.
- [X] T002 Review existing remediation fixtures and fake executors in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py` for reuse in lifecycle tests covering FR-003 through FR-014.
- [X] T003 Confirm no new package dependencies or migrations are needed by checking `specs/322-remediation-lifecycle-repair-prevention/plan.md` storage and dependency decisions for FR-003 through FR-014.

---

## Phase 2: Foundational

**Purpose**: Identify the extension points that block test and implementation work.

**CRITICAL**: No production lifecycle implementation work begins until this phase is complete.

- [X] T004 Map existing bounded phase, summary, audit, and Continue-As-New helpers in `moonmind/workflows/temporal/remediation_context.py` to FR-001, FR-002, FR-013, FR-014, DESIGN-REQ-003, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T005 Map existing action authority, mutation guard, freshness, lock, ledger, and budget paths in `moonmind/workflows/temporal/remediation_actions.py` to FR-004, FR-005, FR-011, DESIGN-REQ-001, DESIGN-REQ-005, and DESIGN-REQ-008.
- [X] T006 Map existing evidence/action artifact publishing in `moonmind/workflows/temporal/remediation_tools.py` and `moonmind/workflows/temporal/remediation_context.py` to FR-003, FR-006, FR-009, FR-012, DESIGN-REQ-002, and DESIGN-REQ-004.
- [X] T007 Create or update implementation notes in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md` listing reused evidence, intended new helpers, and red-first test expectations for FR-003 through FR-014.

**Checkpoint**: Foundational mapping is complete and test-first story work can begin.

---

## Phase 3: Story - Observable Remediation Repair and Prevention Lifecycle

**Summary**: As a remediation task, I need to progress through observable lifecycle phases while separately recording immediate repair and recurrence-prevention outcomes, so operators can understand whether the target was repaired, escalated, or turned into a reviewable prevention change.

**Independent Test**: Run a remediation lifecycle against controlled target states covering repaired, still failed, unsafe, approval-required, escalated, canceled, rerun, and Continue-As-New paths; then verify read models, decision log, repair result, prevention result, audit publication, lock release, and preserved refs.

**Traceability**: FR-001 through FR-015; acceptance scenarios 1-6; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-009.

**Unit Test Plan**: Validate lifecycle value normalization preservation, repair decisions, repair outcome classification, prevention output validation, decision-log shape, corrected-instruction retry provenance, cancellation finalization, rerun summaries, and Continue-As-New continuity.

**Integration Test Plan**: Validate service/artifact boundary publication for decision log and final summary, safe repair path, prevention output, cancellation no-new-mutation behavior, and continuity evidence.

### Unit Tests (write first)

- [X] T008 Add failing unit tests for repair candidate decisions and smallest-plausible-action guardrails in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-004, FR-005, SCN-002, SCN-003, DESIGN-REQ-001, and DESIGN-REQ-008.
- [X] T009 Add failing unit tests for repair outcome classification values `repaired`, `still_failed`, `not_attempted`, `unsafe`, `approval_required`, and `escalated` in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-006, SC-002, and DESIGN-REQ-001.
- [X] T010 Add failing unit tests for recurrence-prevention outputs `reviewable_change_created`, `findings_reported`, `no_reviewable_fix`, and `policy_blocked` in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-007, FR-008, SCN-004, SC-003, and DESIGN-REQ-008.
- [X] T011 Add failing unit tests for remediation decision-log schema, redaction, required repair refs, recurrence category, prevention refs, and no-change reasons in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-009, SC-002, DESIGN-REQ-002, and DESIGN-REQ-009.
- [X] T012 Add failing unit tests for corrected-instruction retry provenance or deterministic unsupported/escalated decision in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-010 and DESIGN-REQ-009.
- [X] T013 Add failing unit tests for cancellation terminal finalization, no new target mutation after cancellation, lock-release attempt recording, and final audit/summary attempt recording in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-011, FR-012, SCN-005, SC-004, and DESIGN-REQ-005.
- [X] T014 Add failing unit tests for rerun and Continue-As-New lifecycle continuity evidence in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-013, FR-014, SCN-006, SC-005, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and confirm T008-T014 fail for the expected missing lifecycle behavior before production changes.

### Integration Tests (write first)

- [X] T016 Add failing hermetic integration test for safe repair path publishing decision log, action request/result/verification refs, repair outcome, and final summary in `tests/integration/temporal/test_remediation_action_contracts.py` covering FR-003, FR-004, FR-006, SCN-002, SC-002, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-004.
- [X] T017 Add failing hermetic integration test for recurrence-prevention output and final `remediation.summary` prevention block in `tests/integration/temporal/test_remediation_action_contracts.py` covering FR-007, FR-008, SCN-004, SC-003, and DESIGN-REQ-008.
- [X] T018 Add failing hermetic integration test for cancellation finalization with no new target mutation, lock-release attempt, and audit/summary attempt evidence in `tests/integration/temporal/test_remediation_action_contracts.py` covering FR-011, FR-012, SCN-005, SC-004, and DESIGN-REQ-005.
- [X] T019 Add failing hermetic integration test for changed target run and Continue-As-New continuity refs in `tests/integration/temporal/test_remediation_action_contracts.py` covering FR-013, FR-014, SCN-006, SC-005, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T020 Run `./tools/test_integration.sh` and confirm T016-T019 fail for the expected missing lifecycle behavior before production changes.

### Red-First Confirmation

- [X] T021 Record the expected red-first failures from T015 and T020 in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md` with links to FR-003 through FR-014 and DESIGN-REQ-001 through DESIGN-REQ-009.
- [X] T022 Confirm already-verified rows FR-001, FR-002, FR-015, SCN-001, SC-001, SC-006, and DESIGN-REQ-003 still pass or remain covered in `tests/unit/workflows/temporal/test_remediation_context.py` before implementing new lifecycle behavior.

### Conditional Fallback Implementation For Verification-First Rows

- [X] T023 If T008 or T016 exposes gaps in fresh target health, allowed action, lock, or policy proof, update `moonmind/workflows/temporal/remediation_tools.py` and `moonmind/workflows/temporal/remediation_actions.py` for FR-004 and DESIGN-REQ-008.
- [X] T024 If T013 or T018 exposes cancellation mutation-boundary gaps, update `moonmind/workflows/temporal/remediation_context.py`, `moonmind/workflows/temporal/remediation_tools.py`, and `moonmind/workflows/temporal/remediation_actions.py` for FR-011 and DESIGN-REQ-005.
- [X] T025 If T014 or T019 exposes rerun or Continue-As-New continuity gaps, update `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/remediation_tools.py` for FR-013, FR-014, DESIGN-REQ-006, and DESIGN-REQ-007.

### Implementation

- [X] T026 Add repair decision, repair outcome, prevention outcome, decision-log entry, and lifecycle final summary builders in `moonmind/workflows/temporal/remediation_context.py` covering FR-003, FR-006, FR-007, FR-008, FR-009, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-008.
- [X] T027 Add validation/redaction for repair decisions, prevention outputs, decision-log metadata, and final summary extension fields in `moonmind/workflows/temporal/remediation_context.py` covering FR-005, FR-008, FR-009, FR-010, and DESIGN-REQ-009.
- [X] T028 Extend remediation action/evidence flow in `moonmind/workflows/temporal/remediation_tools.py` to publish the v1 decision log and final summary with repair/prevention blocks for FR-003, FR-006, FR-007, FR-008, FR-009, FR-012, SC-002, SC-003, and SC-004.
- [X] T029 Add corrected-instruction retry provenance support or explicit unsupported/escalated lifecycle output in `moonmind/workflows/temporal/remediation_actions.py` and `moonmind/workflows/temporal/remediation_context.py` covering FR-010 and DESIGN-REQ-009.
- [X] T030 Wire cancellation, escalation, failure, resolved, rerun, and Continue-As-New finalization paths through existing remediation link/artifact boundaries in `moonmind/workflows/temporal/remediation_tools.py` covering FR-011, FR-012, FR-013, FR-014, SC-004, SC-005, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T031 Update `__all__` exports or imports in `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/__init__.py` only for new public helpers required by tests covering FR-003 through FR-014.

### Story Validation

- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and fix failures in `moonmind/workflows/temporal/remediation_context.py`, `remediation_tools.py`, or `remediation_actions.py` until all MM-622 unit tests pass.
- [X] T033 Run `./tools/test_integration.sh` and fix failures in `moonmind/workflows/temporal/remediation_context.py`, `remediation_tools.py`, `remediation_actions.py`, or `tests/integration/temporal/test_remediation_action_contracts.py` until MM-622 integration coverage passes or record an environment blocker in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md`.
- [X] T034 Validate the story independently against `specs/322-remediation-lifecycle-repair-prevention/quickstart.md` and record evidence in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md` for FR-001 through FR-015, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-009.

**Checkpoint**: The single MM-622 story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T035 [P] Review `specs/322-remediation-lifecycle-repair-prevention/contracts/remediation-lifecycle-repair-prevention.md` against implemented artifact fields and update only if implementation revealed a necessary contract correction for MM-622.
- [X] T036 [P] Review `specs/322-remediation-lifecycle-repair-prevention/data-model.md` against implemented builders and update only if entity fields or validation rules changed during MM-622 implementation.
- [X] T037 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit-suite verification and record any blocker in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md`.
- [X] T038 Run `./tools/test_integration.sh` for full hermetic integration verification and record any Docker/environment blocker in `specs/322-remediation-lifecycle-repair-prevention/implementation-notes.md`.
- [ ] T039 Run `/moonspec-verify` to validate final implementation against the preserved MM-622 Jira preset brief in `specs/322-remediation-lifecycle-repair-prevention/spec.md`.

---

## Dependencies And Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on the story implementation and focused tests passing.

### Within The Story

- Unit tests T008-T014 must be written before implementation.
- Integration tests T016-T019 must be written before implementation.
- Red-first confirmation T021-T022 must complete before production code tasks.
- Conditional fallback tasks T023-T025 run only if verification-first tests expose gaps.
- Builders and validation T026-T027 precede service wiring T028-T030.
- Story validation T032-T034 follows implementation tasks.

### Parallel Opportunities

- T008-T014 are intentionally not marked `[P]` because they all edit `tests/unit/workflows/temporal/test_remediation_context.py`.
- T016-T019 are intentionally not marked `[P]` because they all edit `tests/integration/temporal/test_remediation_action_contracts.py`.
- T035 and T036 can run in parallel after implementation because they touch different design artifacts.

---

## Parallel Example

```bash
# After foundational mapping, test authors should coordinate shared-file edits:
Task: "T008 repair candidate unit tests in tests/unit/workflows/temporal/test_remediation_context.py"
Task: "T010 prevention output unit tests in tests/unit/workflows/temporal/test_remediation_context.py"

# After implementation, design artifact checks can run together:
Task: "T035 contract review in specs/322-remediation-lifecycle-repair-prevention/contracts/remediation-lifecycle-repair-prevention.md"
Task: "T036 data model review in specs/322-remediation-lifecycle-repair-prevention/data-model.md"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational mapping.
2. Write focused unit tests and confirm red-first failures.
3. Write hermetic integration tests and confirm red-first failures.
4. Implement compact lifecycle builders and validation in `remediation_context.py`.
5. Wire lifecycle publication and finalization through `remediation_tools.py`.
6. Touch `remediation_actions.py` only where action/guard evidence or corrected-instruction provenance requires it.
7. Run focused unit and integration validation.
8. Run full unit and hermetic integration suites.
9. Run `/moonspec-verify` against the preserved MM-622 source brief.

### Status Handling

- Already-verified rows are retained through T022, T037, and T039 without new implementation work.
- Implemented-unverified rows have verification tests first and conditional fallback implementation tasks T023-T025.
- Missing and partial rows have red-first test tasks T008-T020 and implementation tasks T026-T031.

## Notes

- This task list covers one story only.
- Do not create `tasks.md` for adjacent remediation specs from this file.
- Do not modify `.agents/skills` or checked-in skill source folders.
- Preserve MM-622 in implementation notes, verification output, commit text, and pull request metadata.

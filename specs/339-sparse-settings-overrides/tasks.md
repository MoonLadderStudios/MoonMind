# Tasks: Sparse Settings Override Persistence and Reset

**Input**: Design documents from `specs/339-sparse-settings-overrides/`
**Prerequisites**: `specs/339-sparse-settings-overrides/plan.md`, `specs/339-sparse-settings-overrides/spec.md`, `specs/339-sparse-settings-overrides/research.md`, `specs/339-sparse-settings-overrides/data-model.md`, `specs/339-sparse-settings-overrides/contracts/`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: save, read, reset, validate, and verify sparse user/workspace settings overrides for `MM-654`.

**Source Traceability**: Original Jira issue `MM-654` and the preserved preset brief are in `spec.md`. Tasks cover FR-001 through FR-013, SCN-001 through SCN-007, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-010. Plan status summary: 27 implemented_verified rows require final validation only, 6 partial rows require red-first tests plus implementation hardening, and 3 implemented_unverified traceability rows require verification-first coverage with conditional artifact updates if verification fails.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused unit iteration: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
- Integration tests: `./tools/test_integration.sh`
- Focused integration iteration: `pytest tests/integration/api/test_settings_overrides_contract.py -m integration_ci -q`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work.
- Every task includes exact file paths and requirement, scenario, success, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active one-story planning context and baseline evidence before adding tests.

- [X] T001 Confirm `specs/339-sparse-settings-overrides/spec.md` still contains exactly one `## User Story -` section and preserves `MM-654` for FR-013/SCN-007/SC-006.
- [X] T002 Confirm `specs/339-sparse-settings-overrides/plan.md`, `specs/339-sparse-settings-overrides/research.md`, `specs/339-sparse-settings-overrides/data-model.md`, `specs/339-sparse-settings-overrides/contracts/settings-overrides-api.md`, and `specs/339-sparse-settings-overrides/quickstart.md` exist before implementation planning continues.

---

## Phase 2: Foundational

**Purpose**: Establish the exact current validation boundary and test locations that block story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Inspect current override validation and unsafe-payload detection in `api_service/services/settings_catalog.py` for FR-008/FR-011/SCN-005/SC-004/DESIGN-REQ-006.
- [X] T004 Inspect current service validation tests in `tests/unit/services/test_settings_catalog.py` for existing coverage of size limits and unsafe payload classes for FR-008/FR-011/SC-004/DESIGN-REQ-006.
- [X] T005 Inspect current API contract tests in `tests/unit/api_service/api/routers/test_settings_api.py` and integration test markers in `tests/integration/api/` to place the MM-654 API boundary tests for contracts/settings-overrides-api.md.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Save, Read, and Reset Sparse Settings Overrides

**Summary**: Workspace administrators and users can persist sparse settings overrides, read inherited effective values, reset overrides safely, reject unsafe values, and prevent stale concurrent writes.

**Independent Test**: Save workspace and user overrides, verify effective sources and version metadata, reset each override, confirm inherited values return, reject oversized/unsafe/stale writes, and confirm no secret plaintext or partial persistence occurs.

**Traceability**: FR-001 through FR-013; SCN-001 through SCN-007; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-010.

**Test Plan**:

- Unit: service validation, size limits, unsafe payload fixture set, stale writes, reset preservation, traceability guards.
- Integration: API contract for PATCH/DELETE/effective reads, invalid payload rejection, no partial persistence, hermetic `integration_ci` boundary.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T006 [P] Add failing unit tests for explicit serialized size-limit rejection in `tests/unit/services/test_settings_catalog.py` covering FR-008/SCN-005/SC-004/DESIGN-REQ-006.
- [X] T007 [P] Add failing unit tests for unsafe OAuth session, decrypted credential, generated credential config, large artifact, workflow payload, and operational command history fixtures in `tests/unit/services/test_settings_catalog.py` covering FR-011/SCN-005/SC-004/DESIGN-REQ-006.
- [X] T008 [P] Add unit verification tests that already-implemented inheritance, workspace/user override, intentional null, user reset while workspace override exists, workspace reset while user override exists, unknown/ineligible/already-absent reset outcomes, SecretRef reference storage, multi-setting atomicity, version, and audit behavior still satisfy FR-001 through FR-007, FR-009, FR-010, SCN-001 through SCN-004, SCN-006, SC-001 through SC-003, SC-005, and DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-007 through DESIGN-REQ-010 in `tests/unit/services/test_settings_catalog.py`.
- [X] T009 Run `pytest tests/unit/services/test_settings_catalog.py -q` and confirm T006-T007 fail for missing MM-654 validation behavior while T008 passes or identifies only verification gaps.

### Integration Tests (write first)

- [X] T010 [P] Add failing API test coverage for oversized override payload rejection and unchanged effective values in `tests/unit/api_service/api/routers/test_settings_api.py` covering FR-008/SCN-005/SC-004/contracts/settings-overrides-api.md.
- [X] T011 [P] Add failing API test coverage for every disallowed unsafe payload category and redacted error responses in `tests/unit/api_service/api/routers/test_settings_api.py` covering FR-011/SCN-005/SC-004/contracts/settings-overrides-api.md.
- [X] T012 [P] Add hermetic integration contract tests for effective read, PATCH save, DELETE reset, user/workspace reset inheritance, unknown/ineligible/already-absent reset outcomes, stale write conflict, multi-setting atomicity, SecretRef reference storage, oversized payload rejection, and unsafe payload rejection in `tests/integration/api/test_settings_overrides_contract.py` with `@pytest.mark.integration` and `@pytest.mark.integration_ci` covering FR-001 through FR-012, SCN-001 through SCN-006, all edge cases, and contracts/settings-overrides-api.md.
- [X] T013 Run `pytest tests/unit/api_service/api/routers/test_settings_api.py -q` and confirm T010-T011 fail for the intended missing validation behavior before production changes.
- [X] T014 Run `pytest tests/integration/api/test_settings_overrides_contract.py -m integration_ci -q` and confirm T012 fails for the intended missing validation behavior or passes already-verified API behavior before production changes.

### Red-First Confirmation

- [X] T015 Record the red-first outcome from T009, T013, and T014 in `specs/339-sparse-settings-overrides/tasks.md` implementation notes or a local handoff artifact under `artifacts/` before editing production code for FR-008/FR-011/SCN-005/SC-004/DESIGN-REQ-006.

### Conditional Fallback For Implemented-Unverified Traceability

- [X] T016 If traceability checks fail, update `specs/339-sparse-settings-overrides/tasks.md` to preserve `MM-654` and the preset brief references for FR-013/SCN-007/SC-006 before final verification.

### Implementation

- [X] T017 Define and enforce a small serialized override payload size limit in `api_service/services/settings_catalog.py` for FR-008/SCN-005/SC-004/DESIGN-REQ-006.
- [X] T018 Harden unsafe payload detection in `api_service/services/settings_catalog.py` so raw secrets, OAuth session blobs, decrypted credentials, generated credential config containing secrets, large artifacts, workflow payloads, and operational command history are rejected for FR-011/SCN-005/SC-004/DESIGN-REQ-006.
- [X] T019 Ensure API error handling in `api_service/api/routers/settings.py` returns structured `invalid_setting_value` responses without echoing unsafe payload content for FR-011/SCN-005/SC-004/contracts/settings-overrides-api.md.
- [X] T020 If T012 reveals integration fixture gaps, add the minimal hermetic fixtures needed by `tests/integration/api/test_settings_overrides_contract.py` without changing unrelated test infrastructure.

### Story Validation

- [X] T021 Run `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q` and fix only MM-654 scoped failures in `api_service/services/settings_catalog.py`, `api_service/api/routers/settings.py`, `tests/unit/services/test_settings_catalog.py`, and `tests/unit/api_service/api/routers/test_settings_api.py`.
- [X] T022 Run `pytest tests/integration/api/test_settings_overrides_contract.py -m integration_ci -q` and fix only MM-654 scoped integration failures in `api_service/services/settings_catalog.py`, `api_service/api/routers/settings.py`, and `tests/integration/api/test_settings_overrides_contract.py`.
- [X] T023 Validate the independent story manually against `specs/339-sparse-settings-overrides/quickstart.md`, including user reset inheritance, workspace reset preservation, multi-setting atomicity, SecretRef reference handling, unknown/ineligible/already-absent reset outcomes, and unsafe/oversized rejection, then record any deviations in `specs/339-sparse-settings-overrides/verification.md` draft notes for FR-012/SC-006.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T024 [P] Review `specs/339-sparse-settings-overrides/contracts/settings-overrides-api.md` against the final API behavior and update only if the contract drifted during implementation.
- [X] T025 [P] Review `specs/339-sparse-settings-overrides/data-model.md` against final validation and state-transition behavior and update only if the model drifted during implementation.
- [X] T026 Run `./tools/test_unit.sh` for final unit verification and capture the result in `specs/339-sparse-settings-overrides/verification.md` for FR-012/SC-001 through SC-006.
- [X] T027 Run `./tools/test_integration.sh` if T012 introduced or changed `integration_ci` coverage, and capture the result in `specs/339-sparse-settings-overrides/verification.md` for contracts/settings-overrides-api.md and FR-012.
- [ ] T028 Run `/speckit.verify` for `specs/339-sparse-settings-overrides/spec.md` after implementation and tests pass, producing final verification evidence that preserves `MM-654`, the original preset brief, FR-001 through FR-013, SCN-001 through SCN-007, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-010.

---

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 setup has no dependencies.
- Phase 2 foundational inspection depends on Phase 1.
- Phase 3 story work depends on Phase 2.
- Phase 4 polish and verification depends on Phase 3 tests and implementation passing.

### Within The Story

- T006 through T008 must be written before T009.
- T010 through T012 must be written before T013 and T014.
- T015 must complete before production changes T017 through T020.
- T017 through T020 depend on red-first evidence from T009, T013, and T014.
- T021 through T023 validate the completed story after implementation.
- T028 is final and must run only after T026 and required T027 evidence is available.

### Parallel Opportunities

- T006, T007, and T008 can run in parallel if coordinated carefully because they add distinct test cases in `tests/unit/services/test_settings_catalog.py`.
- T010, T011, and T012 can run in parallel because API unit tests and integration contract tests are separate files.
- T024 and T025 can run in parallel because contract and data-model docs are separate artifacts.

---

## Parallel Example: Story Phase

```bash
# Launch API and integration test authoring together:
Task: "Add failing API oversized payload tests in tests/unit/api_service/api/routers/test_settings_api.py"
Task: "Add integration contract tests in tests/integration/api/test_settings_overrides_contract.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to confirm the active artifacts and current validation boundary.
2. Write red-first unit tests for the partial rows: FR-008, FR-011, SCN-005, SC-004, and DESIGN-REQ-006.
3. Write API and hermetic integration tests for the same partial rows and the public contract.
4. Confirm the new validation tests fail for the expected reason before production changes.
5. Implement only the size-limit and unsafe-payload hardening needed to satisfy the failing tests.
6. Run focused service, API, and integration tests until the single story passes.
7. Run final `./tools/test_unit.sh` and required `./tools/test_integration.sh` evidence.
8. Run `/speckit.verify` to validate the completed implementation against the original `MM-654` preset brief.

### Status Handling

- Code-and-test work: FR-008, FR-011, SCN-005, SC-004, DESIGN-REQ-006.
- Verification-only work: FR-001 through FR-007, FR-009, FR-010, SCN-001 through SCN-004, SCN-006, SC-001 through SC-003, SC-005, DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-007 through DESIGN-REQ-010.
- Conditional fallback work: FR-013, SCN-007, SC-006 traceability updates if verification finds missing `MM-654` references.
- Final verification work: FR-012 and all success criteria through `/speckit.verify`.

## Notes

- This task list covers one story only.
- Do not implement unrelated settings UI changes.
- Do not add new persistent tables unless validation work proves existing override/audit tables cannot satisfy the story.
- Preserve Jira issue key `MM-654` in all implementation notes, verification output, commit text, and pull request metadata.

## Implementation Evidence

- Red-first unit service evidence: `pytest tests/unit/services/test_settings_catalog.py -q` failed before production changes with `test_oversized_override_value_rejected_before_persistence` and `test_unsafe_payload_classes_rejected_before_persistence` because no `ValueError` was raised.
- Red-first API evidence: `pytest tests/unit/api_service/api/routers/test_settings_api.py -q` failed before production changes with oversized and unsafe override payload requests returning HTTP 200 instead of 400.
- Red-first integration evidence: `pytest tests/integration/api/test_settings_overrides_contract.py -m integration_ci -q` failed before production changes with oversized override payload returning HTTP 200 instead of 400.

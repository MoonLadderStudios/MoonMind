# Tasks: Effective Value Resolver With Source Explanation and Operator Locks

**Input**: Design documents from `specs/340-effective-value-resolver/`
**Prerequisites**: `specs/340-effective-value-resolver/spec.md`, `specs/340-effective-value-resolver/plan.md`, `specs/340-effective-value-resolver/research.md`, `specs/340-effective-value-resolver/data-model.md`, `specs/340-effective-value-resolver/contracts/settings-effective-values-api.md`, `specs/340-effective-value-resolver/quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: explain effective settings values with canonical sources, operator locks, complete metadata, and distinct diagnostics for `MM-655`.

**Source Traceability**: Original Jira issue `MM-655` and the preserved preset brief are in `spec.md`. Tasks cover FR-001 through FR-014, SCN-001 through SCN-007, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-012. Plan status summary: 6 missing rows and 17 partial rows require red-first tests plus implementation, 6 implemented_unverified rows require verification-first coverage with conditional fallback, and 4 implemented_verified rows require final validation only.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused unit iteration: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
- Integration tests: `./tools/test_integration.sh`
- Focused integration iteration: `pytest tests/integration/api/test_settings_effective_values_contract.py -m integration_ci -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work.
- Every task includes exact file paths and requirement, scenario, success, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active one-story planning context and required design artifacts before writing tests.

- [X] T001 Confirm `specs/340-effective-value-resolver/spec.md` contains exactly one `## User Story -` section and preserves `MM-655` for FR-014/SCN-007/SC-006.
- [X] T002 Confirm `specs/340-effective-value-resolver/plan.md`, `specs/340-effective-value-resolver/research.md`, `specs/340-effective-value-resolver/data-model.md`, `specs/340-effective-value-resolver/contracts/settings-effective-values-api.md`, and `specs/340-effective-value-resolver/quickstart.md` exist before implementation starts.
- [X] T003 Confirm `.specify/feature.json` points to `specs/340-effective-value-resolver` for final traceability of MM-655.

---

## Phase 2: Foundational

**Purpose**: Establish the current settings resolver, API, and test boundary that block story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Inspect current effective value models, descriptor models, source labels, diagnostics, read-only fields, and override resolution in `api_service/services/settings_catalog.py` for FR-001 through FR-013 and DESIGN-REQ-001 through DESIGN-REQ-012.
- [X] T005 Inspect current Settings API response behavior and structured error handling in `api_service/api/routers/settings.py` for contracts/settings-effective-values-api.md, SCN-001 through SCN-006, and SC-001 through SC-005.
- [X] T006 Inspect existing service tests in `tests/unit/services/test_settings_catalog.py` for already-verified default, workspace override, user override, SecretRef, provider-profile, migration, activation, and diagnostics coverage for FR-002/FR-004/FR-011 and DESIGN-REQ-006/DESIGN-REQ-007/DESIGN-REQ-010.
- [X] T007 Inspect existing API tests in `tests/unit/api_service/api/routers/test_settings_api.py` and integration test conventions under `tests/integration/` to place the MM-655 API and `integration_ci` contract tests.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Explain Effective Settings Values

**Summary**: Workspace administrators and settings users can read effective settings values that resolve in the documented order, expose canonical source labels, explain inheritance and locks, preserve secret safety, and report distinct actionable diagnostics instead of hidden fallback.

**Independent Test**: Resolve representative settings across default, config/environment default, workspace override, user override, provider profile reference, SecretRef reference, and operator-lock cases, then verify value, source label, inheritance/override/lock state, read-only state, default visibility, reload/restart metadata, dependent systems, and diagnostics for every supported missing or blocked state.

**Traceability**: FR-001 through FR-014; SCN-001 through SCN-007; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-012; contracts/settings-effective-values-api.md.

**Unit Test Plan**:

- Service tests prove resolver precedence, source vocabulary, effective metadata, operator locks, diagnostic matrix, SecretRef redaction, provider profile references, and no silent fallback.
- Existing verified precedence tests remain in place and are updated only if source vocabulary changes require replacing superseded internal labels.

**Integration Test Plan**:

- API contract tests prove effective single/list responses, diagnostics responses, operator lock read-only output, canonical sources, structured errors, and secret-safe output through the Settings router boundary.
- `integration_ci` coverage is required when implementation changes route contracts or persistence-backed resolution behavior.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T008 Add failing unit tests for effective value response metadata in `tests/unit/services/test_settings_catalog.py` covering FR-001/FR-008/FR-009/SCN-001/SC-004/DESIGN-REQ-001/DESIGN-REQ-009.
- [X] T009 Add failing unit tests for canonical source vocabulary and precedence in `tests/unit/services/test_settings_catalog.py` covering FR-003/FR-006/SC-001/DESIGN-REQ-002/DESIGN-REQ-004.
- [X] T010 Add failing unit tests for operator-lock precedence, source `operator_lock`, read-only output, and read-only reason in `tests/unit/services/test_settings_catalog.py` covering FR-005/FR-010/SCN-004/SC-002/DESIGN-REQ-003/DESIGN-REQ-005/DESIGN-REQ-012.
- [X] T011 Add failing unit tests for distinct no-default, inherited-null, intentionally-null override, unresolved-SecretRef, missing-provider-profile, policy-blocked, and post-migration-invalid diagnostics in `tests/unit/services/test_settings_catalog.py` covering FR-007/FR-013/SCN-006/SC-003/DESIGN-REQ-008.
- [X] T012 Add verification-first unit tests for SecretRef and provider-profile reference source labels and redaction in `tests/unit/services/test_settings_catalog.py` covering FR-011/SCN-005/SC-005/DESIGN-REQ-006/DESIGN-REQ-007.
- [X] T013 Add unit traceability guard coverage in `tests/unit/specs/test_mm655_traceability.py` proving `specs/340-effective-value-resolver/spec.md`, `plan.md`, and `tasks.md` preserve `MM-655`, the original preset brief, FR-014, SCN-007, and SC-006.
- [X] T014 Run `pytest tests/unit/services/test_settings_catalog.py tests/unit/specs/test_mm655_traceability.py -q` and confirm T008 through T013 fail only for the intended missing or partial MM-655 behavior before production changes.

### Integration Tests (write first)

- [X] T015 Add failing API contract tests for effective single-value and list response shape, default metadata, inheritance state, canonical source labels, and activation metadata in `tests/unit/api_service/api/routers/test_settings_api.py` covering FR-001/FR-003/FR-006/FR-008/FR-009/SCN-001/SC-004/contracts/settings-effective-values-api.md.
- [X] T016 Add failing API contract tests for operator-lock read-only behavior and populated read-only reason in `tests/unit/api_service/api/routers/test_settings_api.py` covering FR-005/FR-010/SCN-004/SC-002/contracts/settings-effective-values-api.md.
- [X] T017 Add failing API contract tests for the diagnostic matrix and secret-safe provider-profile/SecretRef reference output in `tests/unit/api_service/api/routers/test_settings_api.py` covering FR-007/FR-011/FR-013/SCN-005/SCN-006/SC-003/SC-005/contracts/settings-effective-values-api.md.
- [X] T018 Add hermetic `integration_ci` contract tests for effective read, diagnostics read, workspace/user override precedence, operator lock precedence, SecretRef reference safety, provider-profile missing diagnostics, and post-migration invalid diagnostics in `tests/integration/api/test_settings_effective_values_contract.py` covering FR-001 through FR-013, SCN-001 through SCN-006, SC-001 through SC-005, and contracts/settings-effective-values-api.md.
- [X] T019 Run `pytest tests/unit/api_service/api/routers/test_settings_api.py -q` and confirm T015 through T017 fail only for the intended missing or partial MM-655 API behavior before production changes.
- [X] T020 Run `pytest tests/integration/api/test_settings_effective_values_contract.py -m integration_ci -q` and confirm T018 fails for intended missing MM-655 contract behavior or passes already-verified boundary behavior before production changes.

### Red-First Confirmation

- [X] T021 Record the red-first outcome from T014, T019, and T020 in a local handoff artifact under `/work/agent_jobs/mm:d028c981-c6b8-4edb-9192-9acb2d9e1de4/artifacts/mm655-red-first.md` before editing production code for FR-001/FR-003/FR-005/FR-006/FR-007/FR-008/FR-009/FR-010/FR-013.

### Conditional Fallback For Implemented-Unverified Rows

- [X] T022 If T012 or T017 exposes SecretRef/provider-profile redaction or separation gaps, harden reference output in `api_service/services/settings_catalog.py` and `api_service/api/routers/settings.py` for FR-011/SCN-005/SC-005/DESIGN-REQ-006/DESIGN-REQ-007.
- [X] T023 If T013 exposes missing MM-655 traceability, update `specs/340-effective-value-resolver/spec.md`, `specs/340-effective-value-resolver/plan.md`, or `specs/340-effective-value-resolver/tasks.md` to preserve MM-655 and the original preset brief for FR-014/SCN-007/SC-006.
- [X] T024 If T018 exposes missing future task creation or workspace default refresh evidence, add the minimal route or service verification in `tests/integration/api/test_settings_effective_values_contract.py` and fallback service code in `api_service/services/settings_catalog.py` for DESIGN-REQ-010.

### Implementation

- [X] T025 Extend `EffectiveSettingValue`, `SettingDescriptor` mapping, and diagnostic read models in `api_service/services/settings_catalog.py` with default value, inheritance state, read-only state, read-only reason, reload flags, and affected-system metadata required by FR-001/FR-008/FR-009/SCN-001/SC-004/DESIGN-REQ-001.
- [X] T026 Replace superseded internal source labels with the canonical source vocabulary in `api_service/services/settings_catalog.py`, updating config/default, provider-profile reference, SecretRef reference, and migrated invalid handling for FR-003/FR-006/SC-001/DESIGN-REQ-002/DESIGN-REQ-004.
- [X] T027 Add operator-lock candidate modeling, precedence, source `operator_lock`, write blocking, and descriptor/effective read-only output in `api_service/services/settings_catalog.py` for FR-005/FR-010/SCN-004/SC-002/DESIGN-REQ-003/DESIGN-REQ-005/DESIGN-REQ-012.
- [X] T028 Add distinct diagnostic codes and source explanations for no default, inherited null, intentional null override, unresolved SecretRef, missing provider profile, policy-blocked value, and post-migration invalid value in `api_service/services/settings_catalog.py` for FR-007/FR-013/SCN-006/SC-003/DESIGN-REQ-008.
- [X] T029 Update Settings router serialization and structured error behavior in `api_service/api/routers/settings.py` so `/api/v1/settings/effective`, `/api/v1/settings/effective/{key}`, and `/api/v1/settings/diagnostics` satisfy `specs/340-effective-value-resolver/contracts/settings-effective-values-api.md` for FR-001/FR-005/FR-007/FR-009/FR-010/FR-013.
- [X] T030 Update existing source-label and diagnostics assertions in `tests/unit/services/test_settings_catalog.py` to remove superseded internal labels rather than preserving compatibility aliases for FR-006/DESIGN-REQ-002.
- [X] T031 Update existing Settings API assertions in `tests/unit/api_service/api/routers/test_settings_api.py` to match the canonical source vocabulary and complete effective value contract for FR-001/FR-006/contracts/settings-effective-values-api.md.

### Story Validation

- [X] T032 Run `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm655_traceability.py -q` and fix only MM-655 scoped failures in `api_service/services/settings_catalog.py`, `api_service/api/routers/settings.py`, `tests/unit/services/test_settings_catalog.py`, `tests/unit/api_service/api/routers/test_settings_api.py`, and `tests/unit/specs/test_mm655_traceability.py`.
- [X] T033 Run `pytest tests/integration/api/test_settings_effective_values_contract.py -m integration_ci -q` and fix only MM-655 scoped integration failures in `api_service/services/settings_catalog.py`, `api_service/api/routers/settings.py`, and `tests/integration/api/test_settings_effective_values_contract.py`.
- [X] T034 Validate the independent story manually against `specs/340-effective-value-resolver/quickstart.md`, including source precedence, operator locks, SecretRef/provider-profile safety, diagnostic matrix, and MM-655 traceability, then record deviations in `/work/agent_jobs/mm:d028c981-c6b8-4edb-9192-9acb2d9e1de4/artifacts/mm655-story-validation.md`.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T035 [P] Review `specs/340-effective-value-resolver/contracts/settings-effective-values-api.md` against final API behavior and update only if the contract drifted during implementation.
- [X] T036 [P] Review `specs/340-effective-value-resolver/data-model.md` against final effective value, operator lock, and diagnostic behavior and update only if the model drifted during implementation.
- [X] T037 [P] Review `specs/340-effective-value-resolver/quickstart.md` against final validation commands and update only if test commands or manual checks drifted during implementation.
- [X] T038 Run `./tools/test_unit.sh` for final unit verification and capture the result in `specs/340-effective-value-resolver/verification.md` for FR-012/SC-001 through SC-006.
- [X] T039 Run `./tools/test_integration.sh` if T018 introduced or changed `integration_ci` coverage, and capture the result in `specs/340-effective-value-resolver/verification.md` for contracts/settings-effective-values-api.md and FR-012.
- [ ] T040 Run `/moonspec-verify` for `specs/340-effective-value-resolver/spec.md` after implementation and tests pass, producing final verification evidence that preserves `MM-655`, the original preset brief, FR-001 through FR-014, SCN-001 through SCN-007, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-012.

---

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 setup has no dependencies.
- Phase 2 foundational inspection depends on Phase 1.
- Phase 3 story work depends on Phase 2.
- Phase 4 polish and verification depends on Phase 3 tests and implementation passing.

### Within The Story

- T008 through T013 must be written before T014.
- T015 through T018 must be written before T019 and T020.
- T021 must complete before production changes T025 through T031.
- T022 through T024 are conditional fallback tasks for implemented-unverified rows and run only if verification tests expose gaps.
- T025 through T031 depend on red-first evidence from T014, T019, and T020.
- T032 through T034 validate the completed story after implementation.
- T040 is final and must run only after T038 and required T039 evidence is available.

### Parallel Opportunities

- T008 through T013 should not run concurrently unless the team coordinates edits because they share `tests/unit/services/test_settings_catalog.py`.
- T015 through T017 should not run concurrently unless the team coordinates edits because they share `tests/unit/api_service/api/routers/test_settings_api.py`.
- T018 can run in parallel with T015 through T017 because it creates `tests/integration/api/test_settings_effective_values_contract.py`.
- T035, T036, and T037 can run in parallel because they touch separate design artifacts.

---

## Parallel Example: Story Phase

```bash
# Launch API and integration test authoring together:
Task: "Add failing API effective value contract tests in tests/unit/api_service/api/routers/test_settings_api.py"
Task: "Add hermetic integration contract tests in tests/integration/api/test_settings_effective_values_contract.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to confirm the active artifacts and current resolver boundary.
2. Write red-first unit tests for partial and missing rows: FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-013, SCN-001, SCN-004, SCN-006, SC-001 through SC-004, and relevant DESIGN-REQ rows.
3. Write API and hermetic integration tests for the public effective-value and diagnostics contract.
4. Confirm new tests fail for expected missing or partial behavior before production changes.
5. Implement only the resolver, model, diagnostics, source vocabulary, lock, and route serialization work needed to satisfy the failing tests.
6. Run focused service, API, traceability, and integration tests until the single story passes.
7. Run final `./tools/test_unit.sh` and required `./tools/test_integration.sh` evidence.
8. Run `/moonspec-verify` to validate the completed implementation against the original `MM-655` preset brief.

### Status Handling

- Code-and-test work: FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-012, FR-013, SCN-001, SCN-004, SCN-006, SC-001 through SC-004, DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-011, DESIGN-REQ-012.
- Verification-first with conditional fallback: FR-011, FR-014, SCN-005, SCN-007, SC-005, SC-006, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-010.
- Already verified, final validation only: FR-002, FR-004, SCN-002, SCN-003.
- Final verification work: FR-012 and all success criteria through `/moonspec-verify`.

## Notes

- This task list covers one story only.
- Do not implement unrelated Settings UI changes unless tests prove Mission Control cannot consume the completed backend contract.
- Do not add new persistent tables unless implementation proves operator locks cannot be represented through existing configuration/registry policy surfaces.
- Do not preserve compatibility aliases for superseded internal source labels; update all callers/tests/docs in the same change.
- Preserve Jira issue key `MM-655` in all implementation notes, verification output, commit text, and pull request metadata.

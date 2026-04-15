# Tasks: Auth Security Boundaries

**Input**: Design documents from `/specs/177-auth-security-boundaries/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one security-boundary story for MM-335.

**Source Traceability**: FR-001 through FR-010, SC-001 through SC-005, acceptance scenarios 1-4, and DESIGN-REQ-008/009/017/018/019/021/022 are covered by the test and implementation tasks below.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py`
- Integration tests: `./tools/test_integration.sh` only if Temporal workflow/activity boundaries change
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing project structure and test harnesses are sufficient.

- [X] T001 Confirm MoonSpec feature directory and active feature pointer in `specs/177-auth-security-boundaries/` and `.specify/feature.json`
- [X] T002 Confirm existing pytest/unit tooling and workload/API test locations in `tests/unit/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Identify exact API and workload boundaries before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Inspect provider profile API authorization and response serialization boundaries in `api_service/api/routers/provider_profiles.py` for FR-003/FR-005/DESIGN-REQ-018/019
- [X] T004 Inspect OAuth session API owner scoping and response serialization boundaries in `api_service/api/routers/oauth_sessions.py` and `api_service/api/schemas_oauth_sessions.py` for FR-003/FR-004/DESIGN-REQ-018/019
- [X] T005 Inspect workload mount validation and artifact/result publication boundaries in `moonmind/schemas/workload_models.py` and `moonmind/workloads/docker_launcher.py` for FR-001/FR-002/FR-006/FR-007/DESIGN-REQ-008/009/021/022

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Verify OAuth Credential Security Boundaries

**Summary**: As a security reviewer, I want OAuth credential boundaries to be enforced across workflows, browser surfaces, logs, artifacts, and workload launches so that durable provider credentials cannot leak or be inherited accidentally.

**Independent Test**: Execute boundary tests with secret-like fixture credential files and environment values across OAuth status APIs, provider profile APIs, managed-session launch metadata, artifact/log publication, and workload launch.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-022.

**Test Plan**:

- Unit: Provider profile authorization, OAuth response sanitization, workload auth-volume validation, workload artifact/result redaction.
- Integration: No Temporal workflow/activity boundary change is planned; integration command is reserved if implementation touches those contracts.

### Unit Tests (write first)

- [X] T006 [P] Add failing unit tests for provider-profile management authorization and sanitized responses covering FR-003/FR-004/FR-005/SC-002/DESIGN-REQ-018/019 in `tests/unit/api_service/api/routers/test_provider_profiles.py`
- [X] T007 [P] Add failing unit tests for OAuth session sanitized failure responses covering FR-003/FR-004/SC-001/DESIGN-REQ-009/019 in `tests/unit/api_service/api/routers/test_oauth_sessions.py`
- [X] T008 [P] Add failing unit tests for workload auth-volume fail-closed validation covering FR-006/FR-007/SC-003/SC-004/DESIGN-REQ-008/022 in `tests/unit/workloads/test_workload_contract.py`
- [X] T009 [P] Add failing unit tests for workload artifact/result redaction covering FR-001/FR-002/FR-008/FR-009/SC-001/DESIGN-REQ-009/017/021 in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T010 Run targeted unit tests to confirm T006-T009 fail for expected reasons

### Integration Tests (write first)

- [X] T011 Confirm no Temporal workflow/activity payload shape changed; if any boundary changed, add integration coverage under `tests/integration/services/temporal/workflows/`
- [X] T012 Run `./tools/test_integration.sh` only if T011 adds or changes Temporal integration coverage

### Implementation

- [X] T013 Implement provider-profile management authorization and sanitized response helpers in `api_service/api/routers/provider_profiles.py` for FR-003/FR-004/FR-005/DESIGN-REQ-018/019
- [X] T014 Implement OAuth failure/status response sanitization in `api_service/api/routers/oauth_sessions.py` for FR-003/FR-004/DESIGN-REQ-009/019
- [X] T015 Implement workload secret-like redaction for stdout/stderr, diagnostics, and result metadata in `moonmind/workloads/docker_launcher.py` for FR-001/FR-002/FR-008/FR-009/DESIGN-REQ-009/017/021
- [X] T016 Preserve and extend workload auth-volume validation evidence in `moonmind/schemas/workload_models.py` for FR-006/FR-007/DESIGN-REQ-008/022
- [X] T017 Run targeted unit tests and fix failures until the MM-335 story passes

**Checkpoint**: The story is fully functional, covered by unit tests at the relevant API and workload boundaries, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen completed story without changing scope.

- [X] T018 [P] Update quickstart or verification notes only if commands or evidence differ in `specs/177-auth-security-boundaries/quickstart.md`
- [X] T019 Run `./tools/test_unit.sh` for full required unit verification
- [X] T020 Run `/moonspec-verify` equivalent by creating `specs/177-auth-security-boundaries/verification.md` after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Complete.
- **Foundational (Phase 2)**: Complete.
- **Story (Phase 3)**: Depends on Foundational phase completion.
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing.

### Within The Story

- Unit tests T006-T009 must be written before implementation.
- Red-first confirmation T010 must run before T013-T016.
- Implementation tasks T013-T016 can proceed after T010.
- Story validation T017 must pass before polish.

### Parallel Opportunities

- T006, T007, T008, and T009 touch different files and can be authored in parallel.
- T013, T014, T015, and T016 touch separate implementation files and can be implemented independently after red-first confirmation.

---

## Implementation Strategy

1. Write failing unit tests for provider-profile, OAuth session, workload model, and workload launcher boundaries.
2. Confirm the new tests fail for the intended reasons.
3. Implement only the authorization, sanitization, and fail-closed behavior needed for MM-335.
4. Run targeted unit tests, then the full unit suite.
5. Produce verification evidence in `verification.md`.

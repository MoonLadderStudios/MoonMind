# Tasks: Profile-Backed Workload Contracts

**Input**: Design documents from `specs/249-profile-backed-workload-contracts/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/profile-backed-workload-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-500, the repository already contained most production behavior and unit coverage, so the story work is verification-first: preserve the canonical artifacts, add the missing dispatcher-boundary integration test, and verify the existing workload contract end to end.

**Organization**: Tasks are grouped around the single MM-500 story: keep `container.run_workload`, `container.start_helper`, and `container.stop_helper` profile-backed, preserve bounded helper lifecycle semantics, prove disabled-mode denial, keep curated tools aligned with the runner-profile model, and preserve MM-500 traceability.

**Source Traceability**: MM-500; FR-001 through FR-007; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-025.

**Requirement Status Summary**: verification-first. Runtime behavior for FR-001 through FR-006 and DESIGN-REQ-012/017/018/025 is already implemented in the repository and now has direct unit evidence; MM-500 required feature-local traceability artifacts plus one hermetic integration boundary to upgrade those rows to implemented-verified. FR-007 and SC-006 are satisfied by the new MoonSpec artifact set and final verification evidence.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the MM-500 source brief, feature scope, and repo surfaces before verification work.

- [X] T001 Confirm `docs/tmp/jira-orchestration-inputs/MM-500-moonspec-orchestration-input.md` is the canonical MM-500 source input and no existing MM-500 spec directory exists under `specs/`
- [X] T002 Confirm the MM-500 runtime touchpoints in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, and the existing workload unit suites

---

## Phase 2: Foundational

**Purpose**: Lock the feature-local artifact and validation shape before story verification.

- [X] T003 Confirm MM-500 needs no `data-model.md`, migration, or new persistent storage because the story is a runtime contract verification story
- [X] T004 Confirm the focused unit suites and the hermetic integration boundary are the correct validation paths for FR-001 through FR-006 and DESIGN-REQ-012/017/018/025

**Checkpoint**: Foundation ready - story verification work can begin.

---

## Phase 3: Story - Run Profile-Backed Workloads

**Summary**: As a workflow author, I want Docker-backed workload and helper tools to stay profile-backed so MoonMind launches only approved container shapes instead of arbitrary raw container requests.

**Independent Test**: Through the worker-facing tool dispatcher, invoke `container.run_workload`, `container.start_helper`, and `container.stop_helper` with approved profiles plus invalid raw-container fields and disabled-mode execution, then verify approved profile-backed requests resolve through the runner profile registry, helpers remain bounded-service lifecycles, invalid raw fields are rejected, disabled mode denies execution, curated tools remain profile-backed, and MM-500 traceability is preserved.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-025

**Unit Test Plan**:

- Reuse and rerun the existing workload contract, tool-bridge, activity-runtime, and launcher unit suites.

**Integration Test Plan**:

- Add one `integration_ci` boundary in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering approved one-shot workload execution, raw-field rejection, bounded helper lifecycle behavior, and disabled-mode denial.
- Reuse existing curated-tool integration evidence in `tests/integration/temporal/test_integration_ci_tool_contract.py`.

### Verification Tests

- [X] T005 [P] Add `integration_ci` coverage for FR-001, FR-002, SC-001, and DESIGN-REQ-018 in `tests/integration/temporal/test_profile_backed_workload_contract.py` verifying approved `container.run_workload` requests resolve through an approved runner profile
- [X] T006 [P] Add `integration_ci` coverage for FR-003, SC-002, and DESIGN-REQ-017 in `tests/integration/temporal/test_profile_backed_workload_contract.py` verifying raw container fields are rejected at the dispatcher/runtime boundary
- [X] T007 [P] Add `integration_ci` coverage for FR-004, SC-003, DESIGN-REQ-012, and DESIGN-REQ-018 in `tests/integration/temporal/test_profile_backed_workload_contract.py` verifying bounded helper lifecycle behavior for `container.start_helper` and `container.stop_helper`
- [X] T008 [P] Add `integration_ci` coverage for FR-006 and SC-004 in `tests/integration/temporal/test_profile_backed_workload_contract.py` verifying disabled-mode denial for the profile-backed tool path

### Story Validation

- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py` and confirm the MM-500-focused workload unit suites pass
- [X] T010 Attempt `./tools/test_integration.sh`, record the managed-session Docker-socket blocker, then run `pytest tests/integration/temporal/test_profile_backed_workload_contract.py tests/integration/temporal/test_integration_ci_tool_contract.py -q --tb=short -m 'integration_ci'` as a focused fallback and confirm the MM-500 integration boundary plus existing integration-ci curated-tool evidence pass
- [X] T011 Review `tests/integration/temporal/test_integration_ci_tool_contract.py` together with the new MM-500 integration boundary to confirm FR-005 and DESIGN-REQ-025 remain covered without widening the profile-backed path
- [X] T012 Review `spec.md`, `plan.md`, `research.md`, `contracts/profile-backed-workload-contract.md`, `quickstart.md`, and `docs/tmp/jira-orchestration-inputs/MM-500-moonspec-orchestration-input.md` to confirm FR-007 and SC-006 preserve MM-500 across downstream artifacts

**Checkpoint**: MM-500 is complete when the existing profile-backed workload/helper implementation is proven at unit and integration boundaries and the canonical artifact set preserves the Jira source brief.

---

## Phase 4: Polish And Verification

**Purpose**: Final traceability and read-only verification for the completed story.

- [X] T013 [P] Align the feature-local artifacts in `specs/249-profile-backed-workload-contracts/` after the integration boundary was added so terminology, traceability, and test commands stay coherent
- [X] T014 Run `/moonspec-verify` for `specs/249-profile-backed-workload-contracts/` and produce a final evidence-backed verification report in the task closeout covering MM-500, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-025

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies
- Foundational (Phase 2): depends on Setup completion
- Story (Phase 3): depends on Foundational completion
- Polish (Phase 4): depends on story validation completing

### Within The Story

- T005-T008 define the missing integration boundary before story-level validation.
- T009 and T010 provide the required unit and integration evidence.
- T011 confirms curated-tool alignment using existing integration evidence.
- T012 and T013 preserve MM-500 traceability before final verification.

### Parallel Opportunities

- T005-T008 touch the same new integration file and therefore execute sequentially in practice even though they describe distinct coverage goals.
- T012 and T013 can run in parallel with verification preparation after tests pass.

## Implementation Strategy

1. Preserve MM-500 as the canonical MoonSpec source input and create the feature-local artifact set.
2. Add one missing hermetic integration boundary for the existing profile-backed workload and helper contract.
3. Rerun the focused workload unit suites.
4. Rerun the hermetic integration suite.
5. Reconcile curated-tool evidence and final traceability.
6. Finish with MoonSpec verification against the original MM-500 brief.

# Tasks: Workflow Docker Access Setting

**Input**: Design documents from `specs/237-workflow-docker-access/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration/activity-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover one single-story runtime feature for MM-476.

**Source Traceability**: MM-476, FR-001 through FR-011, SC-001 through SC-006.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/integration/temporal/test_integration_ci_tool_contract.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm current Docker workload boundaries and test extension points.

- [X] T001 Inspect settings defaults and workflow runtime Docker dependency construction in `moonmind/config/settings.py` and `moonmind/workflows/temporal/worker_runtime.py` for FR-001-FR-003
- [X] T002 Inspect existing DooD tool and workload activity code in `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-004-FR-009
- [X] T003 Inspect existing tests in `tests/unit/config/test_settings.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_pentest_tool_contract.py`

---

## Phase 2: Foundational

**Purpose**: Establish the shared policy and curated tool contract before story code.

- [X] T004 Record the setting and curated tool contracts in `specs/237-workflow-docker-access/contracts/workflow-docker-access-tool-contract.md` for FR-001-FR-009
- [X] T005 Record the implementation target and validation commands in `specs/237-workflow-docker-access/plan.md`, `research.md`, `data-model.md`, and `quickstart.md`

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Gate Workflow Docker Workloads

**Summary**: As a MoonMind operator, I want a single runtime setting to enable or deny workflow Docker-backed tool execution so that integration-test and workload access stays explicit, auditable, and limited to the existing DooD boundary.

**Independent Test**: Configure `MOONMIND_WORKFLOW_DOCKER_ENABLED` to both `true` and `false`, invoke an approved Docker-backed workflow tool, and verify enabled requests reach the workload launcher while disabled requests fail before launcher invocation with `docker_workflows_disabled`.

**Traceability**: FR-001 through FR-011, SC-001 through SC-006.

**Test Plan**:

- Unit: settings defaults/env override, generic DooD denial, direct `workload.run` denial, curated integration-CI mapping, result shape.
- Integration/activity boundary: curated `moonmind.integration_ci` routes through dispatcher semantics and returns workload artifact refs without starting an agent session.

### Unit Tests (write first)

- [X] T006 [P] Add failing settings tests for default enabled and `MOONMIND_WORKFLOW_DOCKER_ENABLED=false` in `tests/unit/config/test_settings.py` covering FR-001, FR-002, SC-001
- [X] T007 [P] Add failing workload bridge tests proving disabled `container.run_workload` and `moonmind.integration_ci` raise `docker_workflows_disabled` before registry or launcher calls in `tests/unit/workloads/test_workload_tool_bridge.py` covering FR-004, FR-005, FR-006, SC-002
- [X] T008 [P] Add failing workload bridge tests proving `moonmind.integration_ci` maps to profile `moonmind-integration-ci`, command `./tools/test_integration.sh`, and existing workload result outputs in `tests/unit/workloads/test_workload_tool_bridge.py` covering FR-008, FR-009, SC-004
- [X] T009 [P] Add failing activity tests proving direct `workload.run` denial happens before registry or launcher calls in `tests/unit/workflows/temporal/test_workload_run_activity.py` covering FR-004, FR-005, FR-006, SC-002
- [X] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py` and confirm T006-T009 fail for missing MM-476 behavior before implementation

### Integration Tests (write first)

- [X] T011 [P] Add failing integration/activity-boundary test for `moonmind.integration_ci` dispatcher routing in `tests/integration/temporal/test_integration_ci_tool_contract.py` covering FR-008, FR-009, SC-003, SC-004
- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/integration/temporal/test_integration_ci_tool_contract.py` and confirm T011 fails for missing MM-476 behavior before implementation

### Implementation

- [X] T013 Add `workflow_docker_enabled` to `WorkflowSettings` in `moonmind/config/settings.py` for FR-001, FR-002
- [X] T014 Add `moonmind.integration_ci` tool definition and request mapping to `moonmind/workloads/tool_bridge.py` for FR-008, FR-009
- [X] T015 Gate generic and curated DooD tool handlers in `moonmind/workloads/tool_bridge.py` before registry validation or launcher invocation for FR-004, FR-005, FR-006
- [X] T016 Gate direct `workload.run` activity execution in `moonmind/workflows/temporal/activity_runtime.py` before registry validation or launcher invocation for FR-004, FR-005, FR-006
- [X] T017 Pass the configured setting from `moonmind/workflows/temporal/worker_runtime.py` into workload handlers and activity runtime for FR-003-FR-006
- [X] T018 Add the `moonmind-integration-ci` runner profile to `config/workloads/default-runner-profiles.yaml` for FR-008
- [X] T019 Verify normal managed-session Docker launch code is unchanged and does not gain raw `/var/run/docker.sock` mounts for FR-007, SC-005
- [X] T020 Run focused unit and integration tests from T010 and T012, then fix failures until the story passes end to end

**Checkpoint**: The story is fully functional, covered by unit and activity-boundary tests, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T021 [P] Check traceability for MM-476 in `docs/tmp/jira-orchestration-inputs/MM-476-moonspec-orchestration-input.md` and `specs/237-workflow-docker-access/`
- [X] T022 Run quickstart validation commands from `specs/237-workflow-docker-access/quickstart.md`
- [X] T023 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T024 Run `/moonspec-verify` to validate the final implementation against the original MM-476 feature request

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup: no dependencies
- Foundational: depends on Setup completion
- Story: depends on Foundational completion
- Polish: depends on story tests passing

### Within The Story

- Unit tests T006-T009 must be written before implementation tasks T013-T018.
- Integration/activity-boundary test T011 must be written before implementation tasks T014-T018.
- Red-first confirmations T010 and T012 must complete before production code tasks T013-T018.
- Settings T013 and gate wiring T017 precede final direct runtime verification.
- Story validation T020 precedes polish and final verification.

### Parallel Opportunities

- T006, T007, T008, T009, and T011 can be authored in parallel because they touch different test modules or distinct sections.
- T021 can run in parallel with any non-mutating verification once implementation is complete.

---

## Implementation Strategy

1. Write focused tests for settings, policy denial, curated integration-CI mapping, and direct activity denial.
2. Confirm the new tests fail for the expected missing behavior.
3. Add the setting and propagate it into workload tool handlers and activity runtime.
4. Add the curated integration-CI tool/profile mapping through the existing workload result contract.
5. Run focused validation, then quickstart/full unit verification, then `/moonspec-verify`.

## Notes

- Do not modify `./tools/test_integration.sh`.
- Do not add raw Docker socket mounts to normal agent/session containers.
- Keep MM-476 traceability in artifacts and final verification evidence.

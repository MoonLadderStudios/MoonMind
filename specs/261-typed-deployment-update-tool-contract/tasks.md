# Tasks: Typed Deployment Update Tool Contract

**Input**: Design documents from `/specs/261-typed-deployment-update-tool-contract/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style plan validation are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-519 user story.

**Source Traceability**: Tasks cover FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, and DESIGN-REQ-009.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py tests/unit/api/routers/test_deployment_operations.py -q`
- Integration tests: `pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing contract infrastructure can host the story.

- [X] T001 Inspect existing tool definition, registry snapshot, and plan validation helpers in `moonmind/workflows/skills/tool_plan_contracts.py`, `moonmind/workflows/skills/tool_registry.py`, and `moonmind/workflows/skills/plan_validation.py`. (FR-001-FR-008)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new foundation is needed beyond existing registry and plan validation contracts.

- [X] T002 Confirm existing policy-gated API queued-run payload location in `api_service/services/deployment_operations.py`. (FR-009)

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Typed Deployment Update Tool Contract

**Summary**: As an operator of MoonMind workflows, I need `deployment.update_compose_stack` registered as a typed privileged executable tool, so deployment updates can be orchestrated through the plan/tool system rather than ad hoc shell execution.

**Independent Test**: Validate the canonical tool definition and representative plan nodes against the existing registry and plan validation helpers.

**Traceability**: FR-001-FR-010, SC-001-SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009

**Test Plan**:

- Unit: tool definition schema, capabilities, executor, retry policy, admin role, API queued-run shared constants.
- Integration: pinned registry snapshot plus valid and invalid representative plan validation.

### Unit Tests (write first) ⚠️

- [X] T003 [P] Add failing unit tests for deployment tool definition name, version, schema, capabilities, executor, security, and retry policy in `tests/unit/workflows/skills/test_deployment_tool_contracts.py`. (FR-001-FR-006, SC-001, DESIGN-REQ-001-DESIGN-REQ-006)
- [X] T004 [P] Update deployment API unit assertions in `tests/unit/api/routers/test_deployment_operations.py` to prove queued-run payloads use the shared canonical tool name/version. (FR-009, SC-004, DESIGN-REQ-007)
- [X] T005 Run `pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py tests/unit/api/routers/test_deployment_operations.py -q` to confirm T003-T004 fail for the expected missing contract. (FR-001-FR-009)

### Integration Tests (write first) ⚠️

- [X] T006 [P] Add plan validation tests in `tests/unit/workflows/skills/test_deployment_tool_contracts.py` for a valid representative deployment update plan node. (FR-007, SC-002, DESIGN-REQ-007)
- [X] T007 [P] Add plan validation rejection tests in `tests/unit/workflows/skills/test_deployment_tool_contracts.py` for `command`, `composeFile`, `hostPath`, and `updaterRunnerImage` inputs. (FR-008, SC-003, DESIGN-REQ-008)
- [X] T008 Run `pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py -q` to confirm T006-T007 fail for the expected missing contract. (FR-007-FR-008)

### Implementation

- [X] T009 Implement canonical deployment update tool contract constants and payload builder in `moonmind/workflows/skills/deployment_tools.py`. (FR-001-FR-006, DESIGN-REQ-001-DESIGN-REQ-006)
- [X] T010 Update `api_service/services/deployment_operations.py` to use canonical deployment tool constants for queued-run plan nodes and integration metadata. (FR-009, DESIGN-REQ-007)
- [X] T011 Run targeted unit and integration-style story validation commands and fix failures. (FR-001-FR-009, SC-001-SC-004)
- [X] T012 Run traceability check for MM-519 and DESIGN-REQ coverage across the feature artifacts, code, and tests. (FR-010, SC-005)

**Checkpoint**: The story is fully functional, covered by unit and integration-style tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and outcome recording.

- [X] T013 Run `./tools/test_unit.sh` for full required unit verification. (SC-001-SC-005)
- [X] T014 Run `/moonspec-verify` by producing `specs/261-typed-deployment-update-tool-contract/verification.md` with MM-519 traceability, test evidence, and final verdict. (FR-010, SC-005)

---

## Dependencies & Execution Order

- Phase 1 before Phase 2; Phase 2 before story tests.
- T003-T004 can run in parallel.
- T006-T007 can run in parallel after T003.
- T009-T010 run after red-first confirmation.
- T011-T014 complete final validation and verification.

## Implementation Strategy

1. Preserve the MM-519 Jira preset brief as the source in `spec.md`.
2. Add failing contract and plan-validation tests.
3. Implement the small shared tool contract helper and API constant binding.
4. Run targeted tests, full unit suite, traceability check, and final MoonSpec verification.

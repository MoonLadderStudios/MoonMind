# Tasks: Policy-Gated Deployment Update API

**Input**: `specs/260-policy-gated-deployment-update/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/deployment-operations.openapi.yaml`, `quickstart.md`

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_deployment_operations.py`
**Integration Test Command**: `./tools/test_integration.sh` for the broader hermetic suite when Docker is available; this story's API contract boundary is covered by FastAPI router tests in the unit tier.

**Source Traceability**: The original `MM-518` Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-012, acceptance scenarios 1-6, edge cases, SC-001 through SC-006, and DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, and DESIGN-REQ-018.

## Phase 1: Setup

- [X] T001 Create `specs/260-policy-gated-deployment-update/` MoonSpec artifact structure and set `.specify/feature.json`.

## Phase 2: Foundational

- [X] T002 [P] Add failing API-router tests for submit/read contracts and admin authorization in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T003 [P] Add failing validation tests for disallowed stack, repository, mode, missing reason, and arbitrary command/path fields in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T004 Confirm red-first failure for missing deployment operations router with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_deployment_operations.py`.

## Phase 3: Story - Policy-Gated Deployment Update Requests

**Summary**: Administrators can request and inspect deployment updates through bounded typed operations while unsafe inputs fail before workflow or tool execution.

**Independent Test**: Exercise deployment update endpoints as admin and non-admin callers with valid and invalid payloads, confirming queued run metadata for valid admin requests and pre-execution rejection for invalid or unauthorized requests.

**Traceability IDs**: FR-001-FR-012; SC-001-SC-006; DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-018.

### Unit Test Plan

- [X] T005 Cover valid administrator update submission in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T006 Cover non-admin rejection in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T007 Cover explicit policy error codes for invalid stack, repository, mode, and reason in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T008 Cover schema rejection for arbitrary shell and path fields in `tests/unit/api/routers/test_deployment_operations.py`.
- [X] T009 Cover typed state and image-target read responses in `tests/unit/api/routers/test_deployment_operations.py`.

### Integration Test Plan

- [X] T010 Cover the deployment API contract boundary with FastAPI router tests in `tests/unit/api/routers/test_deployment_operations.py`, including accepted update, rejected unauthorized update, rejected invalid policy values, typed stack state, and typed image-target responses.
- [X] T011 Attempt broader hermetic integration validation with `./tools/test_integration.sh`; record Docker socket unavailability when the managed workspace cannot run compose-backed integration tests.

### Implementation

- [X] T012 Implement deployment policy validation service in `api_service/services/deployment_operations.py`.
- [X] T013 Implement typed deployment request/response models and endpoints in `api_service/api/routers/deployment_operations.py`.
- [X] T014 Wire the deployment operations router into `api_service/main.py`.
- [X] T015 Run focused unit/API-router validation with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_deployment_operations.py`.

### Story Validation

- [X] T016 Validate the single story end-to-end through the focused API-router suite in `tests/unit/api/routers/test_deployment_operations.py`.

## Final Phase: Polish And Verification

- [X] T017 Preserve `MM-518` and source coverage IDs in `spec.md`, `plan.md`, `tasks.md`, and implementation summary.
- [X] T018 Run full unit validation with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T019 Run final `/moonspec-verify` equivalent against `specs/260-policy-gated-deployment-update/spec.md`.

# Tasks: Enforce Image Artifact Storage and Policy

**Input**: `/work/agent_jobs/mm:230435a6-2451-4a5d-9736-19e4bdb70014/repo/specs/195-enforce-image-artifact-policy/spec.md`  
**Plan**: `/work/agent_jobs/mm:230435a6-2451-4a5d-9736-19e4bdb70014/repo/specs/195-enforce-image-artifact-policy/plan.md`  
**Unit test command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration test command**: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/contract/test_temporal_execution_api.py -q`
**Focused iteration commands**: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_temporal_artifacts.py tests/contract/test_temporal_execution_api.py -q`

## Story Scope

This task list covers exactly one independently testable story from MM-368: uploaded image bytes are stored as first-class execution artifacts and governed by server-defined attachment policy so invalid or unsupported image inputs never start an execution.

**Independent test**: Submit task-shaped execution requests with valid and invalid objective-scoped and step-scoped image attachment refs. Valid refs must remain artifact-backed input attachments in the task snapshot; disabled, incomplete, oversized, unsupported, scriptable, future-field, incompatible-runtime, and reserved-namespace attempts must fail before execution starts.

## Source Traceability Summary

- DESIGN-REQ-008: T004, T008, T013, T015, T017
- DESIGN-REQ-009: T005, T009, T012, T016
- DESIGN-REQ-010: T006, T010, T012, T013, T016
- DESIGN-REQ-017: T007, T011, T014, T016
- FR-001 through FR-010: covered by T004-T018
- SC-001 through SC-005: validated by T004-T008 and T016-T019

## Phase 1: Setup

- [X] T001 Confirm active feature directory and branch in `.specify/feature.json` and `specs/195-enforce-image-artifact-policy/`.
- [X] T002 Generate Specify, Plan, research, data model, contract, and quickstart artifacts for MM-368 in `specs/195-enforce-image-artifact-policy/`.
- [X] T003 Update Codex agent context from the implementation plan via `.specify/scripts/bash/update-agent-context.sh`.

## Phase 2: Foundational Tests

- [X] T004 [P] Add failing unit tests for task `inputAttachments` preservation and disabled-policy rejection in `tests/unit/api/routers/test_executions.py`. Covers FR-002, FR-008, SC-001, SC-003.
- [X] T005 [P] Add failing unit tests for image content type allowlist and `image/svg+xml` rejection in `tests/unit/api/routers/test_executions.py`. Covers FR-003, FR-004, SC-002.
- [X] T006 [P] Add failing unit tests for max count, per-file size, total size, incomplete artifacts, unknown future fields, and unsupported target runtime with attachments in `tests/unit/api/routers/test_executions.py`. Covers FR-005, FR-006, FR-009, SC-002, SC-005.
- [X] T007 [P] Add failing unit tests for artifact completion signature validation and reserved input namespace rejection in `tests/unit/workflows/temporal/test_artifacts.py` or `tests/unit/api/routers/test_temporal_artifacts.py`. Covers FR-004, FR-007, SC-004.
- [X] T008 [P] Add failing integration/contract coverage for task-shaped execution preserving image attachment refs in `tests/contract/test_temporal_execution_api.py`. Covers DESIGN-REQ-008, SC-001.

## Phase 3: Implementation

- [X] T009 Implement canonical attachment ref validation helpers in `api_service/api/routers/executions.py`. Covers FR-002, FR-003, FR-004, FR-009.
- [X] T010 Implement execution-start policy validation for disabled policy, max count, per-file size, total size, incomplete artifacts, and missing artifacts in `api_service/api/routers/executions.py`. Covers FR-005, FR-006, FR-008.
- [X] T011 Preserve normalized `task.inputAttachments` and `task.steps[n].inputAttachments` in execution parameters and original task input snapshots in `api_service/api/routers/executions.py`. Covers FR-002, FR-010.
- [X] T012 Implement image attachment completion validation in `moonmind/workflows/temporal/artifacts.py`, including PNG/JPEG/WebP signature sniffing and SVG rejection. Covers FR-003, FR-004, FR-005.
- [X] T013 Add submitted attachment refs to execution artifact visibility and snapshot `attachmentRefs` in `api_service/api/routers/executions.py`. Covers FR-001, FR-002, FR-010.
- [X] T014 Reject worker-side uploads into reserved input attachment namespaces in the artifact API/service boundary in `moonmind/workflows/temporal/artifacts.py`. Covers FR-007.
- [X] T015 Update frontend Create-page attachment behavior only if server-owned linkage requires client adjustment in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`. Covers FR-008. Existing Create-page policy behavior already hides disabled attachment entry points; no frontend code change was required.

## Phase 4: Validation

- [X] T016 Run focused red/green unit coverage: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_temporal_artifacts.py -q`.
- [X] T017 Run focused integration/contract coverage: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/contract/test_temporal_execution_api.py -q`.
- [X] T018 Run final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T019 Run final `/moonspec-verify` equivalent story validation and write `specs/195-enforce-image-artifact-policy/verification.md`.

## Dependencies and Execution Order

1. T001-T003 complete before test and implementation work.
2. T004-T008 must be added and observed failing before T009-T015.
3. T009-T014 are backend implementation tasks; T015 is only required if frontend ownership changes.
4. T016-T019 are story validation and final `/moonspec-verify` tasks.

## Implementation Strategy

Use TDD at API/service boundaries: write failing validation tests first, implement the narrow helpers in existing modules, rerun focused tests, then run the full unit suite. Do not create new storage tables or compatibility aliases.

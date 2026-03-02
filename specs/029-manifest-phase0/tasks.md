# Tasks: Manifest Queue Phase 0 Alignment

**Input**: Design documents from `/specs/029-manifest-phase0/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Tests are required by spec (`DOC-REQ-005`) and must run via `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story to keep each increment independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable (different files, no blocking dependency)
- **[Story]**: User story label (`[US1]`, `[US2]`)
- Include exact file paths in each task description

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T003`, `T004`, `T007`, `T008`, `T011`, `T012`.
- Runtime validation tasks are explicitly present: `T005`, `T006`, `T009`, `T010`, `T015`.
- `DOC-REQ-*` implementation + validation coverage is enforced through `T001` (traceability matrix updates), `T014` (cross-artifact sync verification), and per-requirement task mappings in `specs/029-manifest-phase0/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Context)

**Purpose**: Lock scope + traceability inputs before runtime edits.

- [X] T001 Update DOC-REQ mapping notes and implementation/test targets in specs/029-manifest-phase0/contracts/requirements-traceability.md for DOC-REQ-001 through DOC-REQ-006.
- [X] T002 [P] Update quick validation expectations for queue and registry 422 error codes/messages in specs/029-manifest-phase0/quickstart.md (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare shared router/error-handling boundaries required by all user stories.

**⚠️ CRITICAL**: Complete this phase before user-story work.

- [X] T003 Implement/confirm manifest-specific validation branch entry in api_service/api/routers/agent_queue.py while preserving existing non-manifest queue fallback behavior (DOC-REQ-001, DOC-REQ-003, DOC-REQ-006).
- [X] T004 [P] Implement/confirm manifest registry validation boundary in api_service/api/routers/manifests.py without changing successful registry response payload shapes (DOC-REQ-004, DOC-REQ-006).

**Checkpoint**: Foundational router boundaries are ready for story-specific validation hardening.

---

## Phase 3: User Story 1 - Queue Submission Validation Is Actionable (Priority: P1) 🎯 MVP

**Goal**: Invalid manifest queue submissions return actionable 422 responses while non-manifest semantics remain unchanged.

**Independent Test**: Submit invalid manifest payloads through `POST /api/queue/jobs` and confirm `invalid_manifest_job` + contract message; submit invalid non-manifest payloads and confirm `invalid_queue_payload` remains unchanged.

### Tests for User Story 1

- [X] T005 [P] [US1] Add/adjust router test coverage for manifest queue validation 422 mapping (`invalid_manifest_job` + contract text) in tests/unit/api/routers/test_agent_queue.py (DOC-REQ-002, DOC-REQ-005).
- [X] T006 [P] [US1] Add/adjust regression coverage for non-manifest queue validation mapping (`invalid_queue_payload`) in tests/unit/api/routers/test_agent_queue.py (DOC-REQ-001, DOC-REQ-003, DOC-REQ-005).

### Implementation for User Story 1

- [X] T007 [US1] Update manifest `AgentQueueValidationError` handling in `create_job` within api_service/api/routers/agent_queue.py to return HTTP 422 with `code="invalid_manifest_job"` and `message=str(exc)` (DOC-REQ-002, DOC-REQ-006).
- [X] T008 [US1] Preserve generic queue validation mapping and success payload behavior for non-manifest types in api_service/api/routers/agent_queue.py (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004).

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Registry Upsert Validation Is Actionable (Priority: P2)

**Goal**: Invalid registry upserts return actionable manifest contract errors with stable code semantics.

**Independent Test**: Submit invalid YAML to `PUT /api/manifests/{name}` and confirm 422 with `invalid_manifest` + contract-derived text, while other manifest registry success responses remain unchanged.

### Tests for User Story 2

- [X] T009 [P] [US2] Add/adjust upsert validation test coverage for HTTP 422 `invalid_manifest` with contract-derived message in tests/unit/api/routers/test_manifests.py (DOC-REQ-004, DOC-REQ-005).
- [X] T010 [P] [US2] Add/adjust registry regression tests confirming unchanged success payload shapes for list/get/run flows in tests/unit/api/routers/test_manifests.py (DOC-REQ-004, DOC-REQ-005).

### Implementation for User Story 2

- [X] T011 [US2] Update `ManifestContractError` handling in `upsert_manifest` within api_service/api/routers/manifests.py to return HTTP 422 with `code="invalid_manifest"` and `message=str(exc)` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-006).
- [X] T012 [US2] Preserve manifest run submission 422 semantics and queue metadata response shape in api_service/api/routers/manifests.py (DOC-REQ-004).

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final consistency checks across runtime, contracts, and validation evidence.

- [X] T013 [P] Align manifest validation error code/message examples in specs/029-manifest-phase0/contracts/manifest-phase0.openapi.yaml with router behavior for `/api/queue/jobs` and `/api/manifests/{name}` (DOC-REQ-002, DOC-REQ-004).
- [X] T014 [P] Verify spec-level acceptance/traceability references stay synchronized in specs/029-manifest-phase0/spec.md and specs/029-manifest-phase0/contracts/requirements-traceability.md (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-006).
- [X] T015 Execute validation suite via ./tools/test_unit.sh and record pass/fail evidence in specs/029-manifest-phase0/quickstart.md (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: depends on Phase 1; blocks user stories.
- **Phase 3 (US1)**: depends on Phase 2.
- **Phase 4 (US2)**: depends on Phase 2; can proceed after US1 or in parallel once foundation is stable.
- **Phase 5 (Polish)**: depends on completion of desired user stories.

### User Story Dependencies

- **US1 (P1)**: no dependency on US2 once foundational tasks complete.
- **US2 (P2)**: no dependency on US1 runtime logic; only shared foundational router context.

### Within Each User Story

- Add/update tests first.
- Apply runtime router changes next.
- Re-run validation before story checkpoint sign-off.

### Parallel Opportunities

- T002 can run in parallel with T001.
- T004 can run in parallel with T003.
- US1 tests T005 and T006 can run in parallel.
- US2 tests T009 and T010 can run in parallel.
- Polish tasks T013 and T014 can run in parallel before T015.

---

## Parallel Example: User Story 1

```bash
# Parallel test updates for US1:
Task: "T005 Add/adjust manifest queue validation mapping test in tests/unit/api/routers/test_agent_queue.py"
Task: "T006 Add/adjust non-manifest regression mapping test in tests/unit/api/routers/test_agent_queue.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Deliver US1 (Phase 3) and validate independently.
3. Demo actionable queue validation behavior.

### Incremental Delivery

1. Ship US1 queue validation hardening.
2. Ship US2 registry upsert hardening.
3. Finish with contract/docs/test-suite verification.

### Runtime Scope Guard

1. Ensure runtime production files are touched under `api_service/`.
2. Ensure validation tasks touch `tests/` and run `./tools/test_unit.sh`.
3. Keep docs updates supportive, not a substitute for runtime/test changes.

---

## Notes

- All tasks are checklist-formatted and dependency ordered.
- `DOC-REQ-001` through `DOC-REQ-006` are explicitly carried in implementation and validation tasks.
- Final completion requires runtime code changes plus validation evidence (`./tools/test_unit.sh`).
- Prompt B runtime gate is satisfied only when runtime implementation tasks and validation tasks above are completed together.

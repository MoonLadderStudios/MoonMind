# Tasks: Agent Queue Artifact Upload (Milestone 2)

**Input**: Design documents from `/specs/010-agent-queue-artifacts/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare Milestone 2 scaffolding and file layout.

- [X] T001 Verify branch `010-agent-queue-artifacts` and milestone artifacts exist in `specs/010-agent-queue-artifacts/`.
- [X] T002 Create artifact contract and planning files in `specs/010-agent-queue-artifacts/contracts/`.
- [X] T003 [P] Add queue artifact test module scaffolding in `tests/unit/workflows/agent_queue/` and `tests/unit/api/routers/`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared persistence/config/storage primitives required by all user stories.

- [X] T004 Add `AGENT_JOB_ARTIFACT_ROOT` and upload-size settings in `moonmind/config/settings.py` (DOC-REQ-001, DOC-REQ-008).
- [X] T005 Add `AgentJobArtifact` ORM model and relationship fields in `moonmind/workflows/agent_queue/models.py` (DOC-REQ-005).
- [X] T006 Add migration `api_service/migrations/versions/202602130002_agent_job_artifacts.py` for `agent_job_artifacts` table and indexes (DOC-REQ-005).
- [X] T007 [P] Extend queue schemas for artifact metadata responses in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-003, DOC-REQ-006).
- [X] T008 Implement job-scoped artifact storage helper with traversal protections in `moonmind/workflows/agent_queue/storage.py` (DOC-REQ-004, DOC-REQ-007).
- [X] T009 Extend queue repository/service with artifact metadata CRUD and job/artifact ownership checks in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007).

**Checkpoint**: Shared config, storage, and metadata persistence are ready for API story work.

---

## Phase 3: User Story 1 - Worker Uploads Job Artifacts (Priority: P1) 🎯 MVP

**Goal**: Workers can upload artifacts for queue jobs with metadata capture.

**Independent Test**: `POST /api/queue/jobs/{jobId}/artifacts/upload` persists file and metadata for valid multipart requests.

### Tests for User Story 1

- [X] T010 [P] [US1] Add repository/service unit tests for artifact metadata creation in `tests/unit/workflows/agent_queue/test_artifact_repositories.py` (DOC-REQ-001, DOC-REQ-005).
- [X] T011 [P] [US1] Add API unit tests for upload success and multipart field handling in `tests/unit/api/routers/test_agent_queue_artifacts.py` (DOC-REQ-002, DOC-REQ-003).

### Implementation for User Story 1

- [X] T012 [US1] Implement upload service flow (job validation, size check, storage write, metadata insert) in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005).
- [X] T013 [US1] Implement `POST /api/queue/jobs/{jobId}/artifacts/upload` handler in `api_service/api/routers/agent_queue.py` using multipart upload fields (DOC-REQ-002, DOC-REQ-003).

**Checkpoint**: Upload endpoint is functional and independently testable.

---

## Phase 4: User Story 2 - Operator Lists and Downloads Artifacts (Priority: P1)

**Goal**: Operators can inspect and retrieve artifacts per queue job.

**Independent Test**: Uploaded artifacts can be listed and downloaded through job-scoped endpoints.

### Tests for User Story 2

- [X] T014 [P] [US2] Add API unit tests for artifact list/download success and not-found conditions in `tests/unit/api/routers/test_agent_queue_artifacts.py` (DOC-REQ-006).
- [X] T015 [P] [US2] Add repository tests for job/artifact mismatch rejection in `tests/unit/workflows/agent_queue/test_artifact_repositories.py` (DOC-REQ-006, DOC-REQ-007).

### Implementation for User Story 2

- [X] T016 [US2] Implement artifact list and get-by-id repository/service methods in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-006).
- [X] T017 [US2] Implement `GET /api/queue/jobs/{jobId}/artifacts` and `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download` in `api_service/api/routers/agent_queue.py` (DOC-REQ-006).

**Checkpoint**: Artifact retrieval is functional and independently testable.

---

## Phase 5: User Story 3 - Artifact Path and Size Safety (Priority: P2)

**Goal**: Upload path and size constraints are enforced to prevent abuse.

**Independent Test**: Traversal payloads and oversized uploads are rejected without writing files/metadata.

### Tests for User Story 3

- [X] T018 [P] [US3] Add storage tests for traversal token rejection and job-root isolation in `tests/unit/workflows/agent_queue/test_artifact_storage.py` (DOC-REQ-004, DOC-REQ-007).
- [X] T019 [P] [US3] Add API/service tests for upload size limit enforcement in `tests/unit/api/routers/test_agent_queue_artifacts.py` and `tests/unit/workflows/agent_queue/test_artifact_repositories.py` (DOC-REQ-007, DOC-REQ-008).

### Implementation for User Story 3

- [X] T020 [US3] Enforce maximum artifact upload bytes in queue service and return bounded validation errors in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-007).
- [X] T021 [US3] Enforce sanitized relative storage paths and reject absolute/traversal names in `moonmind/workflows/agent_queue/storage.py` (DOC-REQ-004, DOC-REQ-007).

**Checkpoint**: Security and size safety controls are functional and independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final cleanup, traceability checks, and validation command execution.

- [X] T022 [P] Wire artifact model dependencies and schema exports in `api_service/db/models.py`, `moonmind/workflows/agent_queue/__init__.py`, and `moonmind/schemas/__init__.py`.
- [X] T023 [P] Reconcile implementation with `specs/010-agent-queue-artifacts/contracts/requirements-traceability.md` and update drift.
- [ ] T024 Run unit validation via `./tools/test_unit.sh` focusing on new queue artifact tests (DOC-REQ-007).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- All user stories depend on foundational tasks T004-T009.
- Polish phase runs after selected user stories are complete.

### User Story Dependencies

- US1 starts after foundational phase.
- US2 depends on US1 artifact metadata/storage paths.
- US3 depends on upload flow from US1 and retrieval rules from US2.

### Parallel Opportunities

- T003, T007 can run in parallel during setup/foundation.
- T010/T011, T014/T015, and T018/T019 are parallelizable test tasks.
- T022/T023 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Complete setup and foundational phases.
2. Implement upload endpoint + metadata persistence.
3. Validate upload independently before retrieval/security expansion.

### Incremental Delivery

1. Add list/download endpoints and validations (US2).
2. Add traversal and size-limit hardening (US3).
3. Run full unit validation and close traceability.

### Runtime Scope Commitments

- Production runtime files will be modified in `api_service/`, `moonmind/`, and `api_service/migrations/`.
- Validation coverage will be delivered with new unit tests plus execution of `./tools/test_unit.sh`.

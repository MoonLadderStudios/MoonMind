# Tasks: Manifest Queue Plumbing (Phase 0)

**Input**: Design documents from `specs/028-manifest-queue/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align on scope and confirm repo health before code changes.

- [ ] T001 Review specs/028-manifest-queue/spec.md and plan.md to restate queue + registry deliverables (specs/028-manifest-queue)
- [ ] T002 Run baseline tests via ./tools/test_unit.sh to capture current failures before modifications (tools/test_unit.sh)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared scaffolding required by every user story.

- [ ] T003 Add reusable manifest fixtures/conftest helpers for queue tests (tests/unit/workflows/agent_queue/conftest.py)
- [ ] T004 Document manifest payload sample + capability matrix in data model notes for reference during implementation (specs/028-manifest-queue/data-model.md)

**Checkpoint**: Shared fixtures + references exist so story phases can focus on implementation details without re-deriving test data.

---

## Phase 3: User Story 1 - Queue Accepts Manifest Jobs (Priority: P1) 🎯 MVP

**Goal**: Agent Queue accepts `type="manifest"` jobs using a centralized job-type set.

**Independent Test**: POST `/api/queue/jobs` with a valid manifest payload should succeed and enqueue a job categorized as `manifest`. Invalid job types must still be rejected.

### Implementation

- [ ] T005 [P] [US1] Create moonmind/workflows/agent_queue/job_types.py exporting canonical + legacy + manifest job types for reuse
- [ ] T006 [US1] Update moonmind/workflows/agent_queue/service.py to import job_types, include `manifest`, and surface a clear validation error listing supported types
- [ ] T007 [P] [US1] Update worker token / policy plumbing (moonmind/workflows/agent_queue/models.py + related services) so capability filters allow `manifest` when requested

### Validation

- [ ] T008 [US1] Add queue service tests covering manifest allowlist + rejection paths (tests/unit/workflows/agent_queue/test_manifest_job_type.py)

**Checkpoint**: Agent Queue interface now recognizes `manifest`; other job types unaffected.

---

## Phase 4: User Story 2 - API Normalizes Manifest Payloads (Priority: P1)

**Goal**: Manifest contract module validates YAML, enforces name consistency, computes hashes, and derives capabilities before a job reaches the queue.

**Independent Test**: Calling `AgentQueueService.create_job(type="manifest", payload=...)` should yield a normalized payload with `manifestHash`, `manifestVersion`, derived capabilities, and deterministic run config merging; name mismatches generate 400 errors.

### Tests First (recommended)

- [ ] T009 [P] [US2] Write manifest contract unit tests for capability derivation, hash computation, options precedence, and token-free enforcement (tests/unit/workflows/agent_queue/test_manifest_contract.py)

### Implementation

- [ ] T010 [P] [US2] Implement moonmind/workflows/agent_queue/manifest_contract.py with `ManifestContractError`, `derive_required_capabilities`, and `normalize_manifest_job_payload`
- [ ] T011 [US2] Wire AgentQueueService.create_job to invoke manifest contract path + emit normalized payload metadata (moonmind/workflows/agent_queue/service.py)
- [ ] T012 [US2] Ensure manifest contract reuses shared manifest fixtures + raises descriptive validation errors for mismatched names or raw secrets (moonmind/workflows/agent_queue/manifest_contract.py)

### Validation

- [ ] T013 [US2] Extend queue service tests to assert normalized payload fields and errors flow through public API (tests/unit/workflows/agent_queue/test_manifest_job_type.py)

**Checkpoint**: Manifest jobs are normalized deterministically and blocked when invalid.

---

## Phase 5: User Story 3 - Manifest Registry CRUD + Run Submission (Priority: P2)

**Goal**: Provide `/api/manifests` CRUD + `/runs` endpoints backed by the manifest table and queue contract.

**Independent Test**: PUT + GET `/api/manifests/{name}` should persist YAML + hash metadata, and POST `/runs` should enqueue a manifest job referencing the stored manifest with derived capabilities visible in the response.

### Implementation

- [ ] T014 [P] [US3] Extend ManifestRecord columns and create matching Alembic migration for version/hash/run state metadata (api_service/db/models.py, api_service/migrations/versions)
- [ ] T015 [US3] Build manifests service to wrap DB CRUD + queue submission logic (api_service/services/manifests_service.py)
- [ ] T016 [US3] Implement FastAPI router for `/api/manifests` CRUD + `/runs`, wiring dependencies + auth (api_service/api/routers/manifests.py)
- [ ] T017 [P] [US3] Integrate router into application startup + dependency wiring (api_service/api/routers/__init__.py, api_service/main.py)

### Validation

- [ ] T018 [P] [US3] Add router/service unit tests covering CRUD, name mismatch, missing manifest, and queue submission hooks (tests/unit/api/routers/test_manifests.py)

**Checkpoint**: Operators can manage manifests over HTTP and submit manifest queue jobs via registry entries.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T019 [P] Refresh specs/028-manifest-queue/quickstart.md with any new smoke-test steps discovered during implementation
- [ ] T020 [P] Harden logging + error responses to ensure no raw secrets leak in manifest payloads (moonmind/workflows/agent_queue/manifest_contract.py and api_service/api/routers/manifests.py)
- [ ] T021 Re-run ./tools/test_unit.sh and capture artifacts/logs for review (tools/test_unit.sh)

---

## Dependencies & Execution Order

1. Complete Phase 1 setup to ensure scope alignment and baseline test signal.
2. Phase 2 fixtures/documentation unblock every user story; finish before touching runtime code.
3. Phase 3 (US1) must finish before manifest contract or registry work so queue accepts the new job type.
4. Phase 4 (US2) depends on US1 and provides normalization required by registry submissions.
5. Phase 5 (US3) depends on US2 because routers/service call into manifest contract for queue submissions.
6. Phase 6 polish tasks run after all targeted user stories reach their checkpoints.

## Parallel Opportunities

1. After Phase 2 completes, T005 + T007 (US1 implementation) can run in parallel while T009 prepares manifest contract tests.
2. Once manifest contract baseline tests exist, T010 + T011 can proceed concurrently with Alembic/model work (T014) because they touch disjoint directories.
3. Router implementation (T016) can run in parallel with queue service test hardening (T013) once manifest contract code stabilizes.
4. Final polish tasks (T019–T021) can be parallelized; they touch docs/logging/tests independently.

## Implementation Strategy

### MVP (User Story 1 only)

- Deliver queue allowlist + manifest normalization entry point so inline manifest jobs can be enqueued manually.
- Validate via queue service tests and minimal manual POST /api/queue/jobs submission.

### Incremental Extensions

1. Layer manifest contract module + tests to ensure deterministic payloads (User Story 2).
2. Add registry CRUD + `/runs` endpoints + tests (User Story 3).
3. Polish docs/logging/tests for operator readiness.

Each increment should be individually testable via `./tools/test_unit.sh` and, when applicable, manual HTTP calls scripted in quickstart.md.

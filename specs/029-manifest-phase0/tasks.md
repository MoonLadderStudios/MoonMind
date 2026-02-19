# Tasks: Manifest Queue Phase 0

**Input**: Design artifacts under `specs/029-manifest-phase0/` plus `docs/ManifestTaskSystem.md`
**Prerequisites**: Ensure `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` remain in sync after each change.
**Tests**: Tests are *mandatory* (runtime intent). Add or update unit tests only where noted, then execute `./tools/test_unit.sh`.
**Organization**: Tasks are grouped by user story so each increment is independently testable. Reference `DOC-REQ-*` IDs inside the relevant tasks for traceability.

## Format: `[ID] [P?] [Story] Description`
- Include `[P]` when the task can run in parallel without blocking dependencies.
- User story phases require `[US#]` labels; shared phases omit them.
- Mention exact file paths in every task description.

---

## Phase 1: Setup (Shared Infrastructure)
**Purpose**: Establish manifest-specific scaffolding called by every user story.

- [ ] T001 Register the `manifest` job type constant + allowlist entry in `moonmind/workflows/agent_queue/job_types.py` and expose it through `__all__` for API validation hooks (DOC-REQ-001).
- [ ] T002 Create the manifest contract scaffolding (`moonmind/workflows/agent_queue/manifest_contract.py`) with `ManifestContractError`, YAML loader stubs, capability maps, and option allowlists aligned to `docs/ManifestTaskSystem.md` (DOC-REQ-002, DOC-REQ-003).

---

## Phase 2: Foundational (Blocking Prerequisites)
**Purpose**: Core queue plumbing that all manifest user stories depend on.

- [ ] T003 Update `moonmind/workflows/agent_queue/service.py` so `AgentQueueService.create_job` detects `MANIFEST_JOB_TYPE`, routes payloads through the manifest contract normalizer, and persists `manifestHash`/`manifestVersion` within the job payload (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004).
- [ ] T004 Harden `moonmind/workflows/agent_queue/repositories.py::_is_job_claim_eligible` to enforce that workers must advertise every derived capability when `job.type == "manifest"`, rejecting tokens lacking the `manifest` flag (DOC-REQ-001, DOC-REQ-003).
- [ ] T005 Extend the FastAPI queue submission wiring (`moonmind/workflows/__init__.py` + `api_service/api/routers/agent_queue.py`) so manifest jobs reuse the shared service dependency and surface consistent validation errors (DOC-REQ-001).

---

## Phase 3: User Story 1 – Submit Manifest Job via Queue (Priority: P1) 🎯 MVP
**Goal**: Inline manifest submissions to `/api/queue/jobs` are validated, normalized, and persisted with derived capabilities so only manifest workers can execute them.
**Independent Test**: POST `/api/queue/jobs` with a valid `type="manifest"` payload and verify the stored job includes `manifestHash`, `manifestVersion`, and `requiredCapabilities`, while malformed manifests return HTTP 422 with manifest-specific errors.

### Tests for User Story 1 (write-first)
- [X] T006 [P] [US1] Add contract normalization tests covering name mismatch, unsupported adapters/options, and capability derivation inside `tests/unit/workflows/agent_queue/test_manifest_contract.py` (DOC-REQ-002, DOC-REQ-003).
- [X] T007 [P] [US1] Create queue service tests in `tests/unit/workflows/agent_queue/test_service_manifest.py` that ensure `AgentQueueService.create_job` rejects missing capabilities and persists manifest hashes/versions (DOC-REQ-001, DOC-REQ-004).

### Implementation for User Story 1
- [ ] T008 [US1] Implement `normalize_manifest_job_payload` + `derive_required_capabilities` in `moonmind/workflows/agent_queue/manifest_contract.py`, enforcing YAML parsing, metadata name matching, option allowlists, and capability derivation (DOC-REQ-002, DOC-REQ-003).
- [ ] T009 [US1] Update `moonmind/workflows/agent_queue/service.py` + associated serializers so manifest job creation ignores client-supplied capabilities, injects derived values, and stores `manifestHash`/`manifestVersion` for audit queries (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004).
- [ ] T010 [US1] Extend `api_service/api/routers/agent_queue.py` (and any Pydantic models under `api_service/api/schemas.py`) to accept `type="manifest"`, convert `ManifestContractError` into HTTP 422 responses, and guard against payloads that try to override capability data (DOC-REQ-001, DOC-REQ-003).

---

## Phase 4: User Story 2 – Manage Manifests in Registry (Priority: P2)
**Goal**: Platform operators can upsert manifests in a registry and trigger runs via `/api/manifests/{name}` endpoints while reusing the same normalization pipeline.
**Independent Test**: PUT a manifest via `/api/manifests/{name}`, GET it back to confirm matching `contentHash`, then POST `/api/manifests/{name}/runs` and verify the queued job mirrors inline submissions.

### Tests for User Story 2 (write-first)
- [ ] T011 [P] [US2] Expand `tests/unit/api/routers/test_manifests.py` to cover GET/PUT/POST flows, 404 handling, and content hash stability for registry-backed runs (DOC-REQ-005).
- [X] T012 [P] [US2] Add `tests/unit/services/test_manifests_service.py` coverage ensuring `ManifestsService` reuses the manifest contract, records hashes/versions, and invokes the queue service with `source.kind="registry"` payloads (DOC-REQ-004, DOC-REQ-005).

### Implementation for User Story 2
- [X] T013 [US2] Update the `ManifestRecord` ORM (`api_service/db/models.py` + Alembic migration if needed) to persist `content_hash`, `version`, and last-run telemetry per `specs/029-manifest-phase0/data-model.md` (DOC-REQ-004, DOC-REQ-005).
- [ ] T014 [US2] Implement registry CRUD + run submission logic in `api_service/services/manifests_service.py`, ensuring inline and registry flows both call `normalize_manifest_job_payload` before queue submission (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005).
- [ ] T015 [US2] Wire FastAPI routes + schemas (`api_service/api/routers/manifests.py`, `api_service/api/schemas.py`) to expose GET/PUT/POST endpoints with sanitized content hashes and option payloads, matching `contracts/manifest-phase0.openapi.yaml` (DOC-REQ-005).

---

## Phase 5: User Story 3 – Observe Manifest Jobs Separately (Priority: P3)
**Goal**: Queue administrators can list/filter manifest jobs and see sanitized metadata without exposing raw YAML while worker claim policies honor derived capabilities.
**Independent Test**: Call `/api/queue/jobs?type=manifest` and confirm results omit `manifest.source.content` yet include `manifestHash`, `manifestVersion`, and `requiredCapabilities`; attempt to claim a manifest job with a worker missing the `manifest` capability and ensure it is rejected.

### Tests for User Story 3 (write-first)
- [X] T016 [P] [US3] Add queue API serialization tests in `tests/unit/api/routers/test_agent_queue.py` that assert manifest payloads are sanitized and include audit metadata when listed (DOC-REQ-004).
- [X] T017 [P] [US3] Extend `tests/unit/workflows/agent_queue/test_repositories.py` to cover manifest-specific `type` filtering and worker capability enforcement paths (DOC-REQ-001, DOC-REQ-003).

### Implementation for User Story 3
- [ ] T018 [US3] Implement a `sanitize_manifest_payload` helper (shared between `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/agent_queue.py`) that strips inline YAML yet exposes hashes, versions, and capability labels (DOC-REQ-004).
- [ ] T019 [US3] Update queue listing/filter logic in `moonmind/workflows/agent_queue/repositories.py` and corresponding router query handling so `type=manifest` surfaces only manifest jobs with correct ordering + pagination (DOC-REQ-001, DOC-REQ-004).
- [ ] T020 [US3] Ensure dashboard/telemetry presenters (e.g., `api_service/api/routers/task_dashboard.py` or related view-models) label manifest jobs distinctly to keep admin UX consistent with the new job type (DOC-REQ-001).

---

## Phase 6: Polish & Cross-Cutting Concerns
**Purpose**: Finalize docs, contracts, and validation gates after all user stories ship.

- [ ] T021 [P] Refresh `specs/029-manifest-phase0/quickstart.md` with the new registry + queue smoke steps plus required `./tools/test_unit.sh` invocation notes (DOC-REQ-005, FR-010).
- [ ] T022 [P] Update `specs/029-manifest-phase0/contracts/manifest-phase0.openapi.yaml` so POST `/api/manifests/{name}/runs` and queue listing schemas document sanitized manifest metadata (DOC-REQ-004, DOC-REQ-005).
- [ ] T023 Run `./tools/test_unit.sh` from repo root to execute the manifest contract, registry, and queue suites, capturing logs for the runtime intent gate (DOC-REQ-001…005, FR-010).

---

## Dependencies & Execution Order
- Setup and Foundational phases must finish before any user story starts; they provide the manifest job type constants, normalization hooks, and capability gating shared by all work.
- User Story phases run sequentially by default (US1 ➜ US2 ➜ US3) but can overlap once their explicit dependencies complete; e.g., US2 can begin after T009 finishes, while US3 relies on both T009 and T014 to reuse the sanitized payload helper.
- Polish tasks require all user stories to be code-complete so docs/tests reflect final behavior.

## Parallel Execution Opportunities
- Tasks marked `[P]` touch independent files (e.g., contract tests vs. router tests) and can run concurrently after their prerequisites.
- Within each user story, model/service work (e.g., T008) can proceed in parallel with API/test updates (e.g., T007) once Foundational tasks land.
- Registry docs (T021) and OpenAPI updates (T022) may start as soon as US2 endpoints stabilize.

## Implementation Strategy
1. Finish Setup + Foundational work to get the manifest job type wired end-to-end without exposing any story-specific logic.
2. Deliver **MVP** by completing US1 (queue submission + normalization) and running targeted tests.
3. Layer on US2 for registry CRUD/runs so manifests can be reused without inline YAML.
4. Complete US3 to harden observability, sanitization, and filters needed by administrators.
5. Close with Polish tasks plus a full `./tools/test_unit.sh` run before handing off to the publish stage.

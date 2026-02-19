# Tasks: Manifest Task System Phase 0

**Input**: Design artifacts in `specs/031-manifest-phase0/` plus `docs/ManifestTaskSystem.md`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Always run `./tools/test_unit.sh` (never `pytest` directly) to validate runtime changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]** marks tasks that can proceed in parallel (no unmet dependencies, different files).
- **[Story]** labels appear only for user-story phases (`US1`, `US2`, `US3`).
- Every task cites an exact file path so implementers know where to work.

## Path Conventions

- Queue core: `moonmind/workflows/agent_queue/`
- Queue serialization/events: `moonmind/schemas/`
- API service: `api_service/api/routers/`, `api_service/services/`, `api_service/db/`
- Database migrations: `api_service/migrations/versions/`
- Tests: `tests/unit/**`
- Config toggles: `moonmind/config/settings.py`, `config.toml`
- Fixtures + docs: `tests/fixtures/`, `specs/031-manifest-phase0/quickstart.md`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create shared fixtures and configuration defaults that unblock all manifest phases.

- [X] T001 Build reusable manifest YAML fixtures (`tests/fixtures/manifests/phase0/inline.yaml`, `tests/fixtures/manifests/phase0/registry.yaml`) for queue + registry tests to consume the same payload samples.
- [X] T002 Encode Phase 0 manifest toggles (`spec_workflow.allow_manifest_path_source`, manifest capability flags) inside `moonmind/config/settings.py` and `config.toml` so runtime gating of `manifest.source.kind` matches docs/ManifestTaskSystem.md §6.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish queue-wide primitives required before touching any user story.

- [ ] T003 Introduce `MANIFEST_JOB_TYPE` + `SUPPORTED_QUEUE_JOB_TYPES` exports in `moonmind/workflows/agent_queue/job_types.py` and update queue modules to import the shared allowlist (DOC-REQ-001).
- [ ] T004 Implement `normalize_manifest_job_payload` with YAML parsing, action/sourcing enforcement, options allowlist, and manifest hash/version derivation inside `moonmind/workflows/agent_queue/manifest_contract.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-007).
- [ ] T005 Add `detect_manifest_secret_leaks` + `collect_manifest_secret_refs` helpers to `moonmind/workflows/agent_queue/manifest_contract.py` so normalized payloads emit `manifestSecretRefs` metadata while blocking raw secrets (DOC-REQ-004, DOC-REQ-008).

---

## Phase 3: User Story 1 – Submit Manifest Job via Queue (Priority: P1) 🎯 MVP

**Goal**: `/api/queue/jobs` accepts `type="manifest"` payloads, normalizes them through the manifest contract, persists derived metadata, and surfaces sanitized responses.

**Independent Test**: POST a manifest job to `/api/queue/jobs` with inline YAML; verify the persisted payload contains `requiredCapabilities`, `manifestHash`, `manifestVersion`, and `manifestSecretRefs`, and confirm `/api/queue/jobs?type=manifest` hides inline YAML while listing metadata.

### Tests for User Story 1 (write first)

- [ ] T006 [P] [US1] Extend `tests/unit/workflows/agent_queue/test_manifest_contract.py` with happy-path + failure cases covering action/source gating, option allowlists, capability derivation, and raw secret rejection (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007).
- [X] T007 [P] [US1] Add queue API coverage in `tests/unit/api/routers/test_agent_queue.py` to assert manifest submissions return sanitized payloads, expose `manifestSecretRefs`, and translate `ManifestContractError` into HTTP 4xx responses (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-008).

### Implementation for User Story 1

- [ ] T008 [US1] Update `moonmind/workflows/agent_queue/service.py::AgentQueueService.create_job` to route manifest payloads through `normalize_manifest_job_payload`, persist derived metadata, and log manifest job type audits (DOC-REQ-001, DOC-REQ-003).
- [ ] T009 [P] [US1] Expand `moonmind/workflows/agent_queue/models.py` (and `storage.py` if needed) so job payload rows store `manifestHash`, `manifestVersion`, `manifestSecretRefs`, and `requiredCapabilities` (DOC-REQ-003, DOC-REQ-008).
- [ ] T010 [P] [US1] Implement `sanitize_manifest_payload` serialization inside `moonmind/schemas/agent_queue_models.py` and SSE presenters so queue/event responses expose only hashes, versions, secret refs, and capabilities (DOC-REQ-001, DOC-REQ-003, DOC-REQ-008).
- [ ] T011 [P] [US1] Extend `api_service/api/routers/agent_queue.py` to accept `type="manifest"`, invoke the manifest contract via `AgentQueueService`, and map contract errors to FastAPI HTTPException payloads (DOC-REQ-001, DOC-REQ-002).

**Parallel Example (US1)**: Run T006 and T007 in parallel using the fixtures from T001 while T008 builds the service plumbing; once T008 lands, T009–T011 can proceed simultaneously because they touch distinct modules (models, schemas, router).

---

## Phase 4: User Story 2 – Manage Manifest Registry Entries (Priority: P2)

**Goal**: Provide registry CRUD + `/runs` bridging so operators can store YAML, retrieve metadata, and enqueue jobs referencing registry content.

**Independent Test**: `PUT /api/manifests/{name}` to upsert YAML, `GET /api/manifests/{name}` to retrieve sanitized metadata, and `POST /api/manifests/{name}/runs` to enqueue a job whose queue payload references the stored manifest and updates `last_run_*` fields.

### Tests for User Story 2 (write first)

- [ ] T012 [P] [US2] Expand `tests/unit/services/test_manifests_service.py` with cases for upsert validation, hash/version persistence, `/runs` bridge submissions, and secret rejection for inline payloads (DOC-REQ-004, DOC-REQ-005).
- [ ] T013 [P] [US2] Add REST coverage in `tests/unit/api/routers/test_manifests.py` for list/get/put/post flows including name mismatches, missing manifests, and sanitized responses (DOC-REQ-005).

### Implementation for User Story 2

- [ ] T014 [US2] Author/extend Alembic migration `api_service/migrations/versions/202602190003_manifest_registry_extensions.py` so `manifest` table stores `content_hash`, `version`, `last_run_*`, and `state_json/state_updated_at` columns (DOC-REQ-005).
- [ ] T015 [P] [US2] Update `api_service/db/models.py::ManifestRecord` plus Pydantic schemas in `api_service/api/schemas.py` to expose the new registry columns and defaults (DOC-REQ-005).
- [ ] T016 [US2] Implement `api_service/services/manifests_service.py` helpers (`upsert_manifest`, `submit_manifest_run`) that reuse `normalize_manifest_job_payload`, enforce secret policy, link queue job IDs, and update `last_run_*` metadata (DOC-REQ-004, DOC-REQ-005).
- [ ] T017 [P] [US2] Build `api_service/api/routers/manifests.py` handlers (list/get/put/post) that serialize sanitized registry payloads without inline YAML while surfacing hash/version/secret refs (DOC-REQ-005, DOC-REQ-008).

**Parallel Example (US2)**: After T014 finalizes schema changes, T015 (models) and T016 (service) can run concurrently; T013’s router tests can stub the service while T017 wires FastAPI endpoints.

---

## Phase 5: User Story 3 – Capability-Based Routing (Priority: P3)

**Goal**: Ensure manifest jobs publish deterministic capability chips and that worker claim logic honors those requirements so only manifest-capable workers process these jobs.

**Independent Test**: Submit manifests targeting different embeddings providers, vector stores, and data sources; verify persisted payloads expose the expected `requiredCapabilities`, queue listings show the chips, and `_is_job_claim_eligible` rejects workers missing any derived capability.

### Tests for User Story 3 (write first)

- [ ] T018 [P] [US3] Extend `tests/unit/workflows/agent_queue/test_repositories.py` to assert `_is_job_claim_eligible` blocks workers lacking `manifest`/store/source capabilities and passes when capabilities are supersets (DOC-REQ-003).
- [ ] T019 [P] [US3] Add SSE/listing coverage in `tests/unit/workflows/agent_queue/test_service_manifest.py` (or equivalent presenter test) to confirm queue responses show capability chips, hashes, and secret refs for manifest jobs (DOC-REQ-001, DOC-REQ-003, DOC-REQ-008).

### Implementation for User Story 3

- [ ] T020 [US3] Finalize capability mapping tables inside `moonmind/workflows/agent_queue/manifest_contract.py::derive_required_capabilities` for embeddings providers, vector stores, and each `dataSources[].type` (DOC-REQ-003).
- [ ] T021 [US3] Tighten `moonmind/workflows/agent_queue/repositories.py::_is_job_claim_eligible` so manifest jobs only dispatch to workers whose declared capabilities ⊇ derived capability chips (DOC-REQ-001, DOC-REQ-003).
- [ ] T022 [P] [US3] Surface `requiredCapabilities`, `manifestHash`, `manifestVersion`, and `manifestSecretRefs` through `moonmind/schemas/agent_queue_models.py`, `moonmind/workflows/agent_queue/service.py` event payloads, and `/api/queue/jobs?type=manifest` filtering (DOC-REQ-001, DOC-REQ-003, DOC-REQ-008).

**Parallel Example (US3)**: T020 establishes the mapping tables; once merged, T018–T019 can pin expected behavior while T021–T022 update repository gating and serializers in parallel since they touch different files.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T023 [P] Refresh `specs/031-manifest-phase0/quickstart.md` and `specs/031-manifest-phase0/contracts/requirements-traceability.md` with the new queue + registry flows and DOC-REQ-001…008 coverage notes.
- [ ] T024 Execute `./tools/test_unit.sh` and capture manifest-focused output, ensuring CI parity before MoonMind publish.

---

## Dependencies & Execution Order

1. **Setup (T001–T002)** → fixtures + config gating; prerequisite for all phases.
2. **Foundational (T003–T005)** → manifest job type + contract + secret policy; block every user story.
3. **User Story 1 (T006–T011)** → MVP queue submission; unlocks registry + routing work.
4. **User Story 2 (T012–T017)** → registry CRUD builds atop normalized payloads.
5. **User Story 3 (T018–T022)** → capability routing depends on payload metadata + registry schema.
6. **Polish (T023–T024)** → docs + verification only after runtime features stabilize.

Within each phase, tasks flagged `[P]` can run concurrently once their prerequisites finish.

## Implementation Strategy

1. Ship the **MVP** by finishing Phase 3 so manifest jobs can be submitted, stored, and observed safely.
2. Layer **registry CRUD** (Phase 4) immediately afterward to unlock deterministic re-runs (`PUT/GET/POST /api/manifests`).
3. Close with **capability routing + polish** (Phases 5–6) to enforce worker isolation, record documentation, and run the authoritative unit suite before handing off.

Delivering in this sequence ensures every checkpoint is independently testable and each user story can demonstrate value before moving forward.

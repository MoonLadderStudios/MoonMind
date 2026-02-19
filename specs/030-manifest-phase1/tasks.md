# Tasks: Manifest Task System Phase 1

**Input**: Design documents from `/specs/030-manifest-phase1/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Runtime guard + DOC-REQ coverage require automated tests for every production surface. Each validation task below references the `DOC-REQ-*` items it proves.

## Format Reminder

`- [ ] T### [P?] [Story?] Description (with file path)`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the skeleton packages, worker entrypoint, and test scaffolding needed before coding the engine.

- [ ] T001 Create `moonmind/manifest_v0/__init__.py`, `models.py`, `readers/`, `transforms/`, `embeddings_factory.py`, `vector_store_factory.py`, and `engine.py` scaffolding to host the new pipeline modules per **DOC-REQ-001**.
- [ ] T002 Wire `moonmind/agents/manifest_worker/__init__.py` + `cli.py` and register `moonmind-manifest-worker` in `pyproject.toml` console scripts so the worker can be launched via Poetry (**DOC-REQ-010**).
- [ ] T003 Scaffold dedicated test packages (`tests/unit/manifest_v0/`, `tests/unit/agents/`) with `__init__.py` + pytest fixtures to exercise engine + worker code paths.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared contracts, secret handling, and persistence utilities required by all user stories.

- [ ] T004 Implement `moonmind/manifest_v0/models.py` and `yaml_io.py` to load/validate ManifestV0 structures (version, metadata, source kinds) and enforce queue override rules per **DOC-REQ-001**/**DOC-REQ-013**.
- [ ] T005 Implement `moonmind/manifest_v0/readers/base.py` with the `ReaderAdapter` Protocol plus `SourceDocument`, `SourceChange`, and `PlanStats` dataclasses (**DOC-REQ-002**).
- [ ] T006 Extract shared secret resolution helpers into `moonmind/agents/secret_refs/base.py` and build `moonmind/manifest_v0/secret_refs.py` / `interpolate.py` to support env/profile/vault references (**DOC-REQ-012**/**DOC-REQ-013**).
- [ ] T007 Add `moonmind/manifest_v0/state_store.py` plus API service hooks in `api_service/services/manifests_service.py` to read/write `ManifestRecord.state_json` (**DOC-REQ-009**).
- [ ] T008 Extend Alembic migrations + `api_service/api/routers/manifests.py` to expose `POST /api/manifests/{name}/state` for checkpoint updates and expose state metadata in `GET /api/manifests/{name}` (**DOC-REQ-009**).

---

## Phase 3: User Story 1 - Execute Declarative Manifest Runs End-to-End (Priority: P1) 🎯 MVP

**Goal**: Run a manifest job that validates YAML, fetches sources, transforms/chunks, embeds, and upserts/deletes data in Qdrant with deterministic artifacts.

**Independent Test**: Submit a manifest via `/api/manifests/{name}/runs` and verify the worker emits every stage, uploads plan/run artifacts, and updates Qdrant with stable point IDs.

### Implementation

- [ ] T009 [US1] Implement deterministic transform pipeline (`moonmind/manifest_v0/transforms/html.py`, `splitter.py`, `metadata.py`) covering HTML stripping, TokenTextSplitter, and metadata enrichment per **DOC-REQ-004**/**DOC-REQ-007**.
- [ ] T010 [P] [US1] Build `moonmind/manifest_v0/embeddings_factory.py` with provider adapters (OpenAI, Google, Ollama) enforcing dimension compatibility and batching instrumentation (**DOC-REQ-005**).
- [ ] T011 [P] [US1] Build `moonmind/manifest_v0/vector_store_factory.py` + Qdrant connection validation for inline/registry/path manifests (respecting `allowCreateCollection`, `dryRun`, `forceFull`, `maxDocs`) per **DOC-REQ-006**/**DOC-REQ-013**.
- [ ] T012 [US1] Implement point-id + metadata enforcement in `moonmind/manifest_v0/id_policy.py` and chunk assembly to include manifest/dataSource/doc identifiers per **DOC-REQ-007**/**DOC-REQ-008**.
- [ ] T013 [P] [US1] Implement `GithubRepositoryReaderAdapter` in `moonmind/manifest_v0/readers/github.py` (repo cloning, change detection, capability token) per **DOC-REQ-003**.
- [ ] T014 [P] [US1] Implement `GoogleDriveReaderAdapter` in `moonmind/manifest_v0/readers/google_drive.py` with incremental listing + capability labeling (**DOC-REQ-003**).
- [ ] T015 [P] [US1] Implement `ConfluenceReaderAdapter` in `moonmind/manifest_v0/readers/confluence.py` with HTML export + change detection (**DOC-REQ-003**).
- [ ] T016 [P] [US1] Implement `SimpleDirectoryReaderAdapter` in `moonmind/manifest_v0/readers/simple_directory.py` for local file ingestion (**DOC-REQ-003**).
- [ ] T017 [US1] Compose `moonmind/manifest_v0/engine.py` to orchestrate plan/run flows (validate → fetch → transform → embed → upsert/delete) and compute `reports/*.json` per **DOC-REQ-001**/**DOC-REQ-004**/**DOC-REQ-008**.
- [ ] T018 [US1] Implement `moonmind/agents/manifest_worker/worker.py` to poll `type="manifest"` jobs, perform preflight checks, stream stage transitions, and invoke the engine (**DOC-REQ-010**).

### Validation

- [ ] T019 [US1] Add engine + factory unit tests (`tests/unit/manifest_v0/test_engine_plan_run.py`) covering transforms, embeddings, vector store enforcement, metadata allowlists, and point-id determinism (**DOC-REQ-001/004/005/006/007/008**).
- [ ] T020 [US1] Add adapter-focused tests (`tests/unit/manifest_v0/readers/test_adapters.py`) mocking GitHub/Drive/Confluence/Directory APIs to prove capability derivation and change emission (**DOC-REQ-002/003**).
- [ ] T021 [US1] Add worker loop tests (`tests/unit/agents/test_manifest_worker_job_loop.py`) ensuring job claim, preflight failures, and engine invocation satisfy **DOC-REQ-010**/**DOC-REQ-013**.

---

## Phase 4: User Story 2 - Incremental Sync with Checkpoints (Priority: P2)

**Goal**: Make reruns skip unchanged documents, delete missing ones, and persist checkpoint state through the registry.

**Independent Test**: Run the same manifest twice with one document changed and one removed; verify only changed content re-embeds, deletions happen, and `state_json` updates.

### Implementation

- [ ] T022 [US2] Integrate `moonmind/manifest_v0/state_store.py` with the engine + worker so checkpoints load before runs and POST back via the manifests service (**DOC-REQ-009**).
- [ ] T023 [US2] Implement delete-before-upsert + `forceFull` logic inside `moonmind/manifest_v0/engine.py` and Qdrant helpers to ensure deterministic upserts/deletions (**DOC-REQ-008**).
- [ ] T024 [US2] Extend `api_service/api/routers/manifests.py` + `moonmind/schemas/agent_queue_models.py` to expose checkpoint metadata (state timestamps, hash summaries) to operators (**DOC-REQ-009**).

### Validation

- [ ] T025 [US2] Add checkpoint unit tests (`tests/unit/manifest_v0/test_checkpointing.py`) covering hash skip logic, delete detection, and registry writes (**DOC-REQ-008/009**).
- [ ] T026 [US2] Add API/service tests (`tests/unit/api/routers/test_manifests_checkpoint.py`) ensuring checkpoint endpoints persist state_json and surface metadata (**DOC-REQ-009**).

---

## Phase 5: User Story 3 - Observe and Govern Manifest Runs (Priority: P3)

**Goal**: Provide operators full visibility into manifest runs (stage events, artifacts, cancellations) with secret-safe logs.

**Independent Test**: Stream events for a manifest job, issue a cancellation mid-run, and confirm ordered stage events, artifact updates, and acknowledgement without leaking secrets.

### Implementation

- [ ] T027 [US3] Implement stage event emitter + SSE payload builders in `moonmind/agents/manifest_worker/handlers.py` and update `moonmind/schemas/agent_queue_models.py` to support `moonmind.manifest.*` events (**DOC-REQ-011**).
- [ ] T028 [US3] Implement artifact upload + manifest redaction pipeline (`manifest/input.yaml`, `manifest/resolved.yaml`, `reports/*.json`) inside the worker handlers (**DOC-REQ-011**/**DOC-REQ-012**).
- [ ] T029 [US3] Implement cancellation acknowledgement + safe-stop logic in `moonmind/agents/manifest_worker/worker.py` (finish batch, upload partial summaries, call cancel ACK) per **DOC-REQ-012**.
- [ ] T030 [US3] Update `api_service/api/routers/agent_queue.py` + `moonmind/schemas/agent_queue_models.py` to list manifest artifacts/metrics in job detail responses (**DOC-REQ-011**).

### Validation

- [ ] T031 [US3] Add worker observability tests (`tests/unit/agents/test_manifest_worker_events.py`) verifying stage events/artifact manifests/cancellation statuses and ensuring secrets remain redacted (**DOC-REQ-011/012**).
- [ ] T032 [US3] Add SSE + artifact API tests (`tests/unit/api/routers/test_queue_manifest_events.py`) ensuring job detail endpoints expose ordered events + artifact metadata (**DOC-REQ-011**).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, documentation, and test runs spanning all user stories.

- [ ] T033 Execute the quickstart scenario from `specs/030-manifest-phase1/quickstart.md` against a demo manifest to validate inline/registry source kinds and queue overrides per **DOC-REQ-013**.
- [ ] T034 Run `./tools/test_unit.sh` and capture results for manifest_v0 + worker suites (proves runtime intent + aggregate DOC-REQ coverage).
- [ ] T035 Update `docs/ManifestTaskSystem.md` Phase 1 status + add manifest worker env vars (if gaps discovered during implementation) to keep the contract accurate.

---

## Dependencies & Execution Order

1. **Setup (T001–T003)** → establish package + worker scaffolding.
2. **Foundational (T004–T008)** → complete shared contracts, secret utilities, and checkpoint plumbing before any user story can start.
3. **User Story 1 (T009–T021)** → delivers the MVP ingestion pipeline once foundations are ready.
4. **User Story 2 (T022–T026)** → adds incremental sync + checkpoint visibility on top of US1.
5. **User Story 3 (T027–T032)** → layers observability + cancellation guarantees after US1/US2 exist.
6. **Polish (T033–T035)** → final validation + documentation once all stories meet acceptance criteria.

**Blocking relationships**:
- T004 depends on T001; T005–T008 depend on Setup completion.
- T009–T018 depend on Foundational tasks (adapters rely on models + secret refs).
- T022 depends on checkpoint scaffolding from T007/T008 plus engine work from T017.
- T027–T030 depend on worker loop (T018) and API schema updates (T024).

## Parallel Opportunities

- Tasks marked `[P]` touch independent files (e.g., adapters T013–T016, factories T010–T011) and can be worked on simultaneously once prerequisites finish.
- Separate developers can own User Stories 2 and 3 in parallel after User Story 1 stabilizes, because checkpoints + observability touch disjoint modules.
- Validation tasks (T019–T021, T025–T026, T031–T032, T034) can run concurrently with late-stage polish once their corresponding implementation tasks land.

## Implementation Strategy

### MVP First (User Story 1 Only)
1. Complete Setup + Foundational phases (T001–T008).
2. Deliver US1 implementation + validation tasks (T009–T021) so at least one manifest can run end-to-end.
3. Validate via quick smoke (subset of quickstart) before touching incremental/observability work.

### Incremental Delivery
1. Ship US1 (ingestion engine + worker) → demo ingestion of a single manifest.
2. Add US2 (checkpoint + incremental sync) → prove reruns skip unchanged docs.
3. Add US3 (observability/cancellation) → integrate dashboards + cancellation semantics.
4. Perform Polish tasks → run all tests and document operations.

### Parallel Team Strategy
- Developer A: Engine/factories (T009–T017).
- Developer B: Worker loop + observability (T018, T027–T032).
- Developer C: Checkpoint persistence + API surfaces (T022–T026).
- Shared timebox for validation + polish (T019–T021, T025–T026, T031–T035).

## Notes

- Every task lists explicit file paths to minimize ambiguity.
- Each `DOC-REQ-*` is referenced in at least one implementation and one validation task for traceability.
- Tests should be written to fail before implementation whenever feasible, then rerun in T034.

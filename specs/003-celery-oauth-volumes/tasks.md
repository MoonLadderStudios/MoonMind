# Tasks: Celery OAuth Volume Mounts

**Input**: Design documents from `/specs/001-celery-oauth-volumes/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are included where they add confidence for Codex workflow changes (pre-flight gating, queue routing, API contracts).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare shared configuration artifacts so all environments expose Codex queue and volume settings.

- [x] T001 Add CODEX shard and login placeholders to `.env-template`
- [x] T002 [P] Mirror CODEX shard and login placeholders in `.env.vllm-template`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extend configuration, persistence, and serialization layers so later stories can read/write Codex shard metadata.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T003 Expand Spec Kit settings for CODEX shard configuration in `moonmind/config/settings.py`
- [x] T004 Add CodexAuthVolume/CodexWorkerShard models and new run metadata fields in `moonmind/workflows/speckit_celery/models.py`
- [x] T005 Create Alembic migration adding Codex shard tables and run columns in `api_service/migrations/versions/*_codex_shards.py`
- [x] T006 Update Spec workflow repositories to persist shard and pre-flight fields in `moonmind/workflows/speckit_celery/repositories.py`
- [x] T007 Surface Codex shard metadata in API serializers and schemas via `moonmind/workflows/speckit_celery/serializers.py` and `moonmind/schemas/workflow_models.py`

**Checkpoint**: Foundation ready—Codex shard metadata can be stored and returned end-to-end.

---

## Phase 3: User Story 1 - Reliable Codex Authentication (Priority: P1) 🎯 MVP

**Goal**: Ensure every Codex submission mounts a persistent auth volume and fails fast with actionable messaging if login has expired.

**Independent Test**: Trigger a Spec run hitting the Codex phase and confirm it mounts the worker volume, runs `codex login status`, and records pre-flight results without prompting for interactive login.

### Implementation for User Story 1

- [x] T008 [P] [US1] Mount worker-specific Codex auth volume when starting job containers in `moonmind/workflows/speckit_celery/job_container.py`
- [x] T009 [P] [US1] Implement Codex pre-flight login helper using Docker mounts in `moonmind/workflows/speckit_celery/tasks.py`
- [x] T010 [US1] Gate Codex submission on pre-flight success and persist status/message in `moonmind/workflows/speckit_celery/tasks.py`
- [x] T011 [US1] Record mounted volume metadata on the workflow context in `moonmind/workflows/speckit_celery/orchestrator.py`
- [x] T012 [P] [US1] Add unit coverage for volume mount and pre-flight failure handling in `tests/unit/workflows/test_tasks.py`

**Checkpoint**: Codex phases reuse persistent auth, and failures halt before execution with clear remediation instructions.

---

## Phase 4: User Story 2 - Sharded Worker Routing (Priority: P2)

**Goal**: Route Codex work deterministically across three dedicated queues while keeping non-Codex tasks on the default queue.

**Independent Test**: Enqueue Codex tasks for different repositories and verify each hashes to a stable `codex-{n}` queue; enqueue a non-Codex task and confirm it remains on the default queue.

### Implementation for User Story 2

- [ ] T013 [P] [US2] Add Celery routing helper for codex shard queues in `moonmind/workflows/speckit_celery/celeryconfig.py`
- [ ] T014 [US2] Wire Celery app to new shard queues and routing in `moonmind/workflows/speckit_celery/__init__.py`
- [ ] T015 [US2] Compute shard affinity keys and attach queue metadata when scheduling Codex jobs in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T016 [US2] Define codex worker services and volumes in `docker-compose.yaml` and `docker-compose.job.yaml`
- [ ] T017 [P] [US2] Emit queue and volume diagnostics for each Codex run in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T018 [P] [US2] Add deterministic routing tests covering shard selection in `tests/unit/workflows/test_tasks.py`

**Checkpoint**: Codex tasks fan out across dedicated queues with auditable routing data while other work stays unaffected.

---

## Phase 5: User Story 3 - Credential Stewardship (Priority: P3)

**Goal**: Provide operators with introspection and remediation tooling (API + docs) to maintain Codex auth volumes without container access.

**Independent Test**: List shard health via the API, trigger a targeted pre-flight refresh for a run, and follow the runbook to reauthenticate a failing volume.

### Implementation for User Story 3

- [ ] T019 [P] [US3] Implement shard and volume query helpers in `moonmind/workflows/speckit_celery/repositories.py`
- [ ] T020 [US3] Expose `/api/workflows/speckit/codex/shards` endpoint in `api_service/api/routers/workflows.py`
- [ ] T021 [US3] Expose `/api/workflows/speckit/runs/{run_id}/codex/preflight` endpoint in `api_service/api/routers/workflows.py`
- [ ] T022 [P] [US3] Update operator documentation and quickstart instructions in `docs/SpecKitAutomation.md` and `specs/001-celery-oauth-volumes/quickstart.md`
- [ ] T023 [P] [US3] Add contract coverage for shard and pre-flight endpoints in `tests/contract/test_workflow_api.py`
- [ ] T024 [US3] Add repository/API unit coverage for shard health responses in `tests/unit/workflows/test_tasks.py`

**Checkpoint**: Operators can inspect shard health, trigger remediation, and follow a documented runbook without shelling into containers.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize operational guidance and validate new workflows end-to-end.

- [ ] T025 [P] Document new Codex routing metrics and logging expectations in `docs/ops-runbook.md`
- [ ] T026 Execute targeted regression tests for updated workflows (`tests/unit/workflows/test_tasks.py`, `tests/contract/test_workflow_api.py`) and capture outcomes in release notes

---

## Dependencies & Execution Order

### Phase Dependencies

1. **Setup (Phase 1)** → 2. **Foundational (Phase 2)** → 3. **User Story 1 (Phase 3)** → 4. **User Story 2 (Phase 4)** → 5. **User Story 3 (Phase 5)** → 6. **Polish (Phase 6)**
2. Phase 4 and Phase 5 both depend on Phase 3 completing Codex metadata wiring.
3. Polish tasks start only after all targeted user stories are complete.

### User Story Dependencies

- **US1** depends on Foundational infrastructure to persist metadata.
- **US2** depends on US1 metadata (queue/volume fields) and Foundational configuration.
- **US3** depends on US1 pre-flight fields (status/message) and US2 deterministic routing to surface shard mappings.

### Within-Story Ordering

- US1: Implement mount (T008) → pre-flight helper (T009) → enforce gating (T010) → record metadata (T011) → tests (T012).
- US2: Add routing helper (T013) → hook Celery config (T014) → compute affinity/logging (T015, T017) → compose updates (T016) → tests (T018).
- US3: Repository helpers (T019) → API endpoints (T020, T021) → docs/tests (T022–T024).

## Parallel Execution Examples

### User Story 1

```bash
# In parallel
poetry run pytest tests/unit/workflows/test_tasks.py::test_codex_preflight_gates  # from T012
python -m build_tools.apply_patch moonmind/workflows/speckit_celery/job_container.py  # from T008
```

### User Story 2

```bash
# In parallel
poetry run pytest tests/unit/workflows/test_tasks.py::test_codex_queue_shard_mapping  # from T018
python scripts/update_compose.py docker-compose.yaml  # from T016
```

### User Story 3

```bash
# In parallel
poetry run pytest tests/contract/test_workflow_api.py::test_codex_shard_endpoints  # from T023
code docs/SpecKitAutomation.md  # from T022
```

## Implementation Strategy (MVP First)

- Deliver MVP by finishing User Story 1 (Phase 3) so Codex runs stop failing for authentication and pre-flight errors are actionable.
- Layer deterministic routing (User Story 2) once MVP stabilizes to improve scalability without risking auth regressions.
- Conclude with stewardship tooling (User Story 3) and polish to arm operators with observability and documentation.

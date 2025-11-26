# Tasks: Celery Chain Workflow Integration

**Input**: Design documents from `/specs/001-celery-chain-workflow/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Automated tests are scoped where they directly verify Celery orchestration and API contracts referenced in the spec.

**Organization**: Tasks are grouped by user story so each increment is independently testable.

## Format: `[ID] [P?] [Story] Description`
- `[P]` indicates the task can run in parallel with others in the same phase.
- `[US#]` labels tasks that belong to a specific user story.
- All tasks cite concrete file paths derived from the implementation plan.

---

## Phase 1: Setup (Shared Infrastructure)
**Purpose**: Ensure configuration scaffolding exists before touching schema or workflow code.

- [x] T001 [P] Add Spec workflow queue + artifact defaults to `.env-template`, `.env.vllm-template`, and `config.toml` so operators can set `SPEC_WORKFLOW_CODEX_QUEUE`, `SPEC_WORKFLOW_ARTIFACT_ROOT`, and Celery broker URLs.
- [x] T002 Wire the new settings into `moonmind/config/settings.py` and `api_service/config.template.toml` so API services and workers can read queue + artifact paths.
- [x] T003 [P] Update `docker-compose.yaml` and `docker-compose.job.yaml` Celery worker definitions to bind the `codex` queue and mount `var/artifacts/spec_workflows` for Codex log persistence.

---

## Phase 2: Foundational (Blocking Prerequisites)
**Purpose**: Create database entities, repositories, and Celery configuration required by every story. ⚠️ Do not start story work until these tasks finish.

- [x] T004 Create Alembic migration `api_service/migrations/versions/<timestamp>_spec_workflow_chain.py` adding `spec_workflow_runs`, `spec_workflow_task_states`, `workflow_credential_audits`, and `workflow_artifacts` per `data-model.md`.
- [x] T005 Update ORM models in `api_service/db/models.py` so SQLAlchemy knows about the new tables, FKs, and relationships.
- [x] T006 Implement persistence helpers in `moonmind/workflows/speckit_celery/repositories.py` to insert/update runs, task states, artifacts, and credential audits transactionally.
- [x] T007 Expand Pydantic schemas in `moonmind/schemas/workflow_models.py` and serializers in `moonmind/workflows/speckit_celery/serializers.py` to match the contract outputs.
- [x] T008 Add artifact storage utilities in `moonmind/workflows/speckit_celery/storage.py` (new file) that normalize `var/artifacts/spec_workflows/<run_id>` paths, file metadata, and digests.
- [x] T009 Configure the single `codex` queue plus QoS defaults inside `moonmind/workflows/speckit_celery/celeryconfig.py` and ensure `celery_worker/speckit_worker.py` consumes those settings on startup.

**Checkpoint**: Database + serialization + Celery plumbing ready for user stories.

---

## Phase 3: User Story 1 – Trigger Next Spec Phase (Priority: P1) 🎯 MVP
**Goal**: Operators run “next Spec Kit phase” and a Celery chain executes discovery → submit → apply → publish while persisting artifacts and Codex context.
**Independent Test**: POST `/api/workflows/speckit/runs` against a staging repo and verify a branch/PR plus Codex logs are created without manual steps.

### Tests (write before implementation)
- [x] T010 [P] [US1] Add Celery-chain happy-path coverage to `tests/unit/workflows/test_tasks.py`, asserting discovery/submit/apply/publish states populate the DB.
- [x] T011 [P] [US1] Add contract coverage for `POST /api/workflows/speckit/runs` in `tests/contract/test_workflow_api.py`, checking 202 payload shape and idempotent branch names.

### Implementation
- [x] T012 [US1] Build the Celery chain/orchestrator in `moonmind/workflows/speckit_celery/__init__.py`, composing immutable signatures and saving `AsyncResult` IDs onto `SpecWorkflowRun`.
- [x] T013 [US1] Implement discovery, submit, apply/poll, publish, and finalize tasks with artifact writes inside `moonmind/workflows/speckit_celery/tasks.py`.
- [x] T014 [US1] Create Codex/GitHub helper functions in `moonmind/workflows/speckit_celery/services.py` to enforce idempotent branch naming, push commits, and capture JSONL log paths.
- [x] T015 [US1] Wire `POST /api/workflows/speckit/runs` inside `api_service/api/routers/workflows.py` to validate input, create `SpecWorkflowRun`, and enqueue the chain.
- [x] T016 [US1] Update worker bootstrap scripts (`celery_worker/speckit_worker.py` and `celery_worker/scripts/codex_login_proxy.py`) to run Codex preflight checks and mount the configured auth volume before tasks start.

**Checkpoint**: Triggering a run now creates branches/PRs and stores Codex artifacts automatically.

---

## Phase 4: User Story 2 – Monitor Workflow Progress (Priority: P2)
**Goal**: Operators view real-time run status, task-by-task state, and artifact metadata via API responses.
**Independent Test**: While a run executes, poll `GET /api/workflows/speckit/runs/{id}` and `/runs/{id}/tasks` to confirm timestamps, payloads, and artifacts update; induce a failure to verify actionable messages.

### Tests
- [x] T017 [P] [US2] Extend `tests/unit/workflows/test_tasks.py` to confirm each Celery state change writes `SpecWorkflowTaskState` with timestamps + artifact references.
- [x] T018 [P] [US2] Add contract tests for `GET /api/workflows/speckit/runs`, `/runs/{id}`, `/runs/{id}/tasks`, and `/runs/{id}/artifacts` in `tests/contract/test_workflow_api.py`.

### Implementation
- [x] T019 [US2] Implement run listing/filtering + task-state fetchers in `moonmind/workflows/speckit_celery/repositories.py`, including cursor pagination and error handling.
- [x] T020 [US2] Enhance `moonmind/workflows/speckit_celery/serializers.py` to embed task states, credential audit info, and artifacts per the OpenAPI schema.
- [x] T021 [US2] Add GET handlers for runs, run detail, task states, and artifacts inside `api_service/api/routers/workflows.py`, enforcing auth + query validation.
- [x] T022 [US2] Update `moonmind/workflows/speckit_celery/tasks.py` to emit structured status snapshots (state, timestamps, payload refs) into `SpecWorkflowTaskState`.
- [x] T023 [US2] Document monitoring workflows (API polling + expected logs) in `specs/001-celery-chain-workflow/quickstart.md` and `docs/SpecKitAutomation.md`.

**Checkpoint**: Operators can rely on the API to observe progress and download artifacts.

---

## Phase 5: User Story 3 – Retry or Resume Failed Chains (Priority: P3)
**Goal**: Resume failed chains from the blocking task (when safe) or restart cleanly after fixing credentials, preserving artifacts.
**Independent Test**: Force a failure at publish, POST `/api/workflows/speckit/runs/{id}/retry`, and confirm the chain resumes using stored diff context; retry again without fixing credentials to ensure fast failure with guidance.

### Tests
- [x] T024 [P] [US3] Add retry-state coverage in `tests/unit/workflows/test_tasks.py`, asserting transitions `failed → retrying → {running|failed|succeeded}` and artifact reuse.
- [x] T025 [P] [US3] Add contract tests for `POST /api/workflows/speckit/runs/{id}/retry` in `tests/contract/test_workflow_api.py`, covering successful resumes and credential errors.

### Implementation
- [x] T026 [US3] Implement retry orchestration helpers (`retry_spec_workflow_run`, guard rails) in `moonmind/workflows/__init__.py` to locate failing task outputs and enqueue resumes.
- [x] T027 [US3] Update `moonmind/workflows/speckit_celery/tasks.py` to accept resume tokens, skip completed tasks, and reload artifacts/logs as inputs.
- [x] T028 [US3] Add the retry endpoint + validation to `api_service/api/routers/workflows.py`, surfacing guidance when retries are unsafe.
- [x] T029 [US3] Persist retry metadata (attempt counters, operator notes, extra artifacts) in `moonmind/workflows/speckit_celery/repositories.py` and `moonmind/workflows/speckit_celery/storage.py`.

**Checkpoint**: Failed runs can be resumed safely with complete audit trails.

---

## Phase 6: Polish & Cross-Cutting Concerns
**Purpose**: Hardening, documentation, and observability after core stories ship.

- [x] T030 [P] Refresh operator runbooks in `docs/ops-runbook.md` and `docs/SpecKitAutomationInstructions.md` with queue setup, retry procedures, and artifact locations.
- [x] T031 Add structured logging + StatsD hooks for each Celery phase in `moonmind/config/logging.py` and `moonmind/workflows/speckit_celery/tasks.py`.
- [x] T032 Validate the end-to-end flow via `specs/001-celery-chain-workflow/quickstart.md`, capturing sample run IDs/artifacts for documentation.

---

## Dependencies & Execution Order
- Setup (Phase 1) → Foundational (Phase 2) → User Stories (Phases 3–5) → Polish (Phase 6).
- US1 is the MVP and must complete before US2/US3 finalize; US2 depends on US1 data emissions, while US3 depends on US1 artifacts and US2 repository helpers.
- Tasks marked `[P]` can run concurrently; all others require prior tasks in the same phase to finish.

## Parallel Execution Examples
- **US1**: T010 and T011 can run while T012 starts; once T012 completes, T013–T015 proceed sequentially and T016 can run after T015 stabilizes.
- **US2**: T017/T018 execute in parallel; T019 + T022 can proceed simultaneously with T020 + T021 once repository schema is stable; T023 trails after endpoints stabilize.
- **US3**: T024/T025 can run together, T026/T027 handle orchestration while T028/T029 address API + storage updates.
- **Setup/Foundation**: T001 and T003 are independent; T004 (migration) and T009 (Celery config) can overlap once settings from T002 are in place.

## Independent Test Criteria
- **US1**: POST `/api/workflows/speckit/runs` with a staged Spec repo; verify Celery completes discovery→publish, emits Codex task IDs, and stores JSONL logs + PR URL.
- **US2**: Trigger a run, poll `GET /api/workflows/speckit/runs` and `/runs/{id}/tasks` to confirm timestamps/messages update; fetch `/runs/{id}/artifacts` to download logs.
- **US3**: Force a publish failure, call `/api/workflows/speckit/runs/{id}/retry`, ensure the chain resumes from publish using stored artifacts; retry again without fixing credentials to confirm fast failure + guidance.

## Implementation Strategy (MVP First)
1. Deliver US1 (Phase 3) for MVP—operators get one-click Codex workflow to PR.
2. Layer US2 observability so operators trust automation and can self-serve debugging.
3. Add US3 retry/resume to reduce operational toil and satisfy FR-009.
4. Polish with documentation, logging, and quickstart validation.

## Task Summary & Validation
- Total tasks: **32**
- Per-story counts: **US1 – 7**, **US2 – 7**, **US3 – 6** (remaining tasks cover setup, foundational, and polish work)
- Parallel opportunities: Highlighted above (e.g., T001/T003, T010/T011, T017/T018, T024/T025)
- Independent test criteria: Listed per story and mirror spec expectations
- Suggested MVP scope: Complete through Phase 3 (US1)
- Checklist compliance: Every task uses `- [ ] T### [P?] [US?] Description with file path`

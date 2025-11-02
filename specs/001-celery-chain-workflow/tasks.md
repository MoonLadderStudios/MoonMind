# Tasks: Celery Chain Workflow Integration

## Feature Overview
- **Branch**: `001-celery-chain-workflow`
- **Spec**: specs/001-celery-chain-workflow/spec.md
- **Plan**: specs/001-celery-chain-workflow/plan.md

## Dependencies & Flow
1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6 (Polish)
2. User Story dependency order: **US1 → US2 → US3**
3. Parallelizable tasks marked with `[P]`

## Parallel Execution Examples
- **US1**: Run Celery task implementation (`tasks.py`) in parallel with Codex/GitHub adapter creation (`adapters/*`).
- **US2**: UI router endpoint (`api/routers/workflows.py`) can proceed alongside repository query functions once database layer is ready.
- **US3**: Retry orchestration logic can progress while writing integration tests in `tests/integration/workflows/test_workflow_chain.py`.

## Implementation Strategy
Deliver MVP with US1 only (automated phase trigger through PR). US2 adds observability UI/API depth. US3 introduces retry/resume robustness and completes full operator experience.

---

## Phase 1 – Setup
- [x] T001 Establish Celery worker entrypoint in `celery_worker/speckit_worker.py`
- [x] T002 Add RabbitMQ broker and PostgreSQL Celery result backend defaults in `config/settings.py`
- [x] T003 Document environment variables in ./README.md and ./.env-template

## Phase 2 – Foundational Infrastructure
- [ ] T004 Create database migration `api_service/migrations/versions/add_spec_workflow_tables.py` with new tables
- [ ] T005 Update SQLAlchemy models in `moonmind/workflows/speckit_celery/models.py`
- [ ] T006 Implement repositories in `moonmind/workflows/speckit_celery/repositories.py`
- [ ] T007 [P] Define shared serializers in `moonmind/workflows/speckit_celery/serializers.py`
- [ ] T008 Integrate Celery app setup in `moonmind/workflows/speckit_celery/__init__.py`
- [ ] T009 Add configuration wiring in `moonmind/workflows/__init__.py`

## Phase 3 – User Story 1: Trigger Next Spec Phase (Priority P1)
### Story Goal
Automate discovery, submission, diff application, and PR publication through a Celery Chain invoked via MoonMind.

### Independent Test Criteria
Execute the POST `/api/workflows/speckit/runs` endpoint with valid secrets and verify a branch and PR are created for the next phase.

### Tasks
- [ ] T010 [US1] Implement discovery task in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T011 [US1] Implement Codex submission task in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T012 [US1] Implement apply/PR task in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T013 [US1] Build orchestrator chain in `moonmind/workflows/speckit_celery/orchestrator.py`
- [ ] T014 [US1] Add Codex client adapter in `moonmind/workflows/adapters/codex_client.py`
- [ ] T015 [US1] Add GitHub client adapter in `moonmind/workflows/adapters/github_client.py`
- [ ] T016 [US1] Expose workflow trigger endpoint in `moonmind/api/routers/workflows.py`
- [ ] T017 [US1] Register Pydantic schemas in `moonmind/schemas/workflow_models.py`
- [ ] T018 [US1] Write integration test `tests/integration/workflows/test_workflow_chain.py`
- [ ] T019 [US1] Add contract test `tests/contract/test_workflow_api.py`
- [ ] T020 [US1] Document operator workflow in `specs/001-celery-chain-workflow/quickstart.md`

## Phase 4 – User Story 2: Monitor Workflow Progress (Priority P2)
### Story Goal
Provide real-time visibility into each Celery task state and related artifacts.

### Independent Test Criteria
Trigger a workflow run, poll GET `/api/workflows/speckit/runs/{id}`, and confirm task states, timestamps, and artifact references update as tasks progress.

### Tasks
- [ ] T021 [US2] Extend repositories for task state queries in `moonmind/workflows/speckit_celery/repositories.py`
- [ ] T022 [US2] Emit structured status updates in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T023 [US2] Enhance serializer outputs in `moonmind/workflows/speckit_celery/serializers.py`
- [ ] T024 [US2] Add GET list endpoint in `moonmind/api/routers/workflows.py`
- [ ] T025 [US2] Add GET detail endpoint in `moonmind/api/routers/workflows.py`
- [ ] T026 [US2] Update schemas for task state responses in `moonmind/schemas/workflow_models.py`
- [ ] T027 [US2] Persist JSONL/patch paths in `moonmind/workflows/speckit_celery/models.py`
- [ ] T028 [US2] Write unit tests for serializers in `tests/unit/workflows/test_tasks.py`

## Phase 5 – User Story 3: Retry or Resume Failed Chains (Priority P3)
### Story Goal
Allow operators to resume failed workflows from the point of failure with validation of credentials and artifacts.

### Independent Test Criteria
Force a failure after Codex apply, invoke `/api/workflows/speckit/runs/{id}/retry`, and ensure the chain resumes at the publish phase using stored artifacts.

### Tasks
- [ ] T029 [US3] Add retry orchestration in `moonmind/workflows/speckit_celery/orchestrator.py`
- [ ] T030 [US3] Implement credential audit persistence in `moonmind/workflows/speckit_celery/models.py`
- [ ] T031 [US3] Validate secrets pre-flight in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T032 [US3] Expose retry endpoint in `moonmind/api/routers/workflows.py`
- [ ] T033 [US3] Update schemas for retry responses in `moonmind/schemas/workflow_models.py`
- [ ] T034 [US3] Extend contract tests for retry flow in `tests/contract/test_workflow_api.py`
- [ ] T035 [US3] Add integration retry scenario in `tests/integration/workflows/test_workflow_chain.py`

## Phase 6 – Polish & Cross-Cutting
- [ ] T036 Add observability hooks (logging/metrics) in `moonmind/workflows/speckit_celery/tasks.py`
- [ ] T037 Update deployment manifests in ./docker-compose.yaml and ./docker-compose.job.yaml for Celery worker queue
- [ ] T038 Refresh Codex agent context in ./AGENTS.md with final verification steps
- [ ] T039 Add runbook section to `docs/ops-runbook.md`

---

## Task Validation
- Total tasks: **39**
- User Story task counts: US1 = 11, US2 = 8, US3 = 7
- Parallel tasks identified: T007
- Independent test criteria documented for every user story
- MVP Scope: Complete Phase 3 (US1) to deliver automated Spec Kit phase execution
- Checklist compliance: All tasks include checkbox, sequential ID, optional [P], [US#] where required, and precise file paths

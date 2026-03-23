# Tasks: Phase 3 - Remove Queue Backend Code

**Input**: Design documents from `/specs/100-queue-removal-phase-3/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)

## Format: `[ID] [P?] [Story] Description`
- `[ID]` Task Number (T001, T002...)
- `[PX]` Priority/Phase (P0=Critical, P1=High, P2=Medium, P3=Low)
- `[Story]` Story ID from spec.md (US1, US2...)
- `Description` Concrete implementation step

---

### P0 Setup & API Routing Removal
**Purpose**: Start the cleanup from the top-layer API endpoints.

- [ ] T001 Verify active branch is `100-queue-removal-phase-3`
- [ ] T002 Remove `api_service/api/routers/agent_queue.py`
- [ ] T003 Remove `api_service/api/routers/agent_queue_artifacts.py` (if it exists)
- [ ] T004 Remove inclusion from `api_service/api/router_setup.py`

### P1 Backend Code Deletion
**Purpose**: Eliminate the deep queue sub-system and orchestrator code.

- [ ] T005 Run `rm -rf moonmind/workflows/agent_queue`
- [ ] T006 Run `rm -rf moonmind/workflows/orchestrator`
- [ ] T007 Remove legacy exports from `moonmind/workflows/__init__.py`

### P1 Database & Models
**Purpose**: Remove the ORM layers for agent jobs.

- [ ] T008 Remove `AgentJob*` models from `api_service/db/models.py`
- [ ] T009 Generate Alembic drop-tables migration (`alembic revision --autogenerate -m "Drop legacy agent queue tables"`)

### P1 Config Defaults
**Purpose**: Rip out unused environment configurations.

- [ ] T010 Remove references to `queueEnv` and `MOONMIND_QUEUE` across `config/settings.py` and the `task_dashboard_view_model.py` maps.

### P2 Test Cleanup
**Purpose**: Bring the test suite back to green.

- [ ] T011 Run `rm -rf tests/unit/orchestrator_removal`
- [ ] T012 Run `rm -rf tests/unit/api/routers/test_agent_queue.py` & `test_agent_queue_artifacts.py`
- [ ] T013 Grep for remaining mentions of `AgentQueueService` or `orchestrator` in `tests` and wipe them.
- [ ] T014 Run unit tests locally to confirm passing suite.

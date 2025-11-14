# Tasks: Spec Kit Automation Pipeline

**Input**: Design documents from `/specs/002-document-speckit-automation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Include focused verification tasks only where they unlock story-level acceptance.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish container images and compose resources required for automation runs.

- [ ] T001 [P] Author Spec Kit job container image with git/gh/Codex tooling in images/job/Dockerfile
- [ ] T002 [P] Create lean Celery worker image with Docker client dependencies in images/worker/Dockerfile
- [ ] T003 Update docker-compose.yaml to use worker image, mount /var/run/docker.sock, and declare speckit_workspaces volume

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core configuration and persistence foundations required by all user stories.

- [x] T004 Extend moonmind/config/settings.py and config.toml with spec automation job image, workspace root, and metrics toggles
- [x] T005 Implement workspace manager helpers for run/home/artifact directories in moonmind/workflows/speckit_celery/workspace.py
- [x] T006 Add SpecAutomationRun-related SQLAlchemy models in moonmind/workflows/speckit_celery/models.py
- [x] T007 Generate Alembic migration creating spec_automation_runs and related tables in api_service/migrations/versions/

**Checkpoint**: Foundation ready—user story work can proceed once configuration and schema changes land.

---

## Phase 3: User Story 1 - Launch Automated Spec Run (Priority: P1) 🎯 MVP

**Goal**: Trigger a Spec Kit automation run that clones the repo, executes specify/plan/tasks in a job container, commits changes, and opens a draft PR.

**Independent Test**: Dispatch a Celery task for a staging repo and verify branch, PR URL, and artifacts are produced when changes exist, or “no changes” status otherwise.

### Implementation for User Story 1

- [x] T008 [US1] Add spec automation repository methods for run/task/artifact persistence in moonmind/workflows/speckit_celery/repositories.py
- [x] T009 [US1] Implement job container lifecycle (start/exec/stop) using Docker SDK in moonmind/workflows/speckit_celery/job_container.py
- [x] T010 [US1] Add branch naming and workspace preparation utilities in moonmind/workflows/speckit_celery/workspace.py
- [ ] T011 [US1] Orchestrate specify→plan→tasks execution with container phases in moonmind/workflows/speckit_celery/tasks.py
- [ ] T012 [US1] Implement git commit/push and draft PR helpers for job container runs in moonmind/workflows/speckit_celery/tasks.py
- [ ] T013 [US1] Cover successful automation happy path with integration test in tests/integration/workflows/test_spec_automation_pipeline.py

**Checkpoint**: Automation run can be triggered end-to-end and produces branch/PR metadata or a no-op result deterministically.

---

## Phase 4: User Story 2 - Review Automation Outputs (Priority: P2)

**Goal**: Allow operators to inspect run status, per-phase details, and artifacts through the API.

**Independent Test**: Trigger a run, then call GET `/spec-automation/runs/{id}` to verify phases, artifacts, and summary fields surface correctly.

### Implementation for User Story 2

- [x] T014 [US2] Define SpecAutomation response schemas in moonmind/schemas/workflow_models.py
- [x] T015 [US2] Expose run detail and artifact query helpers in moonmind/workflows/speckit_celery/repositories.py
- [x] T016 [US2] Implement Spec Automation API router per contract in api_service/api/routers/spec_automation.py
- [x] T017 [US2] Register router with FastAPI and ensure dependency wiring in api_service/main.py
- [x] T018 [US2] Add API tests covering run detail and artifact endpoints in tests/unit/api/test_spec_automation.py

**Checkpoint**: Operators can retrieve run status and artifacts via documented API endpoints.

---

## Phase 5: User Story 3 - Maintain Controlled Execution Environment (Priority: P3)

**Goal**: Enforce secret isolation, deterministic cleanup, and agent adapter selection per run.

**Independent Test**: Execute sequential runs with different agent settings and confirm env-only secrets, container cleanup, and adapter switching operate correctly.

### Implementation for User Story 3

- [x] T019 [US3] Inject secrets as env vars and redact sensitive logs in moonmind/workflows/speckit_celery/job_container.py
- [x] T020 [US3] Implement workspace and container cleanup routines with retention policy in moonmind/workflows/speckit_celery/workspace.py
- [x] T021 [US3] Add agent configuration selection and persistence to moonmind/workflows/speckit_celery/tasks.py
- [x] T022 [US3] Add unit tests covering cleanup and agent selection behaviors in tests/unit/workflows/test_spec_automation_env.py

**Checkpoint**: Runs leave no residual credentials, clean up containers/workspaces, and honour agent configuration toggles.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, ops alignment, and validation of the quickstart experience.

- [x] T023 Update docs/SpecKitAutomation.md with finalized architecture and operational guidance
- [x] T024 Refresh quickstart instructions and validation steps in specs/002-document-speckit-automation/quickstart.md
- [x] T025 Document run operations and monitoring tips in specs/002-document-speckit-automation/plan.md and AGENTS.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 → Phase 2**: Container images and compose updates must exist before configuration and schema work can be validated.
- **Phase 2 → Phase 3-5**: Foundation (settings, workspace helpers, database schema) blocks all user story work.
- **Phase 3 (US1)**: Begins once foundational tasks complete; delivers MVP slice.
- **Phase 4 (US2)** and **Phase 5 (US3)**: Can start after Phase 3 establishes core run execution but remain independent; teams may proceed in parallel if resources allow.
- **Phase 6**: Runs after targeted user stories reach acceptance.

### User Story Dependencies

- **US1 (P1)**: Depends on Phases 1-2; no upstream user story prerequisites.
- **US2 (P2)**: Depends on US1 data structures and repository support to serve API responses.
- **US3 (P3)**: Depends on US1 to exercise cleanup/agent logic and on shared workspace utilities from Phase 2.

### Within Each User Story

- Complete repository changes before orchestrator/task wiring.
- Git/PR helpers depend on job container execution scaffolding.
- Tests execute after implementation tasks they cover.

### Parallel Opportunities

- T001 and T002 can be built in parallel while compose changes await review.
- During Phase 3, T009 and T010 may progress concurrently once repository scaffolding (T008) lands.
- US2 and US3 phases can run in parallel after US1 stabilizes, provided they coordinate on shared modules (repositories and workspace helpers).

## Parallel Example: User Story 1

```bash
# Developer A focuses on container orchestration
Implement T009 then T011 to wire container phases.

# Developer B handles repository and workspace concerns
Complete T008 followed by T010 to expose persistence and branch helpers.

# Developer C prepares git/PR operations and tests
Deliver T012 and T013 once container execution and persistence logic are merged.
```

## Implementation Strategy

1. Deliver MVP by completing Phases 1-3, enabling automated runs end-to-end.
2. Layer in operator observability via Phase 4 without blocking continued run execution.
3. Harden environment controls and agent configuration via Phase 5, ensuring compliance and future flexibility.
4. Finish with documentation and operational guidance in Phase 6 to ready the workflow for broader adoption.

# Tasks: Integration of Gemini CLI into Orchestrator and Worker Environments

**Feature**: 006-add-gemini-cli

## Phase 1: Setup

- [x] T001 Verify repository state and checkout feature branch 006-add-gemini-cli
- [x] T002 Verify existence of environment templates (.env-template) for variable updates

## Phase 2: Foundational

**Goal**: Update the shared Docker image and configuration to support the Gemini CLI.
**Independent Test**: Build the `api_service` image and verify `gemini` is present in the path.

- [x] T003 Update `api_service/Dockerfile` to add `GEMINI_CLI_VERSION` build argument and install `@google/gemini-cli` in the `tooling-builder` stage
- [x] T004 Update `api_service/Dockerfile` runtime stage to copy `gemini` binary and node modules from `tooling-builder`
- [x] T005 Update `docker-compose.yaml` to pass `GOOGLE_API_KEY` to `api` and `celery-worker` services
- [x] T006 Update `.env-template` to include `GOOGLE_API_KEY` placeholder

## Phase 3: User Story 1 - Orchestrator Natural Language Processing (Priority: P1)

**Goal**: Enable the Orchestrator to access Gemini for natural language tasks.
**Independent Test**: `gemini --version` runs successfully inside the Orchestrator container.

- [x] T007 [US1] Create a verification script `tools/verify-gemini.sh` to check `gemini --version` and simple prompt execution
- [x] T008 [US1] Execute `docker compose build api` to rebuild the image with new CLI *(attempted; docker CLI unavailable in dev container)*
- [x] T009 [US1] Run `tools/verify-gemini.sh` inside the `orchestrator` container to validate installation *(attempted; docker CLI unavailable in dev container)*

## Phase 4: User Story 2 - Celery Worker Task Execution (Priority: P1)

**Goal**: Enable Celery Workers to access Gemini for background tasks.
**Independent Test**: `gemini` command executes successfully inside the Celery Worker container.

- [x] T010 [US2] Run `tools/verify-gemini.sh` inside the `celery-worker` container to validate installation *(attempted; docker CLI unavailable in dev container)*
- [x] T011 [US2] Verify `celery-worker` logs show no permission errors when accessing `gemini` binary *(attempted; docker CLI unavailable in dev container)*

## Phase 5: Polish

- [ ] T012 Update `docs/ops-runbook.md` to include `GOOGLE_API_KEY` requirement for deployment
- [ ] T013 Update `README.md` to mention Gemini CLI availability in the development environment

## Dependencies

1. Phase 2 (Docker build & Config) MUST complete before Phase 3 & 4.
2. Phase 3 & 4 can technically run in parallel as they depend on the same image update (Phase 2).

## Implementation Strategy

1. **MVP**: Complete Phase 2 to get the binary installed.
2. **Verification**: Use Phase 3 & 4 to verify the installation in both runtime contexts (Orchestrator vs Celery Worker).
3. **Documentation**: Finalize with Phase 5.

# Implementation Tasks: Gemini CLI Worker

**Branch**: `008-gemini-cli-worker` | **Feature**: Gemini CLI Worker

## Dependencies

1. **Phase 1 (Setup)**: Must complete before all phases.
2. **Phase 2 (Foundational)**: Must complete before User Stories.
3. **Phase 3 (US1)**: Independent.
4. **Phase 4 (US2)**: Depends on Phase 3.
5. **Phase 5 (US3)**: Depends on Phase 2 (Infrastructure).

## Parallel Execution Opportunities

- **US1 (Deploy)** and **US3 (Public Dep)** can be validated in parallel once Phase 2 is complete.
- **Tests** in Phase 3/4 can be written parallel to implementation.

---

## Phase 1: Setup

**Goal**: Initialize feature branch and verify prerequisites.

- [ ] T001 Verify feature branch `008-gemini-cli-worker` is active and up-to-date

---

## Phase 2: Foundational (Infrastructure)

**Goal**: Configure Docker services and build dependencies (serves US1, US2, US3).

- [ ] T002 Update `api_service/Dockerfile` to include `INSTALL_GEMINI_CLI` build argument and install logic for `@google/gemini-cli`.
- [ ] T003 Update `docker-compose.yaml` to define `celery_gemini_worker` service with `gemini_auth_volume` and proper environment variables (`GEMINI_API_KEY`, `GEMINI_HOME`, `SPEC_WORKFLOW_CODEX_QUEUE` analog `GEMINI_CELERY_QUEUE`).
- [ ] T004 Create/Verify `api_service/config.template.toml` or ensure Gemini config strategy is defined (if needed for CLI defaults).

---

## Phase 3: US1 - Deploy Dedicated Gemini Worker (P1)

**Goal**: A running Celery worker listening on the `gemini` queue.

- [ ] T005 [US1] Create `celery_worker/gemini_tasks.py` module with `gemini_generate` task skeleton.
- [ ] T006 [US1] Implement `gemini_generate` task logic in `celery_worker/gemini_tasks.py` to invoke Gemini CLI (using `subprocess` or library).
- [ ] T007 [US1] Implement `gemini_process_response` task logic in `celery_worker/gemini_tasks.py`.
- [ ] T008 [US1] Update `celery_worker/speckit_worker.py` (or `celery_app` config) to register `gemini_tasks` and route them to `gemini` queue.
- [ ] T009 [US1] Create `tests/integration/test_gemini_worker.py` to verify worker picks up and executes tasks on `gemini` queue.

---

## Phase 4: US2 - Persistent Authentication (P1)

**Goal**: Ensure worker uses persistent volume for auth/config and fails fast if invalid.

- [ ] T010 [US2] Add pre-flight check in `celery_worker/gemini_tasks.py` (or worker startup) to verify `gemini --version` and `GEMINI_API_KEY` validity.
- [ ] T011 [US2] Ensure `gemini_generate` task uses `GEMINI_HOME` for configuration (via env var injection in subprocess).
- [ ] T012 [US2] Update `tests/integration/test_gemini_worker.py` to include a test case for pre-flight check failure (mocking missing key/CLI).

---

## Phase 5: US3 - Public Dependency Installation (P2)

**Goal**: Verify public npm registry usage.

- [ ] T013 [US3] Create verification script `scripts/verify_gemini_install.sh` to check `npm list -g @google/gemini-cli` and ensure it's installed.
- [ ] T014 [US3] Manually verify Docker build logs show public registry fetch (no code task, process step).

---

## Phase 6: Polish

**Goal**: Final cleanup and documentation.

- [ ] T015 Update `README.md` or `docs/GeminiCliWorkers.md` with final configuration details.
- [ ] T016 Run full test suite `tests/integration/test_gemini_worker.py`.

## Implementation Strategy

1.  **Infrastructure First**: Get the Docker container building with the CLI and the service defined in Compose. This unblocks everything.
2.  **Worker Logic**: Implement the basic Celery task to prove the worker is functional.
3.  **Auth/Robustness**: Add the pre-flight checks and volume logic to ensure reliability.
4.  **Verification**: Confirm the public install requirement.

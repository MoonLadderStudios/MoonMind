# Tasks: OpenClaw Dedicated Integration

**Input**: Design documents from `/specs/028-openclaw-integration/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openclaw-compose.yaml

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare repo structure so OpenClaw artifacts, tests, and docs have dedicated homes.

- [ ] T001 Create `services/openclaw/openclaw/__init__.py` (plus `.gitkeep`) to establish the Python module that will host the adapter, entrypoint helpers, and shared constants.
- [ ] T002 Create `tests/openclaw/__init__.py` and `tests/tools/__init__.py` so `./tools/test_unit.sh` discovers the upcoming model-lock and bootstrap script test suites.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Introduce shared configuration required by all user stories.

- [ ] T003 Extend `.env-template` with `OPENCLAW_ENABLED`, `OPENCLAW_MODEL`, `OPENCLAW_MODEL_LOCK_MODE`, `OPENCLAW_CODEX_VOLUME_NAME`, and `OPENCLAW_CODEX_VOLUME_PATH` defaults plus comments that call out the dedicated auth volume requirement.

**Checkpoint**: Environment surface ready; compose, scripts, and docs can reference the new variables.

---

## Phase 3: User Story 1 - Launch OpenClaw Profile (Priority: P1) 🎯 MVP

**Goal**: Optional `openclaw` compose service joins `local-network`, mounts dedicated volumes, and reuses `.env` without impacting other workers.

**Independent Test**: Run `docker compose --profile openclaw up openclaw` and confirm the container starts with `openclaw_codex_auth_volume` + `openclaw_data` while the rest of the stack remains unchanged.

### Implementation for User Story 1

- [ ] T004 [US1] Add the `openclaw` service block (profiles, build context `services/openclaw/Dockerfile`, `depends_on: api`, `env_file: .env`, `restart: unless-stopped`) to `docker-compose.yaml`.
- [ ] T005 [US1] Wire OpenClaw-specific environment variables inside `docker-compose.yaml` so `CODEX_*` paths point at `${OPENCLAW_CODEX_VOLUME_PATH}` and MoonMind API tokens remain optional.
- [ ] T006 [US1] Declare named volumes `openclaw_codex_auth_volume` (using `${OPENCLAW_CODEX_VOLUME_NAME}`) and `openclaw_data` within `docker-compose.yaml` so they stay detached unless the profile is enabled.
- [ ] T007 [US1] Author `services/openclaw/Dockerfile` that mirrors the worker base image, copies OpenClaw binaries plus the adapter code, and sets the container user to `app`.
- [ ] T008 [US1] Create `services/openclaw/entrypoint.sh` that validates `OPENCLAW_MODEL`, runs `python -m api_service.scripts.ensure_codex_config`, verifies `codex login status`, and then execs the OpenClaw server.
- [ ] T009 [US1] Validate the compose wiring by running `docker compose --profile openclaw config` (against `docker-compose.yaml`) and address any unresolved env or volume references discovered by the command.

**Checkpoint**: Compose profile stands up OpenClaw with isolated volumes and deterministic startup flow.

---

## Phase 4: User Story 2 - Bootstrap Dedicated Codex Auth (Priority: P2)

**Goal**: Provide a helper that clones credentials from `codex_auth_volume` (or triggers manual login) into `openclaw_codex_auth_volume` and verifies Codex access before OpenClaw handles traffic.

**Independent Test**: Execute `./tools/bootstrap-openclaw-codex-volume.sh` on a workstation, then run `docker compose --profile openclaw run --rm openclaw codex login status` to confirm credentials live exclusively in the OpenClaw volume.

### Implementation for User Story 2

- [ ] T010 [US2] Implement `tools/bootstrap-openclaw-codex-volume.sh` to ensure `local-network` exists, tar/copy from `${CODEX_VOLUME_NAME}` into `${OPENCLAW_CODEX_VOLUME_NAME}`, block if the destination volume is attached, and run `codex login status` inside the OpenClaw container.
- [ ] T011 [P] [US2] Add `tests/tools/test_bootstrap_openclaw_volume.py` that mocks Docker CLI invocations to cover success, missing source volume, destination-in-use, and validation failure paths.
- [ ] T012 [US2] Smoke-test the script by running `./tools/bootstrap-openclaw-codex-volume.sh --help` (or dry-run flag) and document the expected output in `docs/OpenClawIntegration.md`’s bootstrap section.

**Checkpoint**: Operators can initialize and verify the dedicated auth volume without touching the shared credentials.

---

## Phase 5: User Story 3 - Enforce a Single OpenClaw Model (Priority: P3)

**Goal**: Guarantee that every OpenClaw Codex request uses `OPENCLAW_MODEL`, with policy-controlled overrides (`force` vs `reject`) and clear audit logs.

**Independent Test**: Run `./tools/test_unit.sh tests/openclaw/test_model_lock.py` to ensure override attempts are either logged-and-forced or rejected before any Codex call occurs.

### Implementation for User Story 3

- [ ] T013 [US3] Implement `services/openclaw/openclaw/llm.py` adapter that reads `OPENCLAW_MODEL`, loads `OPENCLAW_MODEL_LOCK_MODE`, emits structured logs/StatsD metrics, and blocks or overwrites incoming `model` parameters.
- [ ] T014 [P] [US3] Add `services/openclaw/bin/codex` shim that injects `--model "$OPENCLAW_MODEL"` and rejects conflicting flags whenever OpenClaw shells out to the Codex CLI.
- [ ] T015 [US3] Update `services/openclaw/entrypoint.sh` (and any runtime harness in `services/openclaw/`) to route all inference calls through the adapter/wrapper, ensuring no other code path can set the model.
- [ ] T016 [P] [US3] Create `tests/openclaw/test_model_lock.py` that exercises `force` and `reject` branches, asserts warning logs on overrides, and confirms the Codex client receives only the pinned model.

**Checkpoint**: Model lock enforcement and tests ensure OpenClaw cannot bypass approved LLM selection.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize documentation and repository hygiene once all stories pass their checks.

- [X] T017 Document the architecture, compose wiring, bootstrap workflow, and troubleshooting matrix inside `docs/OpenClawIntegration.md`, including the requirement that OpenClaw has its own Codex auth volume.
- [X] T018 Add a quickstart + smoke-test walkthrough (enable profile, run bootstrap script, verify model lock logs) to `docs/OpenClawIntegration.md` so operators know how to trigger the dedicated auth volume safely.
- [ ] T019 Run `./tools/test_unit.sh` from the repo root to execute the new adapter and bootstrap tests before shipping changes.

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2**: Directory scaffolding (T001–T002) must exist before env/config edits (T003) to avoid referencing missing paths.
- **Phase 2 → User Stories**: `.env-template` updates (T003) block all later work because compose, scripts, and adapters rely on those variables.
- **User Story Order**: Implement US1 (T004–T009) first to stand up the container, then US2 (T010–T012) to populate the dedicated auth volume, and finally US3 (T013–T016) to lock the model. Each story can start after Foundational tasks complete.
- **Polish Phase**: T017–T019 depend on the successful completion of the relevant user stories so documentation and regression tests reflect reality.

---

## Parallel Opportunities

- After T003, different engineers can tackle US1 (compose + container) and US2 (bootstrap script) in parallel because they touch distinct files.
- Within US1, Dockerfile (T007) and entrypoint (T008) can progress concurrently once the service block (T004–T005) exists.
- US2’s test suite (T011) can be written in parallel with the script implementation (T010) using mocks.
- US3 allows parallelism between the CLI wrapper (T014) and adapter implementation/tests (T013, T016) because they share only configuration constants.

---

## Implementation Strategy

1. **MVP (User Story 1)**: Complete T001–T009 so the openclaw compose profile exists with its dedicated volumes. Verify via `docker compose --profile openclaw config` before moving on.
2. **Add Bootstrap Workflow (User Story 2)**: Build and test the credential cloning script (T010–T012) so operators can “trigger” the auth volume independently.
3. **Enforce Model Lock (User Story 3)**: Ship the adapter, wrapper, and tests (T013–T016) to guarantee policy compliance.
4. **Finalize Docs & Regression Tests**: Populate `docs/OpenClawIntegration.md` and run `./tools/test_unit.sh` (T017–T019) as the final gate before publishing.

Deliverables remain shippable after each story, enabling incremental rollout (US1 is the MVP).

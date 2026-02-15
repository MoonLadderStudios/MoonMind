# Tasks: Codex & Spec Kit Tooling Availability

**Input**: Design documents from `/specs/004-install-codex-spec/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No dedicated test tasks requested; verification occurs via CLI smoke commands and health checks described per story.

**Organization**: Tasks are grouped by user story priority to keep each increment independently testable.

## Format: `[ID] [P?] [Story] Description`

All tasks below follow `- [ ] T### [P?] [Story?] Description with file path`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Ensure the development environment can build and run the shared api_service image.

- [x] T001 Install Node.js toolchain prerequisites in `api_service/Dockerfile` builder stage per research decisions
- [x] T002 Document required environment variables for Codex/Spec Kit installs in `docs/SpecKitAutomationInstructions.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core build + config assets that every story depends on.

- [x] T003 Create `api_service/docker/README.md` entry describing multi-stage build flow and new build args
- [x] T004 [P] Add `CODEX_CLI_VERSION` and `SPEC_KIT_VERSION` build arguments with defaults to `api_service/Dockerfile`
- [x] T005 [P] Add multi-stage Node builder that installs `@openai/codex` and `@githubnext/spec-kit` via npm in `api_service/Dockerfile`
- [x] T006 [P] Copy Codex & Spec Kit binaries plus licenses from builder stage into the runtime layer in `api_service/Dockerfile`
- [x] T007 Update `README.md` tooling section to mention bundled CLIs and version pin strategy
- [x] T008 Extend `docs/SpecKitAutomation.md` with health-check expectations for bundled CLIs

**Checkpoint**: Dockerfile now supports versioned CLI installs and documentation references the new flow.

---

## Phase 3: User Story 1 – Containerized Codex Access (Priority: P1) 🎯 MVP

**Goal**: Codex CLI is preinstalled in the shared image and validated for Celery tasks.

**Independent Test**: Build the image, run `codex --version` inside a Celery worker container, and execute a Codex-dependent task without extra installs.

### Implementation

- [x] T009 [US1] Add builder-layer test step to run `codex --version` during Docker build in `api_service/Dockerfile`
- [x] T010 [US1] Ensure runtime layer sets PATH/permissions so `codex` is executable by the `app` user in `api_service/Dockerfile`
- [x] T011 [US1] Update `moonmind/workflows/speckit_celery/job_container.py` to assert Codex binary exists before task execution
- [x] T012 [US1] Add worker startup log message confirming Codex CLI version in `celery_worker/speckit_worker.py`
- [x] T013 [US1] Document Codex CLI verification commands in `docs/SpecKitAutomationInstructions.md#codex`

**Parallel Opportunities**: Tasks T009–T013 can run in parallel except T010 depends on T009’s install step, and T012 depends on worker log message schema shared with other logging updates.

---

## Phase 4: User Story 2 – Spec Kit CLI Availability (Priority: P2)

**Goal**: Spec Kit CLI is bundled with the image and exposed on PATH for Celery jobs.

**Independent Test**: Start worker from new image, run `speckit --version` and the smoke test without network installs.

### Implementation

- [x] T014 [P] [US2] Run `speckit --version` verification during Docker build in `api_service/Dockerfile`
- [x] T015 [US2] Confirm runtime layer exposes `speckit` on PATH with correct ownership in `api_service/Dockerfile`
- [x] T016 [US2] Update `moonmind/workflows/speckit_celery/tasks.py` to log Spec Kit CLI availability before orchestrating phases
- [x] T017 [US2] Add quickstart section covering Spec Kit smoke test in `specs/004-install-codex-spec/quickstart.md`
- [x] T018 [US2] Expand `docs/SpecKitAutomation.md` with troubleshooting specific to Spec Kit CLI failures

**Parallel Opportunities**: T014 and T015 depend on foundational builder changes but can run parallel to US1 tasks. T017–T018 are documentation-only and parallelizable after verification commands are known.

---

## Phase 5: User Story 3 – Non-interactive Codex Approvals (Priority: P3)

**Goal**: Managed `.codex/config.toml` enforces `approval_policy = "never"` and workers fail fast if missing.

**Independent Test**: Launch worker, inspect `~/.codex/config.toml` to confirm policy is set, remove the file to trigger health-check failure per acceptance criteria.

### Implementation

- [x] T019 [US3] Add `/etc/codex/config.toml` template with `approval_policy = "never"` in `api_service/Dockerfile`
- [x] T020 [US3] Implement entrypoint merge script (Python/TOML) at `api_service/scripts/ensure_codex_config.py` and invoke from Dockerfile CMD
- [x] T021 [US3] Update `moonmind/workflows/speckit_celery/speckit_worker.py` startup to fail if merge script is missing or config lacks required policy
- [x] T022 [US3] Extend health-check API contract (`specs/004-install-codex-spec/contracts/tooling-healthcheck.openapi.yaml`) with `approvalPolicy` enforcement details
- [x] T023 [US3] Document remediation steps when config drifts in `docs/SpecKitAutomationInstructions.md#codex-config`

**Parallel Opportunities**: T019 and T020 must happen sequentially; T021–T023 can proceed once merge script behavior is defined.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, docs, and release readiness for all stories.

- [x] T024 [P] Add CI job or pipeline step to run `codex --version && speckit --version` inside `docker-compose.test.yaml`
- [x] T025 [P] Update `docs/SpecKitAutomation.md` quick troubleshooting table with summary of Codex/Spec Kit install steps
- [x] T026 Prepare release notes summarizing bundled CLI versions in `docs/release-notes.md`
- [x] T027 Run full quickstart (build, smoke tests) and record outputs in `specs/004-install-codex-spec/research.md#verification`

---

## Dependencies & Execution Order

1. **Phase 1 → Phase 2**: Setup ensures environment readiness; Foundational tasks extend Dockerfile and docs—all user stories depend on Phase 2 completion.
2. **User Stories**: US1 (P1) is MVP and depends on foundational Dockerfile adjustments. US2 depends on shared builder pattern but not on US1 deliverables. US3 depends on the same foundational work plus Codex config template; it can start once merge script design is validated.
3. **Polish**: Runs after desired user stories complete to verify tooling and document release notes.

### Story Dependency Graph
- US1 (Codex CLI) → US3 (approval policy) because merge script needs Codex CLI installed first.
- US2 (Spec Kit CLI) independent once foundational Dockerfile changes land.

### Parallel Execution Examples
- Build tasks for Codex CLI (T009–T010) and Spec Kit CLI (T014–T015) can run in parallel after foundational multi-stage build exists.
- Documentation tasks (T013, T017, T018, T023, T025, T026) can be distributed across teammates concurrently.
- Health-check integration (T011, T016, T021, T022) can proceed in parallel once CLI installation locations are known.

---

## Implementation Strategy

- **MVP**: Complete Phase 1–2 plus User Story 1 (Codex CLI install & validation). This guarantees Celery runs no longer fail due to missing Codex tooling.
- **Increment 2**: Deliver User Story 2 to cover Spec Kit CLI packaging and smoke tests.
- **Increment 3**: Deliver User Story 3 to enforce non-interactive approvals and tighten health checks.
- **Finalization**: Polish phase validates end-to-end quickstart and captures release documentation.

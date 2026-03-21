# Tasks: Cursor CLI Phase 1 — Binary Integration

**Input**: Design documents from `/specs/088-cursor-cli-phase1/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No project initialization needed — this feature modifies existing infrastructure files.

- [x] T001 Create feature tracking branch `088-cursor-cli-phase1` (already done)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No foundational blocking work needed. All tasks are file-level changes in existing infrastructure.

**Checkpoint**: Foundation ready — user story implementation can begin.

---

## Phase 3: User Story 1 — Worker Image Includes Cursor CLI (Priority: P1) 🎯 MVP

**Goal**: Cursor CLI binary is installed and available on PATH in all worker containers.

**Independent Test**: `docker run --rm <image> cursor-agent --version` succeeds.

### Implementation for User Story 1

- [x] T002 [US1] Add Cursor CLI install step to `api_service/Dockerfile` — download via `curl https://cursor.com/install`, rename binary to `cursor-agent` to avoid conflicts, make executable at `/usr/local/bin/cursor-agent` (DOC-REQ-001, DOC-REQ-005)
- [ ] T003 [US1] Verify Docker image builds successfully with `docker build -t moonmind-test api_service/` and `cursor-agent --version` returns a valid version string (DOC-REQ-003)

**Checkpoint**: Worker image includes Cursor CLI. `cursor-agent --version` works in container.

---

## Phase 4: User Story 2 — Auth Provisioning Script (Priority: P1)

**Goal**: Operators can provision and verify Cursor CLI credentials via a standard script.

**Independent Test**: `tools/auth-cursor-volume.sh --check` runs without errors.

### Implementation for User Story 2

- [x] T004 [P] [US2] Create `tools/auth-cursor-volume.sh` with `--api-key` mode that stores `CURSOR_API_KEY` into the cursor auth volume (DOC-REQ-002, DOC-REQ-006)
- [x] T005 [US2] Add `--login` mode to `tools/auth-cursor-volume.sh` that runs `cursor-agent login` interactively inside a container via `docker compose run` (DOC-REQ-002)
- [x] T006 [US2] Add `--check` mode to `tools/auth-cursor-volume.sh` that runs `cursor-agent status` inside a container to verify auth state (DOC-REQ-002)
- [x] T007 [US2] Add `--register` mode to `tools/auth-cursor-volume.sh` that registers the auth profile via MoonMind API with `runtime_id=cursor_cli` (DOC-REQ-002)
- [x] T008 [US2] Add dispatch logic, help text, and `--no-register` flag to match existing `auth-gemini-volume.sh` interface pattern (DOC-REQ-002)

**Checkpoint**: Auth script fully functional with all four modes.

---

## Phase 5: User Story 3 — Headless Execution Verification (Priority: P2)

**Goal**: Cursor CLI runs headless tasks inside the container with valid credentials.

**Independent Test**: `cursor-agent -p "say hello" --output-format json` produces valid JSON.

### Implementation for User Story 3

- [ ] T009 [US3] Verify `cursor-agent -p "say hello" --output-format json` produces valid JSON output inside container with `CURSOR_API_KEY` set (DOC-REQ-003)
- [ ] T010 [US3] Verify `cursor-agent -p "say hello"` fails with a clear authentication error when no credentials are provided (DOC-REQ-003)
- [x] T011 [US3] Document auto-update behavior: Dockerfile pins version at build time, no runtime auto-update in immutable Docker layers (DOC-REQ-005)

**Checkpoint**: End-to-end headless execution verified.

---

## Phase 6: User Story 4 — Docker Compose Volume Setup (Priority: P2)

**Goal**: Docker Compose creates cursor auth volume and init service automatically.

**Independent Test**: `docker compose up cursor-auth-init` creates volume with correct permissions.

### Implementation for User Story 4

- [x] T012 [P] [US4] Add `cursor_auth_volume` to the volumes section of `docker-compose.yaml` (~line 723) with `name: ${CURSOR_VOLUME_NAME:-cursor_auth_volume}` (DOC-REQ-007)
- [x] T013 [P] [US4] Add `cursor-auth-init` service to `docker-compose.yaml` that creates volume directory with UID 1000 ownership and mode 0775, using `profiles: [init]` (DOC-REQ-007)
- [x] T014 [US4] Add `cursor_auth_volume:/home/app/.cursor` mount to `temporal-worker-sandbox` service in `docker-compose.yaml` (DOC-REQ-007)
- [ ] T015 [US4] Verify `docker compose config` includes cursor volume, init service, and worker mount definitions (DOC-REQ-007)

**Checkpoint**: Docker Compose infrastructure ready for Cursor CLI auth.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Environment documentation, validation tests, and final checks.

- [x] T016 [P] Add `CURSOR_API_KEY` entry with usage documentation to `.env-template` near existing API key entries (DOC-REQ-004)
- [ ] T017 [P] Add unit test for `cursor_cli` handling in `resolve_volume_mount_env()` in tests/unit/agents/test_adapter.py (DOC-REQ-001)
- [ ] T018 [P] Add unit test for `CURSOR_API_KEY` in OAuth scrubbable keys in tests/unit/agents/test_adapter.py (DOC-REQ-006)
- [ ] T019 Verify no binary naming conflicts — `which cursor-agent` returns the expected path and no other `agent` binary on PATH is affected (FR-009)
- [x] T020 Run ./tools/test_unit.sh to verify all existing and new tests pass
- [ ] T021 Run full Docker image build and verify no regressions to existing worker functionality

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — already complete
- **US1 (Phase 3)**: No dependencies — can start immediately
- **US2 (Phase 4)**: Depends on US4 volume definitions (T012–T013) for `--login` mode
- **US3 (Phase 5)**: Depends on US1 (Dockerfile) + US2 (auth script)
- **US4 (Phase 6)**: No dependencies — can start immediately
- **Polish (Phase 7)**: Depends on US1–US4 completion

### User Story Dependencies

- **US1 (P1)**: Independent — only needs Dockerfile
- **US2 (P1)**: Soft dependency on US4 for volume name references
- **US3 (P2)**: Depends on US1 + US2 (needs binary + auth)
- **US4 (P2)**: Independent — only needs docker-compose.yaml

### Parallel Opportunities

- T002 (Dockerfile) and T012–T013 (Docker Compose) can run in parallel
- T004–T008 (auth script modes) are sequential within one file
- T016 (.env-template) can run in parallel with any other phase

---

## Parallel Example: US1 + US4

```bash
# These can be worked on simultaneously:
Task: "T002 [US1] Add Cursor CLI install to Dockerfile"
Task: "T012 [US4] Add cursor_auth_volume to docker-compose.yaml"
Task: "T013 [US4] Add cursor-auth-init service"
Task: "T016 [Polish] Add CURSOR_API_KEY to .env-template"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete T002: Install Cursor CLI in Dockerfile
2. Complete T003: Verify build and binary
3. **STOP and VALIDATE**: `cursor-agent --version` works in container
4. This alone proves binary integration works

### Incremental Delivery

1. US1 → Binary installed → Verify
2. US4 → Compose volumes ready → Verify
3. US2 → Auth script complete → Verify
4. US3 → Headless execution verified → Verify
5. Polish → Env docs + regression check → Feature complete

---

## Notes

- Binary renamed to `cursor-agent` to avoid conflicts with generic `agent` name (research.md R1)
- Auto-update disabled by Docker layer immutability (research.md R2)
- Auth script follows `auth-gemini-volume.sh` pattern (research.md R3)
- Init service uses `profiles: [init]` for opt-in behavior (research.md R4)
- DOC-REQ traceability maintained: all 7 DOC-REQs covered by implementation + validation tasks

# Tasks: Worker GitHub Token Authentication Fast Path

**Input**: Design documents from `/specs/014-worker-git-auth/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare feature branch artifacts and worker test surface for auth fast-path work.

- [X] T001 Verify feature artifacts and contracts exist in `specs/014-worker-git-auth/`.
- [X] T002 Create/confirm GitHub auth test scaffolding in `tests/unit/agents/codex_worker/test_cli.py` and `tests/unit/agents/codex_worker/test_handlers.py`.
- [X] T003 [P] Document runtime auth contract baseline in `specs/014-worker-git-auth/contracts/worker-github-auth-runtime.md` (DOC-REQ-002, DOC-REQ-010).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared startup auth and log-safety primitives required by all user stories.

- [X] T004 Implement startup GitHub auth helper and token-redaction utility in `moonmind/agents/codex_worker/cli.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-008).
- [X] T005 [P] Implement repository URL credential detection + normalization helpers in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008).
- [X] T006 [P] Implement command-log redaction support for handler command/stdout/stderr writes in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-008).
- [X] T007 Preserve worker claim payload shape and policy handoff in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-009).

**Checkpoint**: Startup auth, repository validation, and log-safety primitives are ready for user-story behavior.

---

## Phase 3: User Story 1 - Worker Clones and Publishes Private Repositories (Priority: P1) 🎯 MVP

**Goal**: Worker startup configures GitHub auth via `GITHUB_TOKEN` and existing clone/publish flow succeeds for private repositories.

**Independent Test**: Token-present startup executes `gh` auth setup/status checks and handler clone/publish flow remains compatible for allowed repository formats.

### Tests for User Story 1

- [X] T008 [P] [US1] Add CLI tests for token-present startup invoking `gh auth login`, `gh auth setup-git`, and `gh auth status` in `tests/unit/agents/codex_worker/test_cli.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005).
- [X] T009 [P] [US1] Add handler tests for slug/HTTPS/SSH repository inputs preserving clone/publish command flow in `tests/unit/agents/codex_worker/test_handlers.py` (DOC-REQ-002, DOC-REQ-007).

### Implementation for User Story 1

- [X] T010 [US1] Wire GitHub auth preflight into worker startup lifecycle in `moonmind/agents/codex_worker/cli.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005).
- [X] T011 [US1] Keep clone/push/PR command execution path compatible with token-free repository forms in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-002, DOC-REQ-007).

**Checkpoint**: Worker auth preflight and private clone/publish compatibility are functional.

---

## Phase 4: User Story 2 - Worker Prevents Credential Leakage (Priority: P1)

**Goal**: Worker rejects tokenized URLs and prevents raw PAT exposure in logs, command traces, and surfaced errors.

**Independent Test**: Tokenized HTTPS repository inputs fail validation and test fixtures confirm logs/errors never contain raw token strings.

### Tests for User Story 2

- [X] T012 [P] [US2] Add handler tests that reject tokenized repository URLs and verify redacted command log output in `tests/unit/agents/codex_worker/test_handlers.py` (DOC-REQ-006, DOC-REQ-008).
- [X] T013 [P] [US2] Add CLI tests verifying startup auth failures redact token values in error surfaces in `tests/unit/agents/codex_worker/test_cli.py` (DOC-REQ-008, DOC-REQ-010).

### Implementation for User Story 2

- [X] T014 [US2] Enforce token-free repository validation before clone command execution in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-006, DOC-REQ-008).
- [X] T015 [US2] Apply redaction to handler command logs and startup auth error formatting in `moonmind/agents/codex_worker/handlers.py` and `moonmind/agents/codex_worker/cli.py` (DOC-REQ-008).

**Checkpoint**: Credential leakage guardrails are enforced in runtime behavior.

---

## Phase 5: User Story 3 - Fail Fast and Support Operational Recovery (Priority: P2)

**Goal**: Startup failures are deterministic and queue-policy guardrails remain intact while operators can recover through token rotation/restart.

**Independent Test**: Missing `gh` and invalid token conditions fail before polling; existing claim-policy behavior remains unchanged.

### Tests for User Story 3

- [X] T016 [P] [US3] Add CLI tests for token-absent startup behavior, fail-fast invalid-token behavior, and restart-safe auth retries in `tests/unit/agents/codex_worker/test_cli.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-010).
- [X] T017 [P] [US3] Add regression test verifying worker claim payload/policy handoff remains unchanged in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-009).

### Implementation for User Story 3

- [X] T018 [US3] Standardize actionable preflight failure messages and startup sequencing before poll loop in `moonmind/agents/codex_worker/cli.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-010).
- [X] T019 [US3] Preserve policy-safe claim request behavior while integrating auth preflight updates in `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/cli.py` (DOC-REQ-009).

**Checkpoint**: Worker startup robustness and policy guardrails are validated.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability reconciliation and feature-level validation command execution.

- [X] T020 [P] Reconcile implemented changes with `specs/014-worker-git-auth/contracts/requirements-traceability.md` and update drift in `specs/014-worker-git-auth/contracts/requirements-traceability.md`.
- [X] T021 [P] Validate quickstart/auth guidance against implemented behavior in `specs/014-worker-git-auth/quickstart.md` and `specs/014-worker-git-auth/contracts/worker-github-auth-runtime.md` (DOC-REQ-010).
- [X] T022 Run unit validation via `./tools/test_unit.sh` covering codex worker auth, handler safety, and policy-regression tests (DOC-REQ-009, DOC-REQ-010).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- User stories require foundational tasks T004-T007.

### User Story Dependencies

- US1 is MVP and can start once foundational tasks complete.
- US2 depends on US1 clone/publish compatibility surfaces.
- US3 depends on US1 startup auth path and shared worker startup lifecycle.

### Parallel Opportunities

- T003, T005, T006 can run in parallel in setup/foundational work.
- T008/T009, T012/T013, and T016/T017 are parallelizable test tasks.
- T020/T021 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Complete setup and foundational auth/log primitives.
2. Deliver token-driven startup auth and private clone/publish compatibility.
3. Validate US1 tests before moving to security hardening.

### Incremental Delivery

1. Add leakage prevention and tokenized-URL rejection (US2).
2. Add fail-fast/resilience and policy-regression coverage (US3).
3. Reconcile traceability and run full unit test command.

### Runtime Scope Commitments

- Production runtime files will be modified in `moonmind/agents/codex_worker/`.
- Validation coverage will be delivered with unit tests in `tests/unit/agents/codex_worker/` and execution of `./tools/test_unit.sh`.

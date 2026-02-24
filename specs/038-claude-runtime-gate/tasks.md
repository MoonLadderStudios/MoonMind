# Tasks: Claude Runtime API-Key Gating

**Input**: Design documents from `/specs/038-claude-runtime-gate/` (plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md)  
**Prerequisites**: Confirm Anthropic env vars are unset/set per scenario and capture current queue/dashboard behavior for regression baselines.  
**Tests**: FR-010 requires unit coverage; add tests whenever runtime behavior changes.  
**Organization**: Tasks are ordered by dependency so each user story remains independently testable.

## Format: `[ID] [P?] [Story] Description`

- `[P]` denotes tasks that different contributors can execute in parallel without blocking files.
- `[Story]` labels (e.g., `[US1]`) tie tasks to user stories from spec.md.
- Include exact file paths in every description.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Centralize the Claude gate helper so every surface references the same error message and alias logic.

- [X] T001 Add `CLAUDE_RUNTIME_DISABLED_MESSAGE` constant plus docstrings in `moonmind/claude/runtime.py` to codify the API-key gate and reference both `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY`.
- [X] T002 [P] Create `tests/unit/claude/test_runtime.py` covering `resolve_anthropic_api_key` alias precedence, whitespace trimming, and `is_claude_runtime_enabled` truthiness to lock in helper semantics.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Provide reusable runtime gate state so workers, queue validation, and dashboard config consume identical metadata.

- [X] T003 Implement a `RuntimeGateState` dataclass (enabled, source_env, error_message) plus a `build_runtime_gate_state()` helper in `moonmind/claude/runtime.py` for downstream callers.
- [X] T004 [P] Expose a lightweight accessor on `moonmind/config/settings.py` (e.g., `settings.runtime.claude_gate_state`) so services/tests can read cached gate results without re-parsing env vars every time.

**Checkpoint**: Once helpers/tests exist, feature work for user stories can import the shared gate state instead of duplicating env reads.

---

## Phase 3: User Story 1 – Worker preflight blocks unsafe Claude modes (Priority: P1) 🎯 MVP

**Goal**: Fail Claude or Claude-capable workers fast when no Anthropic key exists while still verifying `claude --version` whenever Claude work is allowed.  
**Independent Test**: Run `python -m moonmind.agents.codex_worker.cli --run-preflight` with `MOONMIND_WORKER_RUNTIME=claude` and toggle `ANTHROPIC_API_KEY` to observe failure vs. success with CLI verification.

### Tests for User Story 1

- [X] T005 [P] [US1] Expand `tests/unit/agents/codex_worker/test_cli.py` to cover: (a) runtime=claude + missing key raises `RuntimeError`, (b) runtime=claude + key runs `claude --version` exactly once, (c) universal runtime without claude capability skips Claude checks, (d) universal runtime with claude capability enforces the key gate.

### Implementation for User Story 1

- [X] T006 [US1] Remove `_run_claude_auth_status_check` and legacy auth-status handling from `moonmind/agents/codex_worker/cli.py`, replacing them with the shared gate helpers.
- [X] T007 [P] [US1] Update `run_preflight()` in `moonmind/agents/codex_worker/cli.py` to compute `claude_required` (runtime == "claude" OR capabilities include `claude`), require `build_runtime_gate_state()` when true, and raise `RuntimeError` with the canonical message if disabled.
- [X] T008 [P] [US1] Ensure `verify_cli_is_executable("claude")` + `claude --version` only execute when `claude_required` and the key is present, keeping existing Codex/Gemini checks untouched within `moonmind/agents/codex_worker/cli.py`.

**Checkpoint**: Worker preflight now shares the gate helper, preventing Claude modes from starting without credentials.

---

## Phase 4: User Story 2 – Queue rejects unavailable Claude runtimes (Priority: P1)

**Goal**: Reject queue submissions (and misconfigured defaults) that resolve to `claude` when no Anthropic key is configured.  
**Independent Test**: POST `targetRuntime=claude` to `/api/queue/jobs` with and without `ANTHROPIC_API_KEY` and observe HTTP 400 vs. acceptance; boot API with `MOONMIND_DEFAULT_TASK_RUNTIME=claude` and confirm startup error when key missing.

### Tests for User Story 2

- [X] T009 [P] [US2] Extend `tests/unit/workflows/agent_queue/test_service_hardening.py` to assert that `normalize_task_job_payload` raises `AgentQueueValidationError` with the canonical message when runtime=claude and no key, and passes when the key exists.
- [X] T010 [P] [US2] Update `tests/unit/api/routers/test_agent_queue.py` to verify the router maps the new exception to HTTP 400 with `code="claude_runtime_disabled"`.
- [X] T011 [P] [US2] Add coverage in `tests/unit/config/test_settings.py` showing `AppSettings` rejects `default_task_runtime="claude"` without a key and accepts it once the key is provided.

### Implementation for User Story 2

- [X] T012 [US2] Enforce the gate inside `moonmind/workflows/agent_queue/service.py` after runtime resolution by calling `build_runtime_gate_state()`, raising `AgentQueueValidationError` with `CLAUDE_RUNTIME_DISABLED_MESSAGE` whenever runtime=claude and `enabled` is False.
- [X] T013 [P] [US2] Ensure `api_service/api/routers/agent_queue.py` catches the new validation error path and returns the contract-specified payload `{code: "claude_runtime_disabled", message: ...}`.
- [X] T014 [US2] Add startup validation in `moonmind/config/settings.py` to raise `ValueError` (or `ConfigurationError`) when `workflow.default_task_runtime` or `MOONMIND_DEFAULT_TASK_RUNTIME` resolve to claude but the gate state is disabled.

**Checkpoint**: Invalid Claude jobs are rejected synchronously, and API boot fails when claude defaults are unusable.

---

## Phase 5: User Story 3 – Dashboard hides unusable runtimes (Priority: P2)

**Goal**: Ensure the task dashboard only advertises supported runtimes and never defaults to Claude when the gate is disabled.  
**Independent Test**: Call `GET /api/task-dashboard/config` before/after exporting `ANTHROPIC_API_KEY` and confirm `supportedTaskRuntimes` + `defaultTaskRuntime` match expectations.

### Tests for User Story 3

- [X] T015 [P] [US3] Update `tests/unit/api/routers/test_task_dashboard_view_model.py` to assert `supportedTaskRuntimes == ["codex","gemini"]` when the key is missing and that Claude appears once the key (or alias) is populated, covering `defaultTaskRuntime` fallback behavior.

### Implementation for User Story 3

- [X] T016 [US3] Refactor `_SUPPORTED_TASK_RUNTIMES` inside `api_service/api/routers/task_dashboard_view_model.py` into a builder that appends `claude` only when `build_runtime_gate_state().enabled` is True.
- [X] T017 [P] [US3] Update `build_runtime_config()` in the same module to ensure `defaultTaskRuntime` only chooses values present in `supportedTaskRuntimes`, falling back deterministically (codex → gemini) when Claude is disabled.

**Checkpoint**: Dashboard consumers receive runtime lists that already honor the Claude gate with no frontend overrides.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T018 Run `./tools/test_unit.sh` from the repo root to exercise the updated worker, queue, dashboard, and settings suites together.
- [ ] T019 [P] Follow `specs/038-claude-runtime-gate/quickstart.md` smoke steps to manually verify CLI preflight, queue POST, and dashboard runtime dropdown behaviors with and without `ANTHROPIC_API_KEY`.

---

## Dependencies & Execution Order

- **Setup → Foundational**: Helper constants/tests (T001–T002) must land before building the `RuntimeGateState` accessors (T003–T004).
- **Foundational → User Stories**: Workers, queue logic, and dashboard all import the shared gate state; do not start Phase 3+ until T003–T004 are complete.
- **User Story Dependencies**: US2 (queue/default validation) depends on the shared helper but not on US1 code; US3 depends on both helper phases but is independent of US1/US2 implementations.
- **Polish Phase**: Runs only after targeted story work completes to validate behavior end-to-end.

---

## Parallel Opportunities

- Marked `[P]` tasks (T002, T003?, T004, etc.) can proceed concurrently once their immediate prerequisites finish—for example, CLI test updates (T005) can run alongside queue/router tests (T009–T011) after the helpers are ready.
- Different teams can own US1, US2, and US3 phases in parallel once Setup + Foundational are merged, because the remaining tasks touch disjoint modules (`moonmind/agents`, `moonmind/workflows` + `api_service/api/routers/agent_queue.py`, and `api_service/api/routers/task_dashboard_view_model.py`).
- Test authoring tasks within the same user story are flagged `[P]` when they edit separate files, enabling simultaneous workstreams.

---

## Implementation Strategy

### MVP First
1. Complete Setup + Foundational helpers (T001–T004).
2. Deliver User Story 1 (T005–T008) so Claude workers never boot without credentials—this is the minimum viable product.
3. Validate via `python -m moonmind.agents.codex_worker.cli --run-preflight` before moving on.

### Incremental Delivery
1. After MVP, implement User Story 2 (T009–T014) to protect queue ingestion and startup defaults.
2. Next, implement User Story 3 (T015–T017) to align the dashboard experience.
3. Finish with cross-cutting validation (T018–T019) to ensure CLI/API/UI surfaces stay in sync.

### Team Split Example
- Developer A: US1 worker preflight tasks.
- Developer B: US2 queue + settings validation.
- Developer C: US3 dashboard runtime builder.
- Shared QA/Infra: Polish tasks (tests + quickstart smoke).

Following this plan keeps each user story independently testable while delivering the Claude runtime gate in stages.

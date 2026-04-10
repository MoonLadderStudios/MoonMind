# Tasks: Codex Managed Session Plane Phase 2

## T001 — Add TDD coverage for the session owner and request binding [P]
- [ ] T001a [P] Add unit tests for `MoonMind.AgentSession` initialization, clear/reset epoch handling, and termination state.
- [ ] T001b [P] Add unit tests for `MoonMind.Run` starting exactly one task-scoped Codex session, reusing it across steps, and skipping non-Codex runtimes.
- [ ] T001c [P] Add schema tests for `AgentExecutionRequest.managedSession`.
- [ ] T001d [P] Update worker-topology tests for the new registered workflow type.

**Independent Test**: `./.venv/bin/pytest -q tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/unit/workflows/temporal/test_temporal_workers.py`

## T002 — Implement the task-scoped session owner workflow [P]
- [ ] T002a [P] Add Phase 2 managed-session binding/input/control/status models in `moonmind/schemas/managed_session_models.py`.
- [ ] T002b [P] Add `MoonMind.AgentSession` in `moonmind/workflows/temporal/workflows/agent_session.py`.
- [ ] T002c [P] Export the new schema symbols from `moonmind/schemas/__init__.py`.

**Independent Test**: `./.venv/bin/pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py`

## T003 — Wire the root and step workflows to the session owner [P]
- [ ] T003a [P] Update `MoonMind.Run` to start/reuse/terminate one task-scoped Codex session workflow.
- [ ] T003b [P] Pass the bounded managed-session binding into managed Codex `MoonMind.AgentRun` requests.
- [ ] T003c [P] Preserve managed-session identity in managed Codex step results.

**Independent Test**: `./.venv/bin/pytest -q tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

## T004 — Register the workflow and verify [P]
- [ ] T004a [P] Register `MoonMind.AgentSession` in Temporal worker bootstrap paths.
- [ ] T004b [P] Run `./tools/test_unit.sh`.

**Independent Test**: `./tools/test_unit.sh`

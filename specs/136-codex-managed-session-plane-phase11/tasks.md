# Tasks: Codex Managed Session Plane Phase 11

## T001 — Add TDD coverage for session controls and continuity UI [P]
- [X] T001a [P] Extend `tests/unit/workflows/temporal/workflows/test_agent_session.py` to assert `MoonMind.AgentSession` can execute follow-up and clear/reset updates through the `agent_runtime.*` session activity surface.
- [X] T001b [P] Extend `tests/unit/api/routers/test_task_runs.py` to assert the new task-run session control route validates ownership, routes `send_follow_up` and `clear_session`, and returns the refreshed projection.
- [X] T001c [P] Extend `frontend/src/entrypoints/task-detail.test.tsx` to assert the Session Continuity panel renders epoch/badge metadata, preserves the existing logs/diagnostics panels, and wires `Send follow-up`, `Clear / Reset`, and `Cancel`.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`

## T002 — Add task-run session control orchestration [P]
- [X] T002a [P] Update `moonmind/workflows/temporal/workflows/agent_session.py` with workflow updates that execute `agent_runtime.send_turn`, `agent_runtime.fetch_session_summary`, `agent_runtime.publish_session_artifacts`, and `agent_runtime.clear_session`.
- [X] T002b [P] Update `api_service/api/routers/task_runs.py` to expose `POST /api/task-runs/{task_run_id}/artifact-sessions/{session_id}/control` and return the refreshed projection.
- [X] T002c [P] Keep the control path session-plane-based and avoid worker-local Codex execution or terminal/session-shell semantics.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/api/routers/test_task_runs.py`

## T003 — Ship the minimal Session Continuity panel [P]
- [X] T003a [P] Update `frontend/src/entrypoints/task-detail.tsx` to fetch the task-run session projection for Codex managed-session runs and render a dedicated `Session Continuity` panel.
- [X] T003b [P] Surface the current epoch plus latest summary/checkpoint/control/reset badges while preserving the existing `Live Logs`, `Stdout`, `Stderr`, and `Diagnostics` panels.
- [X] T003c [P] Wire `Send follow-up` and `Clear / Reset` to the new task-run session control route and reuse the existing execution cancel route for `Cancel`.
- [X] T003d [P] Do not add terminal attach, debug shell, rich transcript explorer, or branch/fork controls.

**Independent Test**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

## T004 — Verify scope and finalize [P]
- [X] T004a [P] Run `SPECIFY_FEATURE=136-codex-managed-session-plane-phase11 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T004b [P] Run `SPECIFY_FEATURE=136-codex-managed-session-plane-phase11 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
- [ ] T004c [P] Run `./tools/test_unit.sh`.

**Independent Test**: Scope validation passes and `./tools/test_unit.sh` passes.

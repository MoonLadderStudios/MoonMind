# Tasks: Codex Managed Session Plane Phase 5

## T001 — Add TDD coverage for the session adapter [P]
- [X] T001a [P] Add unit tests for `CodexSessionAdapter` launch-or-reuse behavior, canonical result persistence, and session workflow synchronization in `tests/unit/workflows/adapters/test_codex_session_adapter.py`.
- [X] T001b [P] Add unit tests for `CodexSessionAdapter.clear_session()`, `cancel()`, `fetch_session_summary()`, and `terminate_session()` in `tests/unit/workflows/adapters/test_codex_session_adapter.py`.
- [X] T001c [P] Add workflow-boundary tests for `MoonMind.AgentRun` adapter selection and managed-session result publication in `tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py`.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py`

## T002 — Implement the workflow-side Codex session adapter [P]
- [X] T002a [P] Add `moonmind/workflows/adapters/codex_session_adapter.py` implementing the session-backed managed Codex `AgentAdapter` lifecycle plus explicit session control methods.
- [X] T002b [P] Reuse or extract managed provider-profile resolution/environment-shaping helpers in `moonmind/workflows/adapters/managed_agent_adapter.py` so the session adapter gets the same profile inputs without duplicating the worker-local launcher path.
- [X] T002c [P] Persist canonical step status/result and session locator metadata for session-backed managed runs in `moonmind/workflows/adapters/codex_session_adapter.py` and related helpers.

**Independent Test**: The adapter tests pass while the implementation delegates only through the remote session control surface.

## T003 — Route managed Codex session steps through the adapter [P]
- [X] T003a [P] Update `moonmind/workflows/temporal/workflows/agent_run.py` so managed Codex requests with `managedSession` instantiate `CodexSessionAdapter`.
- [X] T003b [P] Keep managed non-session runtimes on `ManagedAgentAdapter` in `moonmind/workflows/temporal/workflows/agent_run.py`.
- [X] T003c [P] Preserve canonical `AgentRunResult` publication and managed-session metadata enrichment in `moonmind/workflows/temporal/workflows/agent_run.py`.

**Independent Test**: The workflow-boundary tests prove `MoonMind.AgentRun` uses the new adapter only for session-bound managed Codex requests.

## T004 — Validate the Phase 5 slice [P]
- [X] T004a [P] Run `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py`.
- [X] T004b [P] Run `./tools/test_unit.sh`.
- [X] T004c [P] Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

**Independent Test**: Focused tests, full unit verification, and both runtime scope validation checks pass.

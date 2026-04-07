# Tasks: Codex Managed Session Plane Phase 10

## T001 — Add TDD coverage for session-backed observability reuse [P]
- [X] T001a [P] Extend `tests/unit/workflows/adapters/test_codex_session_adapter.py` to assert a completed session-backed run persists a `ManagedRunRecord` with stdout/stderr/diagnostics refs and `liveStreamCapable=false`.
- [X] T001b [P] Extend `tests/unit/api/routers/test_task_runs.py` to assert the existing observability summary route serves a session-backed managed-run record artifact-first and reports live streaming unavailable/ended as appropriate.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py`

## T002 — Persist managed-run observability records for session-backed runs [P]
- [X] T002a [P] Update `moonmind/workflows/adapters/codex_session_adapter.py` so finalized session-backed step runs write a durable `ManagedRunRecord`.
- [X] T002b [P] Copy stdout/stderr/diagnostics refs from durable session publication metadata into the managed-run record and preserve workflow/runtime identifiers needed by the existing observability APIs.
- [X] T002c [P] Keep `liveStreamCapable=false` for this path and avoid adding terminal/session-attach semantics.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py`

## T003 — Verify scope and finalize [P]
- [X] T003a [P] Run `SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T003b [P] Run `SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
- [X] T003c [P] Run `./tools/test_unit.sh`.

**Independent Test**: Scope validation passes and `./tools/test_unit.sh` passes.

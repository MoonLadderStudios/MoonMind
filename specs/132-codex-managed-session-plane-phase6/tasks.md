# Tasks: Codex Managed Session Plane Phase 6

## T001 — Add TDD coverage for durable session supervision [P]
- [X] T001a [P] Add unit tests in `tests/unit/services/temporal/runtime/test_managed_session_store.py` for durable session-record save/load/update behavior and required Phase 6 fields.
- [X] T001b [P] Add unit tests in `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py` for spool-file supervision, artifact publication, diagnostics generation, and log offset tracking.
- [X] T001c [P] Extend `tests/unit/services/temporal/runtime/test_managed_session_controller.py` to assert launch/control persistence, summary/publication sourcing from durable records, and reconciliation behavior.
- [X] T001d [P] Extend `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` to assert worker startup runs managed-session reconciliation.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_store.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

## T002 — Implement the durable session record layer [P]
- [X] T002a [P] Extend `moonmind/schemas/managed_session_models.py` with the Phase 6 durable session record and status models.
- [X] T002b [P] Export the new managed-session schema symbols from `moonmind/schemas/__init__.py`.
- [X] T002c [P] Add `moonmind/workflows/temporal/runtime/managed_session_store.py` for JSON-backed durable session persistence.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_store.py`

## T003 — Implement session-level supervision and controller persistence [P]
- [X] T003a [P] Add `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to watch session spool files, publish stdout/stderr/diagnostics artifacts, and update `last_log_at` / `last_log_offset`.
- [X] T003b [P] Update `moonmind/workflows/temporal/runtime/codex_session_runtime.py` so session lifecycle and turn activity append restart-safe stdout/stderr spool content under the mounted artifact path.
- [X] T003c [P] Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` to persist durable session records, start/stop supervision, serve summaries/publications from stored refs, and reconcile active sessions.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## T004 — Wire startup reconciliation and verify [P]
- [X] T004a [P] Update `moonmind/workflows/temporal/worker_runtime.py` so worker startup builds the session store/supervisor and runs managed-session reconciliation before serving activity traffic.
- [X] T004b [P] Run `SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T004c [P] Run `SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
- [X] T004d [P] Run `./tools/test_unit.sh`.

**Independent Test**: Scope validation passes and `./tools/test_unit.sh` passes.

# Tasks: Codex Managed Session Plane Phase 7

## T001 — Add TDD coverage for durable reset publication [P]
- [X] T001a [P] Extend `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py` with unit tests for publishing `session.control_event` and `session.reset_boundary` artifacts with distinct epoch-specific refs.
- [X] T001b [P] Extend `tests/unit/services/temporal/runtime/test_managed_session_controller.py` to assert `clear_session` persists the new epoch/thread plus latest reset refs and that summary/publication surfaces return those durable refs.
- [X] T001c [P] Extend `tests/unit/workflows/temporal/test_agent_runtime_activities.py` or `tests/unit/workflows/temporal/workflows/test_agent_session.py` with boundary assertions confirming the existing clear/reset contract still holds while the epoch boundary stays explicit.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/workflows/test_agent_session.py`

## T002 — Implement durable reset artifact publication [P]
- [X] T002a [P] Update `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to publish `session.control_event` and `session.reset_boundary` artifacts and persist their refs on the durable session record.
- [X] T002b [P] Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` so `clear_session` records the previous and new epoch/thread boundary, invokes reset artifact publication, and preserves the remote-container control flow.
- [X] T002c [P] Update `moonmind/schemas/managed_session_models.py` if needed so managed-session publication helpers expose the latest continuity refs consistently.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## T003 — Verify scope and full unit suite [P]
- [X] T003a [P] Run `SPECIFY_FEATURE=133-codex-managed-session-plane-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T003b [P] Run `SPECIFY_FEATURE=133-codex-managed-session-plane-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
- [X] T003c [P] Run `./tools/test_unit.sh`.

**Independent Test**: Scope validation passes and `./tools/test_unit.sh` passes.

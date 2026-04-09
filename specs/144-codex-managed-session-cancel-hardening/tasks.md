# Tasks: Codex Managed Session Cancel Hardening

**Input**: Design documents from `/specs/144-codex-managed-session-cancel-hardening/`  
**Prerequisites**: plan.md, spec.md

**Tests**: Add or update failing tests before implementation changes.

## Phase 1: Failing Coverage First

- [X] T001 Add workflow-unit coverage for `TerminateSession` invoking `agent_runtime.terminate_session` when runtime handles exist in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T002 Add service-unit coverage for best-effort session-workflow teardown dispatch in `tests/unit/workflows/temporal/test_temporal_service.py`

## Phase 2: Runtime Hardening

- [X] T003 Update `moonmind/workflows/temporal/workflows/agent_session.py` so `TerminateSession` executes the existing managed-session terminate activity when handles are available
- [X] T004 Update `moonmind/workflows/temporal/service.py` so cancel requests best-effort invoke `TerminateSession` on the task-scoped Codex session workflow when a live managed-session record exists

## Phase 3: Verification

- [X] T005 Run focused verification for the updated workflow and service tests
- [X] T006 Run `./tools/test_unit.sh`

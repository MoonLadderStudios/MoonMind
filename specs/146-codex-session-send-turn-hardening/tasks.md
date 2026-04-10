# Tasks: codex-session-send-turn-hardening

**Input**: Design documents from `/specs/146-codex-session-send-turn-hardening/`  
**Prerequisites**: `spec.md`, `plan.md`

## Phase 1: Tests First

- [X] T001 Update runtime tests in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py` to capture terminal in-process `send_turn` completion and launch-time thread-path persistence.
- [X] T002 Update controller tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` to preserve the terminal/no-extra-poll contract.

## Phase 2: Runtime Hardening

- [X] T003 Preserve launch-time vendor thread path hints in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`.
- [X] T004 Refactor `send_turn` in `moonmind/workflows/temporal/runtime/codex_session_runtime.py` to wait for terminal turn completion in-process when possible and persist the terminal result before returning.

## Phase 3: Verification

- [X] T005 Run `./.venv/bin/pytest -q tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [X] T006 Run `./tools/test_unit.sh`

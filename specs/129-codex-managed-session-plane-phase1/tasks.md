# Tasks: Codex Managed Session Plane Phase 1

## T001 — Add TDD coverage for the frozen contract [P]
- [X] T001a [P] Add a unit test asserting the Phase 1 contract defaults are Codex-only, Docker-only, task-scoped, artifact-first, and disallow cross-task reuse.
- [X] T001b [P] Add a unit test asserting the canonical control-action list.
- [X] T001c [P] Add a unit test asserting `clear_session` preserves `session_id` and `container_id`, increments `session_epoch`, rotates `thread_id`, and clears `active_turn_id`.
- [X] T001d [P] Add validation tests for blank/same-thread clear requests and invalid `session_epoch`.

**Independent Test**: `./.venv/bin/pytest -q tests/unit/schemas/test_managed_session_models.py` fails before implementation and passes after.

## T002 — Implement the executable session-plane contract [P]
- [X] T002a [P] Add `moonmind/schemas/managed_session_models.py` with the frozen Phase 1 Codex session-plane contract model.
- [X] T002b [P] Add the task-scoped session state model with `clear_session()` epoch transition semantics.
- [X] T002c [P] Export the new schema symbols from `moonmind/schemas/__init__.py`.

**Independent Test**: `./.venv/bin/pytest -q tests/unit/schemas/test_managed_session_models.py tests/unit/schemas/test_agent_runtime_models.py`

## T003 — Freeze the desired-state documentation [P]
- [X] T003a [P] Add `docs/ManagedAgents/CodexCliManagedSessions.md` as the canonical desired-state contract.
- [X] T003b [P] Cross-link the new contract from `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.
- [X] T003c [P] Cross-link the new contract from `docs/Temporal/ArtifactPresentationContract.md`.
- [X] T003d [P] Add spec artifacts under `specs/129-codex-managed-session-plane-phase1/`.

**Independent Test**: Scope review confirms the canonical doc contains desired state only and phase sequencing remains in spec artifacts.

## T004 — Verify with the repo test runner [P]
- [X] T004a [P] Run `./tools/test_unit.sh`

**Independent Test**: `./tools/test_unit.sh` passes.

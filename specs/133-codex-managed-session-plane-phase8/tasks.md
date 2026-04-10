# Tasks: Codex Managed Session Plane Phase 8

## T001 — Add TDD coverage for artifact-discipline boundaries [P]
- [X] T001a [P] Extend `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py` to cover `session.summary` / `session.step_checkpoint` publication and latest continuity refs.
- [X] T001b [P] Extend `tests/unit/services/temporal/runtime/test_managed_session_controller.py` to cover `session.control_event` / `session.reset_boundary` persistence during `clear_session` and durable continuity reads.
- [X] T001c [P] Extend `tests/unit/workflows/adapters/test_codex_session_adapter.py` to assert managed-session step results preserve the published continuity refs needed for artifact-first step reconstruction.
- [X] T001d [P] Extend `tests/unit/services/temporal/test_agent_runtime_activities.py` to assert managed-session `agent_runtime.publish_artifacts` publishes `input.instructions`, optional `input.skill_snapshot`, `output.summary`, and `output.agent_result`.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/test_agent_runtime_activities.py`

## T002 — Persist the new continuity artifact refs [P]
- [X] T002a [P] Extend `moonmind/schemas/managed_session_models.py` with the latest reset-boundary ref and helper coverage for the expanded publication set.
- [X] T002b [P] Update `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to publish `session.summary` and `session.step_checkpoint` artifacts beside stdout/stderr/diagnostics.
- [X] T002c [P] Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` to persist and return summary/checkpoint/control/reset refs from durable record state.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## T003 — Make reset boundaries durable [P]
- [X] T003a [P] Update `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` and/or `moonmind/workflows/temporal/runtime/managed_session_controller.py` so `clear_session` emits `session.control_event` and `session.reset_boundary`.
- [X] T003b [P] Ensure the durable session record keeps the latest reset-boundary metadata without losing existing runtime or continuity refs.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## T004 — Publish step-scoped managed-session input/output artifacts [P]
- [X] T004a [P] Update `moonmind/workflows/adapters/codex_session_adapter.py` and `moonmind/workflows/temporal/workflows/agent_run.py` to preserve the instruction/skill/session metadata required for step-scoped artifact publication.
- [X] T004b [P] Update `moonmind/workflows/temporal/activity_runtime.py` so managed Codex session results publish `input.instructions`, optional `input.skill_snapshot`, `output.summary`, and `output.agent_result`.
- [X] T004c [P] Keep runtime stdout/stderr/diagnostics and session continuity refs visible in the returned result metadata/output refs after publication.

**Independent Test**: `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/test_agent_runtime_activities.py`

## T005 — Verify scope and finalize [P]
- [X] T005a [P] Run `SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T005b [P] Run `SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
- [X] T005c [P] Run `./tools/test_unit.sh`.

**Independent Test**: Scope validation passes and `./tools/test_unit.sh` passes.

# Implementation Plan: codex-managed-session-plane-phase8

**Branch**: `133-codex-managed-session-plane-phase8` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/133-codex-managed-session-plane-phase8/spec.md`

## Summary

Implement Phase 8 artifact discipline for the Codex managed session plane. This slice adds durable session summary/checkpoint/control/reset artifact refs to the managed-session record, publishes reset-boundary artifacts during clear operations, and upgrades managed-session step result publication so step inputs and outputs have durable artifact evidence beyond the session container.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing stdlib asyncio/json/pathlib support, `TemporalArtifactService`, `RuntimeLogStreamer`, JSON-backed managed-session store/controller, current Codex session adapter and `MoonMind.AgentRun` workflow
**Testing**: focused pytest suites plus final verification via `./tools/test_unit.sh`
**Project Type**: Temporal backend runtime, artifact publication, and managed-session supervision
**Constraints**: preserve current typed managed-session contracts; keep session control remote-container-first; keep workflow payloads compact; store latest continuity refs durably instead of depending on container state

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Temporal/worker services still own lifecycle and artifact publication; the session container remains a controlled runtime rather than the source of truth.
- **II. One-Click Agent Deployment**: PASS. No new operator prerequisite or deployment surface is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The artifact discipline sits behind managed-session controller/activity boundaries rather than Codex-specific workflow logic.
- **IV. Own Your Data**: PASS. Step/session understanding moves further toward durable artifacts and bounded metadata.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work adds explicit durable refs and artifact publication tests instead of relying on implicit container history.
- **VII. Powerful Runtime Configurability**: PASS. Existing workspace/artifact roots and session image settings remain the only runtime knobs.
- **VIII. Modular and Extensible Architecture**: PASS. Controller/supervisor/activity boundaries stay explicit and future dedicated-image work remains a packaging change.
- **IX. Resilient by Default**: PASS. Later reads come from durable record state, so restart safety improves instead of regressing.
- **XI. Spec-Driven Development**: PASS. The Phase 8 slice is specified before code edits land.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Phase sequencing remains in `specs/`; canonical docs already describe the desired artifact-first behavior.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The phase extends the live managed-session path directly and avoids compatibility side channels.

## Research

- The durable managed-session record already stores the latest continuity refs, so Phase 8 should extend that record instead of introducing a second continuity cache.
- `ManagedSessionSupervisor` already owns spool-file publication into restart-safe artifact storage; it is the right place to emit session summary/checkpoint artifacts beside stdout/stderr/diagnostics.
- `TemporalAgentRuntimeActivities.agent_runtime_publish_artifacts` already has access to `TemporalArtifactService` and Temporal activity info, so it can publish step-scoped `input.*` and `output.*` artifacts without pushing artifact semantics into workflow code.
- `clear_session` already performs the remote reset through the session controller and the `MoonMind.AgentSession` workflow signal; Phase 8 should piggyback durable control/reset artifact writes onto that existing control boundary.

## Project Structure

- Extend `moonmind/schemas/managed_session_models.py` with the additional latest reset-boundary ref and publication helpers.
- Update `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to publish `session.summary`, `session.step_checkpoint`, `session.control_event`, and `session.reset_boundary`.
- Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` to store the new refs on clear/session publication and surface them through summary/publication responses.
- Update `moonmind/workflows/adapters/codex_session_adapter.py` and `moonmind/workflows/temporal/workflows/agent_run.py` to preserve the input/session metadata needed by step artifact publication.
- Update `moonmind/workflows/temporal/activity_runtime.py` so `agent_runtime.publish_artifacts` writes managed-session `input.*` and `output.*` artifacts through the Temporal artifact service.

## Data Model

- **CodexManagedSessionRecord**
  - Existing durable fields remain.
  - Add `latest_reset_boundary_ref`.
  - Treat `latest_summary_ref`, `latest_checkpoint_ref`, `latest_control_event_ref`, and `latest_reset_boundary_ref` as the authoritative latest continuity refs.
- **Managed Session Publication Metadata**
  - Step result metadata should carry `instructionRef`, `resolvedSkillsetRef`, and the latest session artifact refs needed by `agent_runtime.publish_artifacts`.
- **Reset Artifact Payload**
  - Include `session_id`, old/new epoch, old/new thread id, reason, timestamp, and container id.

## Implementation Plan

1. Add failing tests for session artifact publication, clear/reset artifact persistence, and managed-session step artifact publication.
2. Extend the managed-session schemas and durable record helpers to represent the new continuity refs.
3. Update the managed-session supervisor to publish `session.summary` and `session.step_checkpoint` during snapshot/finalize and to publish `session.control_event` / `session.reset_boundary` during clear.
4. Update the managed-session controller to persist those refs, return them from summary/publication calls, and keep clear-session epoch updates durable.
5. Update the adapter/workflow/activity publish path so managed Codex session results produce `input.instructions`, optional `input.skill_snapshot`, `output.summary`, and `output.agent_result` artifacts.
6. Run focused tests, run scope validation, rerun the full unit suite, and mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/test_agent_runtime_activities.py`
2. `SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Launch a managed Codex session, complete one step, and confirm stdout/stderr/diagnostics plus session summary/checkpoint refs remain available after the session container exits.
2. Clear the managed session and confirm a visible control-event/reset-boundary pair exists for the new epoch.
3. Inspect the published step result and confirm it includes durable `input.*` and `output.*` artifact evidence rather than relying only on container-local continuity.

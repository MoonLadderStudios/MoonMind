# Implementation Plan: codex-managed-session-plane-phase7

**Branch**: `133-codex-managed-session-plane-phase7` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/133-codex-managed-session-plane-phase7/spec.md`

## Summary

Build the durable reset slice of the Codex managed-session plane. This phase teaches the managed-session controller/supervisor to materialize `clear_session` as durable `session.control_event` and `session.reset_boundary` artifacts, persist the latest refs on the managed session record, and return those refs through the existing summary/publication contracts without changing workflow or activity payload shapes.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing managed-session controller/store/supervisor modules, current JSON-backed runtime artifact storage, Temporal activity/workflow bindings for `agent_runtime.clear_session`
**Testing**: focused pytest suites plus final verification via `./tools/test_unit.sh`
**Project Type**: Temporal backend runtime/session supervision
**Constraints**: preserve existing managed-session activity/workflow contracts; keep the path container-first; keep reset evidence durable and artifact-first; avoid compatibility shims

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Temporal plus the managed-session controller remain the orchestrator; the remote container still only executes Codex session actions.
- **II. One-Click Agent Deployment**: PASS. The phase reuses the current worker/session deployment shape and adds no new operator prerequisite.
- **III. Avoid Vendor Lock-In**: PASS. The reset publication logic stays behind controller/supervisor boundaries and does not leak image-specific behavior into workflows.
- **IV. Own Your Data**: PASS. Reset visibility moves into durable artifacts and bounded session metadata instead of container-private state.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The phase extends the explicit session record and summary/publication contracts rather than inventing ad hoc reset bookkeeping.
- **VII. Powerful Runtime Configurability**: PASS. Artifact roots and workspace roots remain controlled by existing environment/config surfaces.
- **VIII. Modular and Extensible Architecture**: PASS. Reset artifact publication lives in the session supervisor/controller boundary and does not couple workflow code to artifact storage details.
- **IX. Resilient by Default**: PASS. Reset evidence survives container loss and worker restart because it is persisted in artifacts plus durable session record state.
- **XI. Spec-Driven Development**: PASS. The Phase 7 scope is defined in spec/plan/tasks before code changes.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs already define the desired reset semantics; implementation sequencing remains under `specs/`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The phase extends the one active session supervision path rather than layering fallback reset mechanisms on top.

## Research

- `docs/ManagedAgents/CodexManagedSessionPlane.md` already fixes clear/reset semantics as two artifacts plus an epoch bump, so the implementation only needs to align the Phase 6 durable session layer to that desired-state contract.
- `CodexManagedSessionRecord` already has `latest_checkpoint_ref` and `latest_control_event_ref`, which means Phase 7 can land without changing activity/workflow schemas.
- `ManagedSessionSupervisor` already owns durable log/diagnostics publication using the session artifact storage root, making it the correct place to add reset artifact writing instead of embedding file IO in workflow code.
- The current controller updates epoch/thread/status on `clear_session` but does not publish continuity artifacts, so the minimum viable change is controller-triggered supervisor publication after the remote clear succeeds.

## Project Structure

- Extend `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` with reset artifact publication helpers and durable record updates.
- Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` so `clear_session` persists the new epoch/thread state and invokes durable reset artifact publication.
- Adjust `moonmind/schemas/managed_session_models.py` if needed for helper behavior such as published artifact ref enumeration.
- Add or update tests under `tests/unit/services/temporal/runtime/`, `tests/unit/workflows/temporal/`, and `tests/unit/workflows/adapters/` for clear/reset durability and boundary behavior.

## Data Model

- **CodexManagedSessionRecord**
  - Existing fields retained: `session_id`, `session_epoch`, `thread_id`, `latest_control_event_ref`, `latest_checkpoint_ref`, `updated_at`
  - New Phase 7 behavior: `latest_control_event_ref` points to the newest `session.control_event` artifact and `latest_checkpoint_ref` points to the newest `session.reset_boundary` artifact.
- **Session Control Event Payload**
  - `linkType`: `session.control_event`
  - `action`: `clear_session`
  - `sessionId`, `taskRunId`, `containerId`
  - `previousSessionEpoch`, `newSessionEpoch`
  - `previousThreadId`, `newThreadId`
  - `reason`
- **Session Reset Boundary Payload**
  - `linkType`: `session.reset_boundary`
  - `sessionId`, `taskRunId`, `containerId`
  - `sessionEpoch`
  - `threadId`
  - `previousSessionEpoch`, `previousThreadId`
  - `boundaryKind`: `clear_session`

## Implementation Plan

1. Add failing tests for clear/reset durability in the managed-session supervisor and controller plus boundary assertions for summary/publication behavior after reset.
2. Implement a reset publication helper in the session supervisor that writes epoch-specific JSON artifacts for `session.control_event` and `session.reset_boundary` and updates the durable session record refs.
3. Update the controller clear/reset path to capture the previous record state, persist the returned epoch/thread transition, invoke reset publication, and preserve the container-first remote control flow.
4. Update any session-record helper behavior needed so publication responses include the latest reset refs consistently.
5. Run focused tests, update task status, run scope validation, then run `./tools/test_unit.sh`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/workflows/test_agent_session.py`
2. `SPECIFY_FEATURE=133-codex-managed-session-plane-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=133-codex-managed-session-plane-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Launch a managed session, clear it once, and verify the session artifact root contains one `session.control_event` artifact and one `session.reset_boundary` artifact for the new epoch.
2. Clear the same session again and verify the second epoch writes distinct artifact names while the durable session record points to the latest refs.
3. Fetch session summary/publication after restart and confirm the latest reset refs are returned without querying container-private continuity state.

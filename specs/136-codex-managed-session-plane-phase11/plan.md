# Implementation Plan: codex-managed-session-plane-phase11

**Branch**: `136-codex-managed-session-plane-phase11` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/136-codex-managed-session-plane-phase11/spec.md`

## Summary

Implement Phase 11 by adding a minimal Session Continuity UI to the Mission Control task detail page and wiring it to the existing task-run session projection plus a new task-run session control endpoint. The control path will execute through `MoonMind.AgentSession` and the existing `agent_runtime.*` managed-session activities so follow-up and clear/reset remain session-plane actions rather than frontend-only affordances or worker-local Codex calls.

## Technical Context

**Language/Version**: Python 3.12, TypeScript/React
**Primary Dependencies**: `MoonMind.AgentSession`, `agent_runtime.send_turn`, `agent_runtime.clear_session`, `agent_runtime.fetch_session_summary`, `agent_runtime.publish_session_artifacts`, task-runs router, Mission Control `task-detail.tsx`
**Testing**: focused pytest + Vitest suites, then final verification via `./tools/test_unit.sh`
**Project Type**: Temporal workflow/API plus Mission Control UI
**Constraints**: preserve Phase 10 artifact-first observability; keep session controls container-first through the managed session workflow/activity boundary; do not add terminal attach or debug shell semantics

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Session controls route through the existing managed-session workflow/activity boundary rather than inventing a frontend-only control loop.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency or operator prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The UI consumes a task-run session projection/control surface rather than a raw image- or CLI-specific interface.
- **IV. Own Your Data**: PASS. Continuity remains artifact-backed and readable after the session container is gone.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The new work extends the projection and session-control contracts instead of adding terminal scraping or hidden resets.
- **VII. Powerful Runtime Configurability**: PASS. The implementation reuses existing runtime/image configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Workflow control, API routing, and task-detail rendering remain separate seams.
- **IX. Resilient by Default**: PASS. Follow-up/reset actions execute through durable Temporal updates and publish refreshed continuity artifacts.
- **XI. Spec-Driven Development**: PASS. Phase 11 artifacts define the UI/control slice before code changes.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This plan is an implementation slice for an already-defined desired-state doc.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The phase adds no compatibility alias or shadow UI path.

## Research

- The Phase 9 projection API already returns the session epoch plus latest summary/checkpoint/control/reset refs, so the minimal UI can reuse it directly.
- The generic task `SendMessage` path currently forwards operator messages only to Jules, so Codex managed-session follow-up needs a dedicated session-plane control path instead of reusing the old generic control semantics.
- `MoonMind.AgentSession` already owns task-scoped session identity, and the `agent_runtime.send_turn` / `agent_runtime.clear_session` activities already exist, making the session workflow the correct place to execute follow-up/reset actions durably.
- The task detail page already keeps logs and diagnostics separate from intervention controls, so the new Session Continuity panel can sit alongside those sections without redesigning the rest of Mission Control.

## Project Structure

- Update `moonmind/workflows/temporal/workflows/agent_session.py` to add session-control updates that execute the existing managed-session activity surface.
- Update `api_service/api/routers/task_runs.py` to add a task-run session control endpoint and return the refreshed projection.
- Update `frontend/src/entrypoints/task-detail.tsx` to render the Session Continuity panel and wire the follow-up/reset controls.
- Extend:
  - `tests/unit/workflows/temporal/workflows/test_agent_session.py`
  - `tests/unit/api/routers/test_task_runs.py`
  - `frontend/src/entrypoints/task-detail.test.tsx`

## Data Model

- **TaskRunSessionControlRequest**
  - `action`: `send_follow_up` | `clear_session`
  - `message`: required for `send_follow_up`
  - `reason`: optional operator reason for either action
- **TaskRunSessionControlResult**
  - `action`: echoed requested action
  - `projection`: refreshed `ArtifactSessionProjectionModel`
- **Session Continuity View State**
  - current `session_id`
  - current `session_epoch`
  - grouped runtime/continuity/control artifacts
  - latest summary/checkpoint/control/reset badges
  - pending/success/error state for follow-up/reset controls

## Contracts

- New control contract: [contracts/task-run-session-controls.md](./contracts/task-run-session-controls.md)

## Implementation Plan

1. Add failing workflow tests proving `MoonMind.AgentSession` cannot yet execute session follow-up/reset updates through `agent_runtime.*`.
2. Add failing API tests for a new task-run session control route that validates ownership, routes `send_follow_up` and `clear_session`, and returns the refreshed projection.
3. Implement `MoonMind.AgentSession` update handlers that execute routed `agent_runtime.send_turn`, `agent_runtime.fetch_session_summary`, `agent_runtime.publish_session_artifacts`, and `agent_runtime.clear_session`.
4. Add the task-run session control route and use the managed session store plus Temporal update boundary to target the correct task-scoped session workflow.
5. Add failing frontend tests for the Session Continuity panel, visible epoch/reset metadata, and the three control actions.
6. Implement the Session Continuity panel in `task-detail.tsx`, preserving the existing logs/diagnostics panels and routing cancel through the existing execution cancel path.
7. Run focused tests, run Spec Kit scope validation, rerun the full unit suite, and mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
2. `SPECIFY_FEATURE=136-codex-managed-session-plane-phase11 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=136-codex-managed-session-plane-phase11 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Open a Codex managed-session task detail page and confirm the Session Continuity panel shows the current epoch plus latest summary/checkpoint/control/reset badges.
2. Submit a follow-up from the panel and confirm the page refreshes with updated continuity metadata while the existing logs/diagnostics panels remain unchanged.
3. Trigger `Clear / Reset` and confirm the visible epoch increments and the latest reset-boundary badge updates.
4. Confirm `Cancel` from the Session Continuity panel reuses the normal task cancellation path and no terminal attach or debug shell controls appear.

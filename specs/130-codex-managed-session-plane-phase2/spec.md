# Spec: Codex Managed Session Plane Phase 2

## Problem

Phase 1 froze the Codex managed-session contract, but MoonMind still lacks the task-scoped workflow owner that keeps session continuity independent from any single step execution. Without that workflow, managed Codex session reuse cannot be introduced behind a durable Temporal boundary.

## Requirements

### Functional Requirements

- **FR-001**: MoonMind MUST add a `MoonMind.AgentSession` workflow for task-scoped Codex managed sessions.
- **FR-002**: `MoonMind.AgentSession` MUST own the stable `session_id` and current `session_epoch` for the task-scoped Codex session.
- **FR-003**: `MoonMind.Run` MUST ensure that a managed Codex step reuses one task-scoped `MoonMind.AgentSession` instead of creating a per-step session identity.
- **FR-004**: `MoonMind.Run` MUST pass a bounded session binding into managed Codex `MoonMind.AgentRun` requests.
- **FR-005**: `MoonMind.Run` MUST terminate the task-scoped session workflow at task end.
- **FR-006**: Temporal worker registration MUST treat `MoonMind.AgentSession` as a first-class workflow type.

### Non-Functional Requirements

- **NF-001**: The new session-owner contract MUST be encoded in executable schema models, not only workflow-local dicts.
- **NF-002**: The new workflow and request-boundary wiring MUST be covered by unit tests.
- **NF-003**: Non-Codex runtimes MUST retain their existing execution path unchanged.

### Acceptance Criteria

1. **Given** a task with multiple managed Codex steps, **When** `MoonMind.Run` prepares their requests, **Then** both steps receive the same task-scoped session binding and only one `MoonMind.AgentSession` child workflow is started.
2. **Given** a task-scoped session workflow has been initialized, **When** a clear/reset control action is applied, **Then** the workflow preserves `session_id`, increments `session_epoch`, and clears `active_turn_id`.
3. **Given** a task completes, **When** finalization runs, **Then** the root workflow signals the active `MoonMind.AgentSession` to terminate and clears its local binding/handle cache.
4. **Given** a managed non-Codex runtime step, **When** `MoonMind.Run` dispatches it, **Then** no `MoonMind.AgentSession` is created.

## Scope

- **In scope**: new session-owner workflow/models, run-workflow session binding/teardown, Temporal worker registration, and unit tests.
- **Out of scope**: remote session activities, Docker session launch, session adapter implementation, projection API, and UI controls.

# Feature Specification: codex-session-send-turn-hardening

**Feature Branch**: `146-codex-session-send-turn-hardening`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Troubleshoot and fix the Codex managed-session workflow stall where the first turn fails with `thread/resume` / `no rollout found` before the workflow can progress."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep the first managed Codex turn recoverable (Priority: P1)

MoonMind operators need the first managed Codex turn to complete without depending on a separate follow-up `session_status` process to recover transient thread state, so the parent workflow can move past the first step instead of wedging on `thread/resume`.

**Why this priority**: The current failure blocks real task execution at the first managed-session step.

**Independent Test**: Exercise the container-side runtime and the managed-session controller with a resume-failure/fallback-start path and confirm `send_turn` returns a terminal response instead of leaving the controller to poll a broken fresh process.

**Acceptance Scenarios**:

1. **Given** a launched managed Codex session whose first `thread/resume` cannot recover prior rollout state, **When** `send_turn` starts a turn, **Then** the runtime waits for the terminal turn outcome inside the same invocation and returns a terminal typed response.
2. **Given** the direct runtime path receives a completed turn result in-process, **When** the state file is persisted, **Then** `lastTurnStatus`, `lastAssistantText`, and `activeTurnId` reflect the terminal result without requiring a later `session_status` refresh.
3. **Given** the controller receives a terminal `send_turn` response from the container boundary, **When** it completes the activity, **Then** it does not need a follow-up `session_status` poll to turn that response into `completed`.

### User Story 2 - Preserve the launch thread path hint (Priority: P2)

MoonMind engineers need the managed-session runtime to retain the vendor thread path returned at launch even before the rollout file appears on disk, so later recovery has the best available resume hint.

**Why this priority**: The current launch path discards the path too early, increasing the chance of unnecessary fallback thread creation and continuity loss.

**Independent Test**: Launch a managed session and confirm the persisted runtime state keeps the normalized vendor thread path from `thread/start`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The container-side Codex managed-session runtime MUST attempt to drive a started turn to a terminal outcome within the same `send_turn` invocation before falling back to later polling.
- **FR-002**: If the turn reaches a terminal outcome during that in-process wait, the runtime MUST return `completed`, `failed`, or `interrupted` directly and MUST persist the terminal state to the session state file.
- **FR-003**: If the in-process wait times out, the runtime MAY still return `running`, preserving the existing asynchronous fallback behavior.
- **FR-004**: The managed-session controller MUST continue polling only when the runtime returns a non-terminal `send_turn` status.
- **FR-005**: `launch_session` MUST persist the normalized vendor thread path returned by `thread/start` even when the referenced file does not yet exist.
- **FR-006**: The fix MUST include regression tests at the runtime/controller boundary for the failing managed-session recovery seam.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Focused runtime tests prove `send_turn` can return a terminal response immediately for the existing fallback-start path that previously required a broken `session_status` recovery.
- **SC-002**: Focused controller tests prove a terminal `send_turn` response completes without follow-up polling.
- **SC-003**: Launch-state tests prove the vendor thread path hint is preserved in the runtime state file.

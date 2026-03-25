# Temporal Message Contracts (Phase 2)

This document establishes explicit Query/Signal/Update contracts for job orchestration workflows, fulfilling the Phase 2 deliverables of the Temporal Message Passing Improvements plan.

## 1. MoonMind.Run

### Updates (Control-Plane Commands)
- **Pause**: Pauses workflow execution. Rejects if already paused, or if the workflow is in a terminal state.
- **Resume**: Resumes workflow execution. Rejects if not paused/awaiting external completion, or if terminal.
- **Approve**: Sets approval flag for external systems. Rejects if workflow is terminal.
- **Cancel**: Requests workflow cancellation natively inside the workflow and unblocks waiting states. Rejects if already canceled or terminal.
- **update_parameters**: Updates execution parameters.
- **update_title**: Updates the execution title.

### Signals (Fire-and-Forget Events)
- **ExternalEvent**: Captures asynchronous events from external systems (e.g., integrations) where a trackable update isn't strictly necessary.
- **child_state_changed**: Captures status changes from child orchestrators (e.g., AgentRun).

### Queries (Read-Only Views)
- **get_status**: Returns current execution state (`state`, `paused`, `cancel_requested`, `step_count`, `summary`, `awaiting_external`, `waiting_reason`). Contains NO mutating operations or side effects.

## 2. MoonMind.ManifestIngest

### Updates
- **Pause**: Pauses the ingestion process.
- **Resume**: Resumes the ingestion process.
- **CancelNodes**: Cancels specific execution nodes.
- **RetryNodes**: Retries specific failed nodes.
- **UpdateManifest**: Applies modifications to the ingestion manifest.
- **SetConcurrency**: Modifies the maximum number of concurrent nodes.

## 3. Implementation Plan for Workflow Updates

- [x] **Transition**: `pause`, `resume`, `approve`, and `cancel` have been converted from `@workflow.signal` to `@workflow.update` with corresponding `@update.validator` methods in `MoonMind.Run` (`moonmind/workflows/temporal/workflows/run.py`).
- [x] **Validation Rules**: Added to prevent resuming a canceled workflow, pausing an already paused workflow, or modifying a workflow in a terminal state (COMPLETED, CANCELED, FAILED).
- [x] **Unblock Waiting**: Updated `Cancel` update to clear the `_paused` flag to unblock `wait_condition` immediately upon cancellation.
- [x] **Update API Models**: Added `Cancel` and `Approve` to `SUPPORTED_UPDATE_NAMES` in `moonmind/schemas/temporal_models.py`.
- [x] **Test Adjustments**: Updated `test_run_signals_updates.py` to use `execute_update` instead of `signal` and `start_signal`.

## 4. Refactor List (Anti-Patterns Addressed)
- **Anti-Pattern**: Control plane commands like `Pause` and `Resume` were implemented as Signals instead of Updates, lacking synchronous validation to reject invalid state transitions.
  - **Resolution**: Refactored `MoonMind.Run` to use Updates for `Pause`, `Resume`, `Approve`, and `Cancel` with strong state validation.
- **Anti-Pattern**: Using `signal_with_start` to create workflows paused.
  - **Resolution**: Tests are adjusted to start the workflow normally and send a synchronous `Pause` update, ensuring correct handler invocation.

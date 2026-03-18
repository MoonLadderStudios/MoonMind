# Research: Worker Pause System (Temporal Era)

## R1: Temporal Visibility API for Drain Metrics

**Decision**: Use `ListWorkflowExecutions` with query filter `ExecutionStatus="Running"` to derive `runningCount` and `isDrained`.

**Rationale**: Temporal Visibility is the source of truth for workflow execution state. The legacy approach counted rows in agent_jobs queue table, which is irrelevant in the Temporal era.

**Alternatives considered**:
- Polling individual workflow handles: Too slow for large workflow counts.
- Maintaining a separate counter table: Redundant with Temporal's built-in tracking.

## R2: Temporal Batch Operations API for Quiesce Signals

**Decision**: Use `StartBatchOperation` with `SignalOperation` to send `pause`/`resume` signals to all running workflows matching a Visibility query.

**Rationale**: The `run.py` and `agent_run.py` workflows already have `@workflow.signal` handlers for `pause` and `resume`. Batch Operations is the Temporal-native way to signal many workflows at once.

**Alternatives considered**:
- Iterating through workflow list and signaling one-by-one: Works but is slow and introduces race conditions.
- Custom Temporal Activity that iterates: Adds unnecessary complexity.

## R3: API Guard Implementation

**Decision**: Check the `system_worker_pause_state` DB singleton's `paused` flag in `main.py` before calling `temporal_client.start_workflow()`. Return HTTP 503 with "system paused" metadata when paused.

**Rationale**: The DB singleton already exists and is the operator-controlled pause state. Checking it before workflow start is the simplest and most reliable guard.

**Alternatives considered**:
- Middleware-level guard: Too broad; would block non-workflow endpoints.
- Temporal namespace-level pause: Not supported natively; would require custom Temporal server plugin.

## R4: Existing Workflow Signal Infrastructure

**Decision**: No changes needed to `run.py` or `agent_run.py` workflow definitions. They already have the `pause`/`resume` signal handlers and `wait_condition` pattern.

**Rationale**: The signal handler sets `self._paused = True` and the workflow blocks on `await workflow.wait_condition(lambda: not self._paused)` at line 231 of `run.py`. This is exactly the Quiesce pattern described in the spec.

**Alternatives considered**: None — the existing implementation is correct.

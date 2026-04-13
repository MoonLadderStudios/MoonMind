# Data Model: Managed Session Observability and Reconcile

## ManagedSessionVisibilityMetadata

**Description**: Bounded operator-facing metadata attached to a managed Codex session workflow.

**Fields**:

- `taskRunId`: Task-scoped run identifier.
- `runtimeId`: Managed runtime identifier.
- `sessionId`: Task-scoped managed session identifier.
- `sessionEpoch`: Current session continuity epoch.
- `sessionStatus`: Current operator-visible session phase.
- `isDegraded`: Boolean indicating whether the latest known session state is degraded.
- `latestSummaryRef`: Optional compact artifact reference for the latest session summary.
- `latestCheckpointRef`: Optional compact artifact reference for the latest session checkpoint.
- `latestControlEventRef`: Optional compact artifact reference for the latest control boundary.
- `latestResetBoundaryRef`: Optional compact artifact reference for the latest reset boundary.

**Validation Rules**:

- Indexed visibility fields are exactly `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, and `IsDegraded`.
- Continuity refs may appear in current details/query state but must remain compact refs.
- Prompts, transcripts, scrollback, raw logs, credentials, secret values, and unbounded provider output are forbidden.

## ManagedSessionTransition

**Description**: Operator-visible lifecycle or control boundary for a managed session.

**Allowed Values**:

- `session_started`
- `active_turn_running`
- `interrupted`
- `cleared_to_new_epoch`
- `degraded`
- `terminating`
- `terminated`

**State Rules**:

- `cleared_to_new_epoch` increments `sessionEpoch` and clears active turn identity.
- `degraded` sets `isDegraded` to true without embedding failure bodies or raw logs.
- `terminated` is terminal for the session workflow run and must not imply recoverability.

## ManagedSessionActivitySummary

**Description**: Bounded human-readable summary attached to a managed-session control activity event.

**Fields**:

- `activityType`: Stable activity type.
- `controlAction`: Launch, send, interrupt, clear, or terminate.
- `sessionId`: Optional bounded session identifier when available.
- `sessionEpoch`: Optional bounded epoch when available.
- `containerId`: Optional bounded runtime locator when available.
- `threadId`: Optional bounded runtime locator when available.
- `turnId`: Optional bounded active-turn identifier when relevant.

**Validation Rules**:

- Summary must identify the control action.
- Summary must not include instructions, prompts, transcripts, scrollback, logs, credentials, or raw error bodies.

## ManagedSessionReconcileOutcome

**Description**: Bounded result of recurring managed-session reconciliation.

**Fields**:

- `managedSessionRecordsReconciled`: Count of records touched by reconciliation.
- `degradedSessionRecords`: Count of records found or left degraded.
- `sessionIds`: Bounded list of compact session IDs for operator triage.
- `truncated`: Boolean indicating whether additional IDs were omitted.

**Validation Rules**:

- Output must remain bounded even when many records are inspected.
- Output must not include container logs, session transcripts, credentials, or full record dumps.

## RecurringReconcileTrigger

**Description**: Durable operational trigger that starts managed-session reconciliation on a schedule.

**Fields**:

- `scheduleId`: Stable operational schedule identifier.
- `workflowType`: Reconcile workflow target.
- `workflowIdTemplate`: Stable workflow ID template for scheduled runs.
- `cadence`: Operator-configured recurring cadence.
- `enabled`: Whether recurring reconciliation is active.

**Validation Rules**:

- Create/update must be idempotent.
- Schedule metadata must remain bounded and secret-safe.
- Trigger target must delegate Docker/runtime side effects to the runtime activity boundary.

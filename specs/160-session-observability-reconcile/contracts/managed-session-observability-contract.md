# Runtime Contract: Managed Session Observability and Reconcile

## 1. Workflow Visibility Contract

Managed Codex session workflows MUST expose only bounded operator metadata.

Required indexed fields:

| Field | Type | Description |
| --- | --- | --- |
| `TaskRunId` | keyword | Task-scoped run identifier |
| `RuntimeId` | keyword | Managed runtime identifier |
| `SessionId` | keyword | Managed session identifier |
| `SessionEpoch` | integer | Current continuity epoch |
| `SessionStatus` | keyword | Current operator-visible session phase |
| `IsDegraded` | boolean | Whether the session is degraded |

Forbidden in all workflow visibility metadata:

- prompts
- transcripts
- scrollback
- raw logs
- credentials
- secret values
- unbounded provider output
- raw error bodies

## 2. Current Details Contract

Current details MUST be updated for these transitions:

- session started
- active turn running
- interrupted
- cleared to new epoch
- degraded
- terminating
- terminated

Current details MAY include compact artifact references for:

- latest summary
- latest checkpoint
- latest control event
- latest reset boundary

Current details MUST remain bounded and safe for operator display.

## 3. Activity Summary Contract

The following managed-session activities MUST have readable bounded summaries:

| Operation | Summary Requirement |
| --- | --- |
| launch | Identifies managed Codex session launch |
| send | Identifies managed Codex turn send |
| interrupt | Identifies managed Codex turn interruption |
| clear | Identifies managed Codex session clear/reset |
| terminate | Identifies managed Codex session termination |

Summaries MAY include bounded identifiers such as session ID, epoch, container ID, thread ID, or turn ID. Summaries MUST NOT include instructions, raw logs, transcripts, credentials, or raw errors.

## 4. Worker Separation Contract

Workflow workers MUST only orchestrate managed-session observability and reconciliation.

Docker/runtime side effects, including managed-session reconciliation, MUST execute through the agent-runtime activity fleet. The workflow fleet MUST NOT acquire Docker/runtime privileges for this feature.

## 5. Recurring Reconcile Contract

The system MUST provide a durable recurring trigger for managed-session reconciliation.

The trigger MUST:

- start a managed-session reconcile workflow target,
- create or update idempotently,
- use bounded and secret-safe schedule metadata,
- route reconciliation side effects through `agent_runtime.reconcile_managed_sessions`,
- return a bounded reconcile outcome.

The reconcile outcome MUST include counts and a bounded session ID list only. Full records, logs, transcripts, and credentials are forbidden.

## 6. Validation Contract

Validation MUST cover:

- initial static summary/details and bounded indexed fields,
- current details updates for major transitions,
- forbidden metadata safety for prompts/logs/secrets,
- activity summaries for launch/send/interrupt/clear/terminate,
- agent-runtime fleet routing for reconciliation,
- workflow registration for the reconcile target,
- idempotent schedule create/update behavior,
- bounded reconcile activity output.

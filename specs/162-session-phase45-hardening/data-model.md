# Data Model: Codex Managed Session Phase 4/5 Hardening

## ManagedSessionVisibilityMetadata

Bounded operator-visible session state.

Fields:

- `task_run_id`: MoonMind task run identity.
- `runtime_id`: Runtime identity, such as the Codex CLI runtime.
- `session_id`: Task-scoped managed session identity.
- `session_epoch`: Logical continuity interval for the session.
- `session_status`: Compact lifecycle state.
- `is_degraded`: Whether the session is degraded.
- `latest_summary_ref`: Optional compact artifact reference.
- `latest_checkpoint_ref`: Optional compact artifact reference.
- `latest_control_event_ref`: Optional compact artifact reference.
- `latest_reset_boundary_ref`: Optional compact artifact reference.

Validation rules:

- Must not contain prompts, transcripts, scrollback, raw logs, credentials, raw errors, or secret values.
- Indexed visibility is limited to `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, and `IsDegraded`.
- Artifact references may identify durable evidence but must not inline artifact contents.

## ManagedSessionControlOperation

A bounded lifecycle mutation requested against a managed session.

Fields:

- `action`: One of launch, send, steer, interrupt, clear, cancel, or terminate.
- `session_id`: Target session identity.
- `session_epoch`: Expected epoch for epoch-sensitive controls.
- `container_id`: Runtime container identity when handles are attached.
- `thread_id`: Runtime thread identity when handles are attached.
- `turn_id`: Active turn identity when the action targets a turn.
- `request_id`: Optional idempotency/dedupe identity.
- `reason`: Optional bounded operator reason.

Validation rules:

- Stale epochs are rejected for epoch-sensitive mutators.
- Duplicate completed request IDs do not repeat side effects.
- Interrupt and steer require an active turn.
- Clear is rejected while already clearing.
- Mutators are rejected after termination begins, except idempotent termination completion checks.
- Summaries must not include instructions, raw output, raw errors, or secret-like values.

State transitions:

- `active` -> `active turn running` -> `active`
- `active` -> `interrupted` -> `active`
- `active` -> `clearing` -> `active` with incremented epoch and new thread identity
- `active` -> `terminating` -> `terminated`
- Any non-terminal state -> `degraded` when runtime/control failure leaves state uncertain

## ManagedSessionReconcileOutcome

Bounded recurring recovery result for managed sessions.

Fields:

- `checked_count`: Number of records or runtime items inspected.
- `reattached_count`: Number of sessions recovered by reattaching supervision.
- `degraded_count`: Number of sessions marked degraded.
- `orphaned_count`: Number of orphan runtime items detected.
- `terminated_count`: Number of terminal records confirmed.
- `sample_session_ids`: Compact bounded identifiers for operator triage.
- `warnings`: Compact bounded reason codes or messages.

Validation rules:

- Outcome size remains bounded even when many sessions are inspected.
- No raw records, logs, transcripts, prompts, credentials, or container dumps are included.
- Missing containers and stale degraded records are represented with safe reason codes.

## LifecycleTestFixture

Deterministic validation scenario or replay fixture for managed session behavior.

Fields:

- `fixture_id`: Stable fixture identity.
- `scenario`: Lifecycle scenario name.
- `workflow_history_ref`: Optional representative history reference.
- `expected_state`: Bounded expected state projection.
- `expected_artifact_refs`: Expected summary, checkpoint, control, or reset refs.
- `forbidden_values`: Values that must not appear in metadata or summaries.

Validation rules:

- Fixtures must be credential-free and provider-independent for required verification.
- Fixtures must intentionally exclude prompts, transcripts, scrollback, raw logs, and secrets.
- Replay fixtures must be representative enough to catch handler-shape and state-payload changes.

## ContinueAsNewCarryForwardState

Compact state preserved across managed session workflow handoff.

Fields:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`
- `last_control_action`
- `last_control_reason`
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `request_tracking_state`

Validation rules:

- Carry-forward payload remains compact and metadata-only.
- Request-tracking state is bounded.
- No prompt, transcript, scrollback, raw log, credential, or secret values are carried forward.

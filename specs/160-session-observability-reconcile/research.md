# Research: Managed Session Observability and Reconcile

## Decision 1: Use bounded workflow UI metadata for operator state

**Decision**: Represent managed-session identity, phase, degradation state, and latest continuity references through static workflow summary/details, mutable current details, and bounded indexed visibility fields.

**Rationale**: Operators need to identify the session and current phase without opening raw activity payloads, logs, or provider output. Workflow UI metadata is the existing operator-facing surface for this class of durable runtime state.

**Alternatives considered**:

- Put all status in artifacts only: rejected because operators would need to leave workflow history/detail views for basic triage.
- Put raw session summaries or transcripts in visibility fields: rejected because indexed visibility must stay bounded and secret-safe.

## Decision 2: Keep the indexed field set deliberately small

**Decision**: Limit managed-session indexed visibility to `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, and `IsDegraded`.

**Rationale**: These fields support lookup, filtering, and triage while avoiding unbounded or sensitive data. The requested field list is sufficient for operator identity and phase inspection.

**Alternatives considered**:

- Add prompt, transcript, log, container output, or provider status fields: rejected because they can be large, sensitive, or unstable.
- Add per-activity fields: rejected because the feature is session-plane visibility, not a general activity-history indexing expansion.

## Decision 3: Generate readable activity summaries at the scheduling boundary

**Decision**: Add concise summaries when launch and session-control activities are scheduled by workflows.

**Rationale**: Temporal history becomes readable without opening every event payload, and the workflow scheduling boundary already has the control action and bounded identifiers needed for a safe summary.

**Alternatives considered**:

- Generate summaries inside the activity implementation: rejected because Temporal activity summaries are supplied by the scheduling workflow.
- Omit summaries and rely on activity type names only: rejected because type names do not always make the session/control context obvious to operators.

## Decision 4: Preserve workflow/activity worker separation

**Decision**: Keep Docker/runtime reconciliation and control work on the `agent_runtime` activity fleet while workflow workers only orchestrate.

**Rationale**: Worker separation keeps workflow processing privilege-light and prevents Docker/runtime blocking work from sharing workflow-task execution capacity.

**Alternatives considered**:

- Run reconciliation directly in the workflow worker: rejected because it would mix side effects and Docker/runtime authority into workflow processing.
- Add a new fleet for this single reconcile path: rejected because the existing agent-runtime fleet already owns managed runtime and Docker workload privileges.

## Decision 5: Use a Temporal Schedule target workflow for recurring recovery

**Decision**: Add a durable schedule target workflow that records bounded reconcile metadata and delegates side effects to `agent_runtime.reconcile_managed_sessions`.

**Rationale**: A workflow target gives recurring recovery a durable execution record, while the activity boundary keeps side effects retryable and fleet-routed.

**Alternatives considered**:

- Use an external cron or background loop: rejected because recurring operational work should be visible, durable, and managed through the Temporal control plane.
- Call the controller from API startup only: rejected because startup reconciliation does not provide recurring orphan/stale-session checks.

## Decision 6: Validate with focused runtime boundary tests

**Decision**: Cover the feature through unit tests for workflow metadata updates, activity summaries, activity catalog/routing, worker registration, reconcile activity output, and schedule creation/update.

**Rationale**: The highest-risk behavior lives at workflow/activity/client/worker boundaries. Focused tests can verify runtime behavior without requiring provider credentials.

**Alternatives considered**:

- Manual Temporal UI inspection only: rejected because runtime mode requires validation tests.
- Provider verification tests: rejected for this phase because the behavior is MoonMind orchestration metadata and local runtime routing, not third-party provider behavior.

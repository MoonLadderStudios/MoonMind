# Data Model: Remediation Mission Control Panels

## Remediation Creation Request

Represents the operator choices submitted from Mission Control to create a remediation task.

Fields:
- `target.workflowId`: canonical target execution workflow id. Required and injected by the remediation create route for path-based submissions.
- `target.runId`: pinned target run snapshot. Required when the operator submits from a known run context.
- `target.taskRunIds`: optional selected task-run or step scope identifiers. Empty or absent means all available target evidence.
- `mode`: one of `snapshot`, `snapshot_then_follow`, or `live_follow`.
- `authorityMode`: one of `observe_only`, `approval_gated`, or `admin_auto`.
- `actionPolicyRef`: optional configured remediation action policy reference.
- `evidencePolicy`: bounded evidence choices such as step ledger inclusion, diagnostics inclusion, live-follow allowance, and log tail size.
- `trigger.type`: `manual` for Mission Control operator-created remediation.

Validation:
- Target must be a visible `MoonMind.Run` execution.
- Target cannot be the remediation task itself.
- Nested remediation is denied unless a future explicit policy allows it.
- Requested `runId` must match the pinned target run.
- Requested selected task-run ids must belong to the target.

## Remediation Link Summary

Operator-facing relationship metadata returned for inbound and outbound task detail panels.

Fields:
- `remediationWorkflowId`
- `remediationRunId`
- `targetWorkflowId`
- `targetRunId`
- `selectedSteps` or equivalent selected task-run scope
- `currentTargetState`
- `mode`
- `authorityMode`
- `status`
- `activeLockScope`
- `activeLockHolder`
- `allowedActions`
- `latestActionSummary`
- `resolution`
- `contextArtifactRef`
- `evidenceDegraded`
- `unavailableEvidenceClasses`
- `liveFollow`
- `approvalState`
- `createdAt`
- `updatedAt`

Relationships:
- Inbound links show remediation tasks targeting the current execution.
- Outbound links show the target execution for the current remediation task.

## Live Observation State

Non-authoritative live-follow metadata shown in Mission Control.

Fields:
- `status`: active, unavailable, unsupported, policy_denied, disconnected, or complete.
- `supported`: whether the target task-run supports live follow.
- `taskRunId`: observed target task run.
- `resumeCursor`: compact cursor such as sequence number.
- `reconnectState`: current reconnect or disconnected state when known.
- `epoch`: managed-session epoch boundary metadata when known.
- `fallbacks`: durable evidence classes available when live follow is unavailable.
- `reason`: bounded non-secret reason for degraded state.

Rules:
- Live observation must be labeled as observation, not authoritative evidence.
- Durable artifacts remain the fallback and review source.

## Approval Handoff

Operator decision state for approval-gated or high-risk remediation actions.

Fields:
- `requestId`
- `actionKind`
- `riskTier`
- `preconditions`
- `blastRadius`
- `decision`: pending, approved, rejected, not_required, timed_out, or unavailable.
- `decisionActor`
- `decisionAt`
- `canDecide`
- `auditRef`

Validation:
- Approve/reject controls are available only when `canDecide` is true and decision is pending.
- Decisions are persisted through the remediation approval endpoint and reflected in audit state.

## Remediation Evidence Summary

Panel-level summary of bounded evidence available to the operator.

Fields:
- `contextArtifactRef`
- `artifactRefs`: remediation context, plan, decision log, action request/result, verification, and summary refs when applicable.
- `targetLogs`: bounded references to stdout, stderr, merged logs, or diagnostics.
- `evidenceDegraded`
- `unavailableEvidenceClasses`
- `nonApplicableEvidence`

Rules:
- Use artifact ids and safe labels, not raw storage paths or presigned URLs.
- Missing optional artifacts must distinguish non-applicable from unavailable.

## State Transitions

- Creation request submitted -> remediation link created with pinned target run.
- Link status progresses through diagnosis, awaiting approval, acting, verifying, resolved, escalated, or failed states.
- Approval handoff transitions pending -> approved/rejected/timed_out and records audit evidence.
- Live observation transitions active -> disconnected/reconnected/complete, or unavailable with durable fallback.
- Lock state transitions none -> held -> released/lost/conflict and remains operator-visible.

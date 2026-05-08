# Contract: Remediation Mutation Guard

## Purpose

Before any side-effecting remediation action can run, the control plane evaluates the mutation guard. The guard is a service/activity-boundary contract: workflows and tools pass compact identifiers, bounded target health, action metadata, and policy inputs; the guard returns a compact decision suitable for artifact publication and downstream execution gating.

## Request Shape

```json
{
  "remediationWorkflowId": "mm:remediate_123",
  "remediationRunId": "run_remediate_1",
  "targetWorkflowId": "mm:target_456",
  "targetRunId": "run_target_1",
  "actionKind": "workload.restart_helper_container",
  "idempotencyKey": "stable-logical-action-key",
  "parameters": {
    "reason": "bounded operator-visible reason"
  },
  "policy": {
    "lockScope": "target_execution",
    "lockMode": "exclusive",
    "lockTtlSeconds": 1800,
    "maxActionsPerTarget": 3,
    "maxAttemptsPerActionKind": 2,
    "cooldownSeconds": 300,
    "allowNestedRemediation": false,
    "allowSelfTarget": false,
    "maxSelfHealingDepth": 1,
    "targetChangePolicy": "escalate"
  },
  "targetFreshness": {
    "pinnedRunId": "run_target_1",
    "currentRunId": "run_target_1",
    "pinnedState": "executing",
    "state": "executing",
    "pinnedSummary": "old summary",
    "summary": "old summary",
    "pinnedSessionIdentity": "session-1",
    "sessionIdentity": "session-1",
    "targetRunChanged": false
  },
  "requireTargetFreshness": true,
  "targetIsRemediation": false,
  "selfHealingDepth": 1
}
```

## Response Shape

```json
{
  "schemaVersion": "v1",
  "decision": "allowed",
  "reason": "allowed",
  "executable": true,
  "remediationWorkflowId": "mm:remediate_123",
  "targetWorkflowId": "mm:target_456",
  "actionKind": "workload.restart_helper_container",
  "idempotencyKey": "stable-logical-action-key",
  "lock": {
    "status": "acquired",
    "lockId": "rlock_stable",
    "scope": "target_execution",
    "mode": "exclusive",
    "holderWorkflowId": "mm:remediate_123",
    "holderRunId": "run_remediate_1",
    "targetWorkflowId": "mm:target_456",
    "targetRunId": "run_target_1",
    "createdAt": "2026-05-08T00:00:00Z",
    "expiresAt": "2026-05-08T00:30:00Z"
  },
  "ledger": {
    "status": "recorded",
    "duplicate": false,
    "unsafeReuse": false,
    "requestShapeHash": "sha256"
  },
  "budget": {
    "status": "within_budget",
    "actionsUsed": 1,
    "maxActionsPerTarget": 3,
    "attemptsForActionKind": 1,
    "maxAttemptsPerActionKind": 2,
    "cooldownSeconds": 300
  },
  "nestedRemediation": {
    "status": "allowed",
    "allowNestedRemediation": false,
    "allowSelfTarget": false,
    "maxSelfHealingDepth": 1
  },
  "targetFreshness": {
    "status": "fresh",
    "pinnedRunId": "run_target_1",
    "currentRunId": "run_target_1",
    "state": "executing",
    "summary": "old summary",
    "sessionIdentity": "session-1",
    "targetRunChanged": false
  },
  "redactedParameters": {
    "reason": "bounded operator-visible reason"
  }
}
```

## Required Decision Reasons

The contract must preserve these bounded reasons where applicable:

- `allowed`
- `idempotency_key_required`
- `idempotency_key_unsafe_reuse`
- `mutation_lock_conflict`
- `mutation_lock_lost`
- `target_health_unavailable`
- `target_materially_changed`
- `action_budget_exhausted`
- `action_kind_attempt_budget_exhausted`
- `action_cooldown_active`
- `self_target_denied`
- `nested_remediation_denied`
- `self_healing_depth_exceeded`
- `raw_access_action_denied`

## Integration Expectations

- Downstream action execution must proceed only when `executable` is true.
- Action request/result/verification artifacts must include the action kind, target identity, idempotency key, and bounded decision evidence.
- Sensitive parameters, local filesystem paths, and presigned URLs must be redacted before artifact publication.
- Repeated identical requests must reuse ledger-backed decisions rather than producing duplicate side effects.

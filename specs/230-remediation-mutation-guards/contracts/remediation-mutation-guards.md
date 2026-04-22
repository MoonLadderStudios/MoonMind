# Contract: Remediation Mutation Guards

## Purpose

The mutation guard contract is the service-boundary decision that must pass before a side-effecting remediation action can execute. It does not execute host, container, SQL, provider, storage, network, or secret operations.

## Guard Request

```json
{
  "remediationWorkflowId": "mm:remediate_123",
  "remediationRunId": "run_remediate",
  "targetWorkflowId": "mm:target_456",
  "targetRunId": "run_target_pinned",
  "actionKind": "restart_worker",
  "dryRun": false,
  "idempotencyKey": "restart-worker-target-1",
  "parameters": {
    "reason": "worker process stalled"
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
    "pinnedRunId": "run_target_pinned",
    "currentRunId": "run_target_current",
    "state": "executing",
    "summary": "Fresh target summary",
    "sessionIdentity": "session-1",
    "targetRunChanged": true
  }
}
```

## Guard Result

```json
{
  "schemaVersion": "v1",
  "decision": "escalate",
  "reason": "target_materially_changed",
  "executable": false,
  "remediationWorkflowId": "mm:remediate_123",
  "targetWorkflowId": "mm:target_456",
  "actionKind": "restart_worker",
  "idempotencyKey": "restart-worker-target-1",
  "lock": {
    "status": "acquired",
    "lockId": "rlock_...",
    "scope": "target_execution",
    "mode": "exclusive",
    "holderWorkflowId": "mm:remediate_123",
    "targetWorkflowId": "mm:target_456",
    "expiresAt": "2026-04-22T12:30:00Z"
  },
  "ledger": {
    "status": "recorded",
    "duplicate": false,
    "unsafeReuse": false
  },
  "budget": {
    "status": "within_budget",
    "actionsUsed": 1,
    "maxActionsPerTarget": 3,
    "attemptsForActionKind": 1,
    "maxAttemptsPerActionKind": 2
  },
  "nestedRemediation": {
    "status": "allowed",
    "allowNestedRemediation": false,
    "allowSelfTarget": false,
    "maxSelfHealingDepth": 1
  },
  "targetFreshness": {
    "status": "materially_changed",
    "pinnedRunId": "run_target_pinned",
    "currentRunId": "run_target_current",
    "state": "executing",
    "targetRunChanged": true
  },
  "redactedParameters": {
    "reason": "worker process stalled"
  }
}
```

## Decision Semantics

- `allowed`: The guard passed and the side-effecting action may proceed.
- `no_op`: The action must not execute because current state makes it unnecessary or stale.
- `rediagnose`: The action must not execute and the remediation task should collect fresh evidence.
- `escalate`: The action must not execute without operator or policy escalation.
- `denied`: The action must not execute because a lock, idempotency, budget, cooldown, nested-remediation, target-health, or policy precondition failed.

## Required Failure Reasons

- `mutation_lock_conflict`
- `mutation_lock_lost`
- `idempotency_key_required`
- `idempotency_key_unsafe_reuse`
- `action_budget_exhausted`
- `action_kind_attempt_budget_exhausted`
- `action_cooldown_active`
- `self_target_denied`
- `nested_remediation_denied`
- `self_healing_depth_exceeded`
- `target_health_unavailable`
- `target_materially_changed`
- `raw_access_action_denied`

## Boundary Rules

- The guard result must be produced before any side-effecting action executes.
- Duplicate requests with the same idempotency key and request shape return the original result.
- Lock, ledger, budget, cooldown, nested-remediation, and target-freshness outputs must be redaction-safe.
- Raw host, SQL, Docker, volume, network, secret-reading, storage-key, or redaction-bypass paths must never be fallback execution paths.

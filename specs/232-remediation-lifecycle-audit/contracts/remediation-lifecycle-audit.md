# Contract: Remediation Lifecycle Audit

## Remediation Artifact Types

Required artifact names and types:

| Artifact name | artifact_type |
| --- | --- |
| `reports/remediation_context.json` | `remediation.context` |
| `reports/remediation_plan.json` | `remediation.plan` |
| `logs/remediation_decision_log.ndjson` | `remediation.decision_log` |
| `reports/remediation_action_request-<n>.json` | `remediation.action_request` |
| `reports/remediation_action_result-<n>.json` | `remediation.action_result` |
| `reports/remediation_verification-<n>.json` | `remediation.verification` |
| `reports/remediation_summary.json` | `remediation.summary` |

All remediation artifacts must use the existing artifact authorization, preview, lifecycle, and redaction boundaries.

## Run Summary Block

`reports/run_summary.json` for remediation executions must include a compact `remediation` object:

```json
{
  "remediation": {
    "targetWorkflowId": "mm:target_123",
    "targetRunId": "run_456",
    "resultingTargetRunId": "run_789",
    "phase": "resolved",
    "mode": "snapshot_then_follow",
    "authorityMode": "admin_auto",
    "actionsAttempted": [
      {
        "kind": "provider_profile.evict_stale_lease",
        "status": "applied"
      }
    ],
    "resolution": "resolved_after_action",
    "lockConflicts": 0,
    "approvalCount": 0,
    "evidenceDegraded": false,
    "escalated": false,
    "unavailableEvidenceClasses": [],
    "fallbacksUsed": []
  }
}
```

Rules:
- `phase` and `resolution` must be bounded symbolic values.
- `resultingTargetRunId` is present only when an action intentionally changes the target run and the new run is known.
- `actionsAttempted` contains compact action summaries only.
- No secrets, storage keys, presigned URLs, raw local paths, or unbounded logs are allowed.

## Target-Side Linkage Summary

Target detail read models must be able to expose:

```json
{
  "remediation": {
    "activeRemediationCount": 1,
    "latestRemediationTitle": "Publish remediation lifecycle phases, artifacts, summaries, and audit events",
    "latestRemediationStatus": "acting",
    "latestActionKind": "provider_profile.evict_stale_lease",
    "latestOutcome": "resolved_after_action",
    "activeLockScope": "target_execution",
    "activeLockHolder": "mm:remediation_123",
    "lastUpdatedAt": "2026-04-22T00:00:00Z"
  }
}
```

Rules:
- The summary is compact target metadata, not a replacement for remediation artifacts.
- Missing data should be omitted or set to `null`; consumers must not fetch raw artifact bodies to render the target summary.

## Control-Plane Audit Event

Remediation action and approval audit events must include:

```json
{
  "eventId": "audit_123",
  "eventType": "remediation.action",
  "actorUser": "user:operator",
  "executionPrincipal": "service:admin-healer",
  "remediationWorkflowId": "mm:remediation_123",
  "remediationRunId": "run_remediation",
  "targetWorkflowId": "mm:target_123",
  "targetRunId": "run_target",
  "actionKind": "provider_profile.evict_stale_lease",
  "riskTier": "medium",
  "approvalDecision": "approved",
  "timestamp": "2026-04-22T00:00:00Z",
  "metadata": {
    "resolution": "resolved_after_action"
  }
}
```

Rules:
- `metadata` must remain bounded and redacted.
- Audit events provide queryable control evidence; deep decision/action detail remains artifact-backed.

## Continue-As-New Payload

When a remediation task continues as new, the continued payload must preserve:

```json
{
  "targetWorkflowId": "mm:target_123",
  "targetRunId": "run_456",
  "contextArtifactRef": "artifact_123",
  "lockIdentity": "lock_123",
  "actionLedgerRef": "ledger_123",
  "approvalState": "approved",
  "retryBudgetState": {
    "actionsAttempted": 1
  },
  "liveFollowCursor": "cursor_123"
}
```

Rules:
- The payload carries refs and compact state only.
- Large evidence bodies and logs must remain artifact-backed.

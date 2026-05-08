# Contract: Remediation Lifecycle Repair and Prevention

## Intent

This contract defines the compact runtime evidence shape for MM-622. It extends existing remediation context/action artifacts with a deterministic lifecycle decision layer for immediate repair and recurrence prevention.

## Artifact Types

The implementation uses existing remediation artifact types:

- `remediation.context`
- `remediation.plan`
- `remediation.decision_log`
- `remediation.action_request`
- `remediation.action_result`
- `remediation.verification`
- `remediation.summary`

All artifacts are restricted, server-mediated JSON artifacts unless the existing artifact service rejects the payload.

## Repair Outcome Contract

```json
{
  "schemaVersion": "v1",
  "target": {
    "workflowId": "mm:target",
    "pinnedRunId": "run-pinned",
    "currentRunId": "run-current",
    "targetRunChanged": false
  },
  "candidate": {
    "actionKind": "workload.restart_helper_container",
    "reason": "helper_container_unhealthy"
  },
  "decision": "attempted",
  "decisionReason": "fresh_target_health_and_policy_allowed",
  "artifactRefs": {
    "actionRequest": "art_request",
    "actionResult": "art_result",
    "verification": "art_verification"
  },
  "repairOutcome": "repaired"
}
```

Allowed `decision` values:
- `attempted`
- `skipped`
- `denied`
- `unsafe`
- `approval_required`
- `escalated`

Allowed `repairOutcome` values:
- `repaired`
- `still_failed`
- `not_attempted`
- `unsafe`
- `approval_required`
- `escalated`

## Prevention Outcome Contract

```json
{
  "schemaVersion": "v1",
  "status": "reviewable_change_created",
  "rootCauseCategory": "provider_profile_lease_recovery_gap",
  "summary": "A bounded recurring failure was identified and a reviewable change was created.",
  "branch": "mm-622-prevention",
  "commit": "abc123",
  "pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/1234",
  "findingsRef": null,
  "blockedReason": null
}
```

Allowed `status` values:
- `reviewable_change_created`
- `findings_reported`
- `no_reviewable_fix`
- `policy_blocked`

## Decision Log Contract

```json
{
  "schemaVersion": "v1",
  "entries": [
    {
      "timestamp": "2026-05-08T00:00:00Z",
      "phase": "diagnosing",
      "decisionType": "repair_candidate",
      "decision": "attempted",
      "reason": "fresh_target_health_and_policy_allowed",
      "actor": "service:remediation",
      "actionKind": "workload.restart_helper_container",
      "targetWorkflowId": "mm:target",
      "targetRunId": "run-pinned",
      "artifactRefs": {
        "context": "art_context",
        "actionRequest": "art_request",
        "actionResult": "art_result",
        "verification": "art_verification"
      },
      "metadata": {}
    }
  ]
}
```

Required entry types:
- repair candidate considered
- repair attempted/skipped/denied/unsafe/approval-required/escalated reason
- action request/result/verification refs when attempted
- recurrence category
- prevention result refs or no-change reason

## Final Summary Extension

`reports/remediation_summary.json` must include:

```json
{
  "targetWorkflowId": "mm:target",
  "targetRunId": "run-pinned",
  "resultingTargetRunId": "run-result",
  "phase": "resolved",
  "mode": "snapshot_then_follow",
  "authorityMode": "admin_auto",
  "resolution": "resolved_after_action",
  "repair": {
    "repairOutcome": "repaired",
    "decision": "attempted",
    "actionKind": "workload.restart_helper_container",
    "verificationRef": "art_verification"
  },
  "prevention": {
    "status": "findings_reported",
    "rootCauseCategory": "configuration_gap",
    "findingsRef": "art_findings"
  },
  "decisionLogRef": "art_decision_log",
  "lockRelease": "released",
  "evidenceDegraded": false,
  "escalated": false
}
```

## Failure And Cancellation Rules

- If repair is unsafe, policy-denied, approval-required, or budget-exhausted, `repairOutcome` must not be `repaired`.
- If remediation is canceled, the lifecycle may record already-requested actions but must not request new target mutation after cancellation.
- Terminal finalization must attempt lock release and summary/audit publication and record skipped or failed attempts.
- Continue-As-New payloads must carry only compact refs and metadata.

## Redaction And Boundedness

- No raw credentials, tokens, cookies, private keys, presigned URLs, local filesystem paths, or artifact bodies may appear in lifecycle artifacts.
- Workflow history carries artifact refs and compact metadata only.
- Unsupported or unknown lifecycle values fail closed instead of being translated through compatibility aliases.

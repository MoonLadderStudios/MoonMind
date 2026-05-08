# Contract: Remediation Audit Evidence

## Scope

This contract describes the public and integration-visible behavior required by MM-623. It covers artifact metadata, lifecycle summary shape, queryable audit events, and target-side remediation annotations. It does not redefine artifact storage internals or grant raw artifact access.

Source coverage: DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-028.

## Artifact Family Contract

Every remediation evidence artifact published for this story must expose:

```json
{
  "artifact_type": "remediation.context | remediation.plan | remediation.decision_log | remediation.action_request | remediation.action_result | remediation.audit_event | remediation.target_annotation | remediation.verification | remediation.summary",
  "name": "bounded display name",
  "schemaVersion": "v1",
  "targetWorkflowId": "target workflow identity when applicable",
  "targetRunId": "target run identity when applicable"
}
```

Rules:
- `artifact_type` must be remediation-specific.
- Artifact metadata must be safe for control-plane display.
- Artifact refs are identifiers only; clients must not treat them as access grants or URLs.
- Non-applicable artifacts must be explained by bounded summary or decision-log reasons.

## Decision Log Contract

Decision log artifacts use this shape:

```json
{
  "schemaVersion": "v1",
  "entries": [
    {
      "timestamp": "2026-05-08T00:00:00Z",
      "phase": "diagnosing | acting | verifying | resolved | escalated | failed",
      "decisionType": "repair_candidate | prevention | approval | cancellation | verification",
      "decision": "attempted | skipped | denied | escalated | findings_reported | policy_blocked",
      "reason": "bounded rationale",
      "actor": "service or operator principal",
      "actionKind": "optional action kind",
      "targetWorkflowId": "target workflow identity",
      "targetRunId": "target run identity",
      "artifactRefs": {
        "actionRequest": "artifact id",
        "actionResult": "artifact id",
        "verification": "artifact id",
        "prevention": "artifact id"
      },
      "metadata": {
        "safeKey": "safe value"
      }
    }
  ]
}
```

Rules:
- A decision log must include at least one entry.
- Attempted repair decisions must reference action and verification evidence.
- Skipped, denied, escalated, prevention, and no-PR decisions must include bounded reasons.
- Metadata must be redacted and bounded.

## Remediation Summary Contract

Final remediation summary artifacts use this shape:

```json
{
  "targetWorkflowId": "target workflow identity",
  "targetRunId": "pinned target run identity",
  "resultingTargetRunId": "optional resulting target run identity",
  "phase": "resolved | escalated | failed",
  "mode": "remediation mode",
  "authorityMode": "observe_only | approval_gated | admin_auto",
  "actionsAttempted": [
    {
      "kind": "action kind",
      "status": "applied | skipped | denied | escalated"
    }
  ],
  "resolution": "not_applicable | diagnosis_only | no_action_needed | resolved_after_action | escalated | unsafe_to_act | lock_conflict | evidence_unavailable | failed",
  "lockConflicts": 0,
  "approvalCount": 0,
  "evidenceDegraded": false,
  "escalated": false,
  "unavailableEvidenceClasses": [],
  "fallbacksUsed": [],
  "repair": {
    "schemaVersion": "v1",
    "decision": "attempted | skipped | denied | unsafe | approval_required | escalated",
    "repairOutcome": "repaired | still_failed | not_attempted | unsafe | approval_required | escalated"
  },
  "prevention": {
    "schemaVersion": "v1",
    "status": "reviewable_change_created | findings_reported | no_reviewable_fix | policy_blocked"
  },
  "decisionLogRef": "artifact id",
  "finalAuditRef": "optional queryable audit ref",
  "lockRelease": "attempted | released | not_held | failed"
}
```

Rules:
- Required fields must remain present for every completed remediation run.
- Unknown or unsupported phase/resolution values must fail or normalize to bounded failure values rather than silently disappearing.
- Repair and prevention sections must include bounded status and safe artifact refs.

## Queryable Audit Event Contract

Side-effecting remediation decisions must publish compact queryable events:

```json
{
  "eventId": "durable event id",
  "eventType": "remediation.action",
  "actorUser": "optional user principal",
  "executionPrincipal": "service principal",
  "remediationWorkflowId": "remediation workflow identity",
  "remediationRunId": "remediation run identity",
  "targetWorkflowId": "target workflow identity",
  "targetRunId": "target run identity",
  "actionKind": "action kind",
  "riskTier": "low | medium | high",
  "approvalDecision": "approved | denied | not_required | approval_required",
  "timestamp": "RFC3339 timestamp",
  "metadata": {
    "safeKey": "safe value"
  }
}
```

Rules:
- Events must be queryable by remediation workflow/run and target workflow/run.
- Event metadata must not include artifact bodies, raw logs, secrets, presigned URLs, raw storage keys, or absolute local paths.
- Retried publication of the same side-effecting decision must be idempotent.

## Target-Side Annotation Contract

When remediation mutates a target-managed session or workload, the target side must receive supplemental evidence:

```json
{
  "schemaVersion": "v1",
  "kind": "remediation.target_annotation",
  "targetWorkflowId": "target workflow identity",
  "targetRunId": "target run identity",
  "remediationWorkflowId": "remediation workflow identity",
  "remediationRunId": "remediation run identity",
  "actionKind": "action kind",
  "decision": "attempted | skipped | denied | approval_required | escalated",
  "artifactRefs": {
    "actionRequest": "artifact id",
    "actionResult": "artifact id",
    "verification": "artifact id",
    "auditEvent": "artifact id"
  },
  "timestamp": "RFC3339 timestamp",
  "metadata": {
    "safeKey": "safe value"
  }
}
```

Rules:
- Target annotations must supplement target-native artifacts and must not overwrite them.
- Annotation metadata must be safe for target detail views.
- Annotation publication must be retry-safe.

## Compatibility and Failure Rules

- Workflow/activity payloads must remain compact and should carry refs rather than artifact bodies.
- If a new persisted audit shape is introduced and existing in-flight workflows may invoke older activity signatures, preserve worker-bound invocation compatibility or document a versioned cutover before implementation.
- Missing evidence must be represented with bounded degraded or escalated outcomes.
- Secret-like values must be redacted before persistence, publication, or display.

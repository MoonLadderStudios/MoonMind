# Contract: Remediation Authority Boundaries

## Service Boundary

`RemediationActionAuthorityService.evaluate_action_request(...)` evaluates one remediation action request and returns a deterministic decision. It does not directly invoke host, Docker, SQL, provider, or storage operations.

### Request

```json
{
  "remediationWorkflowId": "mm:remediation-workflow",
  "actionKind": "terminate_session",
  "parameters": {
    "reason": "session is wedged"
  },
  "dryRun": false,
  "idempotencyKey": "workflow-action-1",
  "requestingPrincipal": "user:operator",
  "permissions": {
    "canViewTarget": true,
    "canCreateRemediation": true,
    "canRequestAdminProfile": true,
    "canApproveHighRisk": false,
    "canInspectAudit": false
  },
  "securityProfile": {
    "profileRef": "admin_healer",
    "executionPrincipal": "service:admin-healer",
    "allowedActionKinds": ["restart_worker", "terminate_session"],
    "enabled": true
  },
  "approvalRef": null
}
```

### Response

```json
{
  "remediationWorkflowId": "mm:remediation-workflow",
  "targetWorkflowId": "mm:target-workflow",
  "authorityMode": "admin_auto",
  "actionKind": "terminate_session",
  "risk": "high",
  "decision": "approval_required",
  "reason": "high_risk_requires_approval",
  "idempotencyKey": "workflow-action-1",
  "securityProfileRef": "admin_healer",
  "approvalRef": null,
  "executable": false,
  "redactedParameters": {
    "reason": "session is wedged"
  },
  "audit": {
    "requestingPrincipal": "user:operator",
    "executionPrincipal": "service:admin-healer",
    "decision": "approval_required",
    "summary": "terminate_session requires approval"
  }
}
```

## Decision Semantics

- `observe_only` returns `dry_run_only` for dry runs and `denied` for side-effecting execution.
- `approval_gated` returns `approval_required` unless a valid `approvalRef` is supplied by a caller with high-risk approval permission when required.
- `admin_auto` returns `allowed` only when the action is enabled, policy-permitted, profile-permitted, and not blocked by risk policy.
- High-risk actions return `approval_required` or `denied` according to policy, even under `admin_auto`.
- Unknown action kinds, disabled actions, unsupported profiles, unauthorized callers, missing idempotency keys, and invalid approvals fail closed.
- Duplicate idempotency keys return the same decision payload for the same remediation workflow and action request.

## Redaction Contract

The response and audit payload must redact or reject:

- raw secrets and token-like assignments,
- private key blocks,
- presigned URLs,
- raw storage keys,
- absolute local filesystem paths,
- raw secret-bearing config bundles,
- action kinds that imply raw host shell, Docker daemon, or SQL/database access.

## Traceability

All implementation and verification artifacts for this contract must preserve Jira issue key `MM-453` and source requirement IDs `DESIGN-REQ-010`, `DESIGN-REQ-011`, and `DESIGN-REQ-024`.

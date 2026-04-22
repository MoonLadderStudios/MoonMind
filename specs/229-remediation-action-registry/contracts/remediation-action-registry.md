# Contract: Remediation Action Registry

## `list_allowed_actions`

Input:

```json
{
  "permissions": {
    "canViewTarget": true,
    "canRequestAdminProfile": true
  },
  "securityProfile": {
    "profileRef": "admin_healer",
    "enabled": true,
    "allowedActionKinds": ["restart_worker", "terminate_session"]
  }
}
```

Output:

```json
[
  {
    "actionKind": "restart_worker",
    "riskTier": "medium",
    "targetType": "workload_container",
    "inputMetadata": {
      "reason": {"type": "string", "required": false}
    },
    "verificationRequired": true,
    "verificationHint": "verify helper container health and target state"
  }
]
```

Rules:
- Return only enabled action kinds allowed by caller permissions and security profile.
- Return an empty list when permissions/profile do not authorize administrative action discovery.
- Do not return raw host, SQL, Docker daemon, storage-key, network, or secret-reading capabilities.

## `evaluate_action_request`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "actionKind": "restart_worker",
  "parameters": {"reason": "stale helper"},
  "dryRun": false,
  "idempotencyKey": "mm:remediation:run:restart_worker:target",
  "requestingPrincipal": "workflow:remediator",
  "permissions": {"canViewTarget": true, "canRequestAdminProfile": true},
  "securityProfile": {"profileRef": "admin_healer", "executionPrincipal": "service:admin-healer"},
  "approvalRef": null
}
```

Output:

```json
{
  "schemaVersion": "v1",
  "decision": "allowed",
  "executable": true,
  "request": {
    "schemaVersion": "v1",
    "actionKind": "restart_worker",
    "riskTier": "medium",
    "dryRun": false,
    "idempotencyKey": "mm:remediation:run:restart_worker:target"
  },
  "result": {
    "schemaVersion": "v1",
    "status": "applied",
    "verificationRequired": true,
    "verificationHint": "verify helper container health and target state",
    "sideEffects": []
  },
  "audit": {
    "requestingPrincipal": "workflow:remediator",
    "executionPrincipal": "service:admin-healer",
    "decision": "allowed",
    "reason": "allowed"
  }
}
```

Rules:
- Validate remediation link, action kind, permissions, profile, risk, approval, idempotency key, dry-run state, and params before any action can be executable.
- High-risk actions without required approval return `approval_required` and `executable: false`.
- Unsupported raw access requests return `denied` and `executable: false`.
- Duplicate requests with the same remediation workflow, idempotency key, action kind, and dry-run state return the original decision.
- Outputs must be redaction-safe before durable storage or display.

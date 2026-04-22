# Contract: Remediation V1 Policy Guardrails

## Task Payload Policy Contract

Runtime submissions may include future-facing policy metadata under task-level policy fields, but v1 creation semantics remain manual by default.

```json
{
  "task": {
    "remediationPolicy": {
      "enabled": true,
      "triggers": ["failed", "attention_required", "stuck"],
      "createMode": "proposal",
      "templateRef": "admin_healer_default",
      "authorityMode": "approval_gated",
      "maxActiveRemediations": 1,
      "maxSelfHealingDepth": 1
    }
  }
}
```

Contract rules:
- This payload alone must not create a remediation execution or remediation link in v1.
- Executable remediation still requires the existing explicit `task.remediation` contract.
- Unsupported automatic behavior must fail closed or remain inert; it must not silently spawn an admin healer.
- Future support must validate bounded triggers, create mode, template, authority mode, active remediation limit, depth, audit, and redaction constraints before creating remediation.

## Action Capability Contract

Allowed action metadata returned by remediation action authority must describe typed, policy-bound actions only.

Required guarantees:
- No `raw_host_shell`, host shell, raw Docker, Docker daemon, arbitrary SQL, storage-key read, decrypted secret read, or redaction-bypass action kind is discoverable as allowed action metadata.
- If such an action kind is requested directly, the decision is non-executable and includes a structured denial reason.
- Action result/request metadata remains redaction-safe.

## Bounded Outcome Contract

Policy, precondition, and edge-case failures must produce bounded machine-readable outcomes.

Required guarantees:
- Missing target visibility fails validation or returns a bounded remediation error.
- Partial evidence records degraded evidence instead of hiding the limitation.
- Live-follow unavailability falls back to durable evidence.
- Target reruns and failed preconditions record no-op, re-diagnosis, precondition_failed, or escalation.
- Lock conflicts, stale leases, missing containers, unsafe termination, and remediator failure produce explicit bounded outcomes.
- None of these outcomes may use raw access fallback.

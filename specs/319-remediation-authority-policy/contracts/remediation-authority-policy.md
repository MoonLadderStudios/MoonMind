# Contract: Remediation Authority Policy

## Authority Evaluation Request

Inputs:

- `remediationWorkflowId`: remediation execution identity.
- `actionKind`: typed remediation action identifier.
- `parameters`: action-specific inputs.
- `dryRun`: whether the request is diagnostic only.
- `idempotencyKey`: stable request key.
- `requestingPrincipal`: user or service asking for the action.
- `permissions`: target visibility, remediation creation, admin-profile, high-risk approval, and audit-inspection capabilities.
- `securityProfile`: optional named privileged profile.
- `approvalRef`: optional approval evidence ref.

## Authority Evaluation Result

Outputs:

- `schemaVersion`
- `remediationWorkflowId`
- `targetWorkflowId`
- `authorityMode`
- `actionKind`
- `risk`
- `decision`
- `reason`
- `idempotencyKey`
- `securityProfileRef`
- `approvalRef`
- `executable`
- `redactedParameters`
- `request`
- `result`
- `audit`

Rules:

- `observe_only` never executes side effects.
- `approval_gated` side-effect actions require approval.
- High-risk actions require approval and high-risk approval permission.
- Privileged execution requires an enabled named security profile that allows the action.
- Raw host shell, SQL, arbitrary Docker, arbitrary volume/network, secret-reading, and redaction-bypass operations are denied.
- Serialized outputs must not include raw secrets, bearer tokens, local workspace paths, storage keys, presigned URLs, or unauthorized target identifiers.

## Remediation Creation Boundary

Rules:

- Only supported authority modes are accepted.
- Unsupported authority modes fail validation.
- Unsupported action policy refs fail validation.
- The target workflow and run identity are pinned by the service boundary.

## Remediation Link Presentation Boundary

Rules:

- Approval-gated remediations expose bounded pending approval state.
- Displayed link summaries include compact authority mode and status.
- Unauthorized users must not receive raw action payloads or audit details.

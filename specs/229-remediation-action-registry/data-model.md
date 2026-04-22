# Data Model: Remediation Action Registry

## Action Registry Entry

Represents one enabled typed remediation action kind.

Fields:
- `actionKind`: Stable action kind string.
- `riskTier`: One of `low`, `medium`, or `high`.
- `targetType`: Target resource class the action can affect.
- `inputMetadata`: Bounded metadata describing accepted params and required fields.
- `verificationRequired`: Whether successful execution requires follow-up verification.
- `verificationHint`: Compact guidance for verifying the action result.

Validation:
- Entries are available only when enabled and allowed by the active security profile.
- Unsupported future actions are omitted until an owning control-plane path exists.

## Action Request

Represents one remediation action evaluation request.

Fields:
- `schemaVersion`: `v1`.
- `actionId`: Stable action/request identity, currently derived from idempotency key for decision output.
- `actionKind`: Typed action kind.
- `requester`: Requesting user, workflow, or principal.
- `target`: Target workflow/run/resource identity.
- `riskTier`: Declared risk tier from the registry.
- `dryRun`: Whether the request must avoid side effects.
- `idempotencyKey`: Stable retry key.
- `params`: Redacted bounded params.

Validation:
- Missing idempotency key fails closed.
- Unknown or raw action kinds fail closed.
- Params are redacted before serialization.

## Action Result

Represents the bounded result/decision envelope for one evaluated action.

Fields:
- `schemaVersion`: `v1`.
- `actionId`: Matches request identity.
- `status`: Closed status such as `applied`, `approval_required`, `precondition_failed`, `rejected`, or `failed`.
- `appliedAt`: Present only when an owning executor applies an action.
- `beforeStateRef`: Optional bounded ref to pre-action state.
- `afterStateRef`: Optional bounded ref to post-action state.
- `verificationRequired`: Whether follow-up verification is required.
- `verificationHint`: Compact verification guidance.
- `sideEffects`: Bounded side-effect descriptors.

Validation:
- Denied and approval-required decisions are not executable.
- Executable decisions include verification guidance.

## Action Audit Record

Represents durable review evidence for an action decision.

Fields:
- `requestingPrincipal`: Redacted caller identity.
- `executionPrincipal`: Redacted privileged principal when present.
- `decision`: `allowed`, `approval_required`, `dry_run_only`, or `denied`.
- `reason`: Stable decision reason.
- `summary`: Redacted compact summary.

Validation:
- Raw secrets, bearer headers, presigned URLs, storage keys, and absolute local filesystem paths are redacted.

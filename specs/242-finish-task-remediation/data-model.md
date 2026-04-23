# Data Model: Finish Task Remediation Desired-State Implementation

## Remediation Action Definition

- `actionKind`: canonical dotted action kind.
- `targetType`: target resource class such as execution, managed session, provider profile lease, or workload container.
- `riskTier`: `low`, `medium`, or `high`.
- `inputMetadata`: bounded field metadata for accepted inputs.
- `preconditions`: bounded checks required before execution.
- `idempotency`: key requirements and duplicate behavior.
- `verification`: required post-action verification description.
- `auditPayloadShape`: bounded audit fields emitted for the action.

Validation:
- Unknown raw access action kinds are denied.
- New action kinds must declare all metadata above.
- Legacy internal aliases are not accepted as compatibility shims.

## Remediation Action Request

- `schemaVersion`
- `actionId`
- `actionKind`
- `requester`
- `target`
- `riskTier`
- `dryRun`
- `idempotencyKey`
- `params`

Validation:
- Target identity must match the remediation link.
- Parameters are redacted before durable output.
- Idempotency key is required for side-effecting actions.

## Remediation Action Result

- `schemaVersion`
- `actionId`
- `status`
- `appliedAt`
- `beforeStateRef`
- `afterStateRef`
- `verificationRequired`
- `verificationHint`
- `sideEffects`

Validation:
- Result status is bounded.
- State references are artifact refs, not raw paths or URLs.

## Remediation Mutation Lock

- `lockId`
- `scope`
- `mode`
- `holderWorkflowId`
- `holderRunId`
- `targetWorkflowId`
- `targetRunId`
- `createdAt`
- `expiresAt`
- `releasedAt`

Validation:
- Exclusive locks prevent conflicting target mutations.
- Expired locks can be recovered through bounded policy.
- Released or lost locks deny stale holders.

## Remediation Action Ledger

- `remediationWorkflowId`
- `targetWorkflowId`
- `actionKind`
- `idempotencyKey`
- `requestShapeHash`
- `decision`
- `reason`
- `recordedAt`

Validation:
- Duplicate same-shape requests return the original decision.
- Same idempotency key with a different shape is denied.

## Self-Healing Policy

- `enabled`
- `triggers`
- `createMode`
- `templateRef`
- `authorityMode`
- `maxActiveRemediations`
- `allowNestedRemediation`
- `maxDepth`

Validation:
- Disabled by default.
- Immediate creation and nesting require explicit policy.

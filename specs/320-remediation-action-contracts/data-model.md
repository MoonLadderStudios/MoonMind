# Data Model: Remediation Action Contracts

**Traceability**: Jira issue `MM-620`; feature path `specs/320-remediation-action-contracts`.

## Remediation Action Registry Entry

Represents one typed action that may be visible to a remediation task.

Fields:

- `actionKind`: stable typed action identifier.
- `targetType`: target class the action may operate on.
- `inputMetadata`: bounded action input schema/metadata.
- `riskTier`: `low`, `medium`, or `high`.
- `preconditions`: named checks that must pass before authorization or execution.
- `idempotency`: rule describing duplicate request handling.
- `verificationRequired`: whether execution requires post-action verification.
- `verificationHint`: bounded user-safe verification guidance.
- `auditPayloadShape`: compact metadata shape expected in audit output.

Validation rules:

- Raw host, database, Docker, volume, network, secret-reading, and redaction-bypass actions must not be listed.
- Entries are returned only when enabled and compatible with caller permissions, security profile, target evidence, and action policy.
- Returned metadata is immutable from the caller perspective; callers cannot mutate catalog state by editing a response.

## Remediation Action Request Evidence

Durable v1 record of an action request before any side effect is authorized.

Fields:

- `schemaVersion`: `v1`.
- `actionId`: stable action/request identifier, normally tied to the idempotency key.
- `actionKind`: typed action identifier.
- `requester`: user, workflow, or service principal requesting the action.
- `target`: target workflow/run/resource identity and resource kind.
- `riskTier`: evaluated risk tier.
- `dryRun`: whether side effects are prohibited for this request.
- `idempotencyKey`: duplicate protection key.
- `params`: redacted bounded action parameters.

Validation rules:

- Required identity fields must be non-empty.
- `params` must not contain raw secrets, raw local paths, storage-local handles, or presigned URLs.
- Request shape must be stable for idempotency decisions.
- Request evidence must be published before a side effect is attempted.

## Remediation Action Result Evidence

Durable v1 record of an action decision or execution outcome.

Fields:

- `schemaVersion`: `v1`.
- `actionId`: action/request identifier.
- `actionKind`: typed action identifier.
- `status`: one of `applied`, `no_op`, `rejected`, `precondition_failed`, `approval_required`, `timed_out`, or `failed`.
- `message`: bounded user-safe result message.
- `appliedAt`: timestamp when a side effect was applied, otherwise absent/null.
- `beforeStateRef`: optional artifact/reference to pre-action state.
- `afterStateRef`: optional artifact/reference to post-action state.
- `verificationRequired`: boolean flag for post-action verification.
- `verificationHint`: bounded verification guidance when verification is required.
- `sideEffects`: redacted bounded summary of side effects.

Validation rules:

- Unsupported statuses fail closed before durable publication.
- `verificationHint` is required when `verificationRequired` is true.
- `appliedAt` is present only when a side effect was actually applied.
- Side effects are compact descriptions, not raw command output or administrative handles.

## Action Policy

Policy context used to decide action availability and execution.

Fields:

- `allowedActions`: typed actions that may be listed or requested.
- `approvalRules`: risk and action-specific approval requirements.
- `verificationRules`: actions requiring verification.
- `retryBudget`: total and per-action attempt limits.
- `cooldown`: time bounds between repeat attempts.
- `locking`: mutation lock scope and mode.
- `nesting`: nested remediation restrictions.

Validation rules:

- Policy cannot enable raw action kinds.
- Missing or unsupported policy values fail with bounded denial rather than fallback execution.
- High-risk policy must be reflected in action decisions before side effects.

## State Transitions

```text
listed -> requested -> authority_decided -> guard_decided -> executed_or_denied -> result_recorded -> verification_recorded
```

Rules:

- `requested` cannot proceed to side effects without executable authority and guard decisions.
- `approval_required`, `rejected`, `precondition_failed`, `timed_out`, and `failed` are terminal for that action attempt unless a new request with valid idempotency context is submitted.
- Duplicate idempotency keys may return prior compatible decisions but must not authorize a different action shape.

# Contract: Remediation Action Contracts

**Traceability**: Jira issue `MM-620`; feature path `specs/320-remediation-action-contracts`.

## List Allowed Actions

Intent: return the typed actions a remediation task may request for the current target and policy.

Inputs:

- `remediationWorkflowId`
- caller permissions/principal
- security profile or action policy context
- pinned target workflow/run evidence

Output: ordered collection of action registry entries.

Each entry includes:

- `actionKind`
- `targetType`
- `inputMetadata`
- `riskTier`
- `preconditions`
- `idempotency`
- `verificationRequired`
- `verificationHint`
- `auditPayloadShape`

Rules:

- Return only enabled, policy-compatible typed actions.
- Do not include raw host, database, Docker, volume, network, secret-reading, or redaction-bypass actions.
- Omit unsupported actions or return a bounded denial through the request path; never expose raw fallback instructions.

## Evaluate Action Request

Intent: decide whether one typed action request may proceed.

Inputs:

- `remediationWorkflowId`
- `actionKind`
- `parameters`
- `dryRun`
- `idempotencyKey`
- `requestingPrincipal`
- permissions
- security profile
- approval reference when available
- current target evidence/freshness guard

Output: authority/guard decision plus v1 request evidence.

Required decision outcomes:

- `executable`
- `approval_required`
- `rejected`
- `precondition_failed`

Rules:

- Validate action kind, target type, authority, policy, inputs, preconditions, risk, dry-run, idempotency, and current target evidence before any side effect.
- High-risk actions require policy-compatible approval before execution.
- Dry-run requests cannot claim side effects were applied.
- Unsupported raw operation classes fail before side effects.
- Redact secrets, local paths, presigned URLs, storage keys, and raw administrative handles.

## Publish Action Request Evidence

Intent: durably preserve the request that led to an executable, denied, approval-required, or precondition-failed decision.

Artifact type: `remediation.action_request`.

Required payload fields:

- `schemaVersion: v1`
- `actionId`
- `actionKind`
- `requester`
- `target`
- `riskTier`
- `dryRun`
- `idempotencyKey`
- `params`

Rules:

- Publish before invoking any side-effecting executor.
- Keep payload bounded and redacted.
- Preserve enough identity for audit without exposing unauthorized target details.

## Publish Action Result Evidence

Intent: durably preserve the outcome of an action request or execution attempt.

Artifact type: `remediation.action_result`.

Allowed statuses:

- `applied`
- `no_op`
- `rejected`
- `precondition_failed`
- `approval_required`
- `timed_out`
- `failed`

Required payload fields:

- `schemaVersion: v1`
- `actionId`
- `actionKind`
- `status`
- `message`
- `appliedAt` when applicable
- `beforeStateRef`
- `afterStateRef`
- `verificationRequired`
- `verificationHint` when verification is required
- `sideEffects`

Rules:

- Unsupported statuses fail closed before publication.
- `verificationRequired` and `verificationHint` must match action policy and result status.
- `sideEffects` must be redacted bounded summaries, not raw output or handles.
- A separate verification artifact may provide details, but the result artifact still declares verification requirements.

## Verification Evidence

Intent: prove the target state after an action when verification is required.

Artifact type: `remediation.verification`.

Rules:

- Verification references must be linked to the action ID and action kind.
- Failed or unavailable verification must be represented explicitly.
- Verification evidence must remain bounded and redacted.

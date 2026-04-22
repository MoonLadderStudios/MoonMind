# Data Model: Remediation Authority Boundaries

## Remediation Authority Decision

Represents the result of evaluating whether a remediation action request may proceed.

Fields:

- `remediation_workflow_id`: Workflow ID of the remediation execution.
- `target_workflow_id`: Workflow ID of the linked target execution.
- `authority_mode`: One of `observe_only`, `approval_gated`, or `admin_auto`.
- `action_kind`: Typed remediation action kind.
- `risk`: `low`, `medium`, or `high`.
- `decision`: `allowed`, `approval_required`, `dry_run_only`, or `denied`.
- `reason`: Stable reason code for the decision.
- `idempotency_key`: Deterministic caller-provided key for duplicate suppression.
- `security_profile_ref`: Named profile used for elevated actions when applicable.
- `approval_ref`: Approval evidence identifier when execution is approved.
- `audit`: Compact redacted audit payload.
- `redacted_parameters`: Action parameters after secret and raw-access redaction.

Validation rules:

- `action_kind` is required and must be in the allowlisted policy catalog.
- `idempotency_key` is required for executable side-effecting actions.
- `observe_only` can never produce an executable `allowed` decision.
- `approval_gated` requires `approval_ref` for side-effecting execution.
- `admin_auto` requires a permitted security profile for side-effecting execution.
- High-risk actions require approval unless policy disables them entirely.
- Denied and approval-required decisions must include a reason.
- Audit payloads must not contain raw secrets, presigned URLs, storage keys, absolute local paths, or secret-bearing config bundles.

## Remediation Permission Set

Compact caller authority used for the decision.

Fields:

- `can_view_target`: Caller can view the target execution.
- `can_create_remediation`: Caller can create basic remediation tasks.
- `can_request_admin_profile`: Caller can request an elevated security profile.
- `can_approve_high_risk`: Caller can approve high-risk action execution.
- `can_inspect_audit`: Caller can inspect privileged remediation audit history.

Validation rules:

- Target view alone does not imply any other permission.
- Admin profile use requires `can_request_admin_profile`.
- High-risk approval requires `can_approve_high_risk`.
- Audit inspection requires `can_inspect_audit`.

## Remediation Security Profile

Named privileged execution identity for elevated remediation actions.

Fields:

- `profile_ref`: Stable profile identifier.
- `execution_principal`: Principal recorded in audit output.
- `allowed_action_kinds`: Action kinds this profile may execute.
- `enabled`: Whether this profile may currently be used.

Validation rules:

- Elevated action execution requires an enabled profile.
- The profile must allow the requested action kind.
- Audit output must include both the requestor and `execution_principal`.

## Remediation Action Policy

Named policy that maps action kinds to risk and approval behavior.

Fields:

- `policy_ref`: Stable policy identifier.
- `allowed_actions`: Mapping of action kind to risk and enabled state.
- `auto_allowed_risk`: Highest risk level allowed without approval.
- `disabled_actions`: Action kinds blocked regardless of authority mode.

Validation rules:

- Unknown policies fail closed.
- Unknown or disabled action kinds fail closed.
- Risk values outside `low`, `medium`, and `high` fail closed.
- High-risk actions cannot be made silently executable by `admin_auto`.

## Remediation Action Audit

Redacted review evidence for a remediation action decision.

Fields:

- `requesting_principal`: User or workflow that requested the action.
- `execution_principal`: Security profile principal used for execution, if any.
- `remediation_workflow_id`: Remediation execution.
- `target_workflow_id`: Target execution.
- `authority_mode`: Evaluated authority mode.
- `action_kind`: Requested action.
- `risk`: Evaluated risk.
- `decision`: Decision result.
- `reason`: Reason code.
- `approval_ref`: Approval evidence when present.
- `idempotency_key`: Duplicate-suppression key.
- `summary`: Redacted bounded summary.

Validation rules:

- Audit is emitted for allowed, approval-required, dry-run-only, and denied decisions.
- Audit includes requestor and execution principal for privileged actions.
- Audit summaries are bounded and redacted.

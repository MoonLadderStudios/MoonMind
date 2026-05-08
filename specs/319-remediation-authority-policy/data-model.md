# Data Model: Remediation Authority Policy

## Remediation Authority Mode

- Values: `observe_only`, `approval_gated`, `admin_auto`.
- Validation: unsupported values fail closed during remediation creation or action evaluation.
- Relationships: stored with a remediation link and used by action authority evaluation.

## Remediation Permission Set

- Fields:
  - `can_view_target`
  - `can_create_remediation`
  - `can_request_admin_profile`
  - `can_approve_high_risk`
  - `can_inspect_audit`
- Validation: privileged actions require the specific permission for the requested capability.
- Visibility rule: lack of target visibility prevents capabilities and action execution.

## Remediation Security Profile

- Fields:
  - `profile_ref`
  - `execution_principal`
  - `allowed_action_kinds`
  - `enabled`
- Validation: privileged execution requires an enabled profile that allows the requested action kind.
- Audit rule: allowed privileged decisions record both requester and effective execution principal.

## Remediation Action Decision

- Fields:
  - remediation workflow and target workflow refs
  - authority mode
  - action kind and risk
  - decision and reason
  - executable flag
  - idempotency key
  - security profile ref
  - approval ref
  - redacted parameters
  - bounded audit output
- Validation:
  - observe-only side-effect requests are denied unless dry-run.
  - approval-gated side-effect requests require approval.
  - high-risk actions require approval and approval permission.
  - raw operations are denied by policy.
- Serialization: outputs are compact, deterministic, and redacted.

## Remediation Audit Output

- Fields:
  - requesting principal
  - execution principal
  - remediation workflow ref
  - target workflow ref when authorized
  - action kind
  - risk tier
  - decision and reason
  - timestamp
- Validation:
  - no raw secrets, authorization headers, local filesystem paths, storage keys, or presigned URLs.
  - unauthorized target existence is not revealed in summaries.

## State Transitions

- `observe_only` + dry-run: `dry_run_only`, not executable.
- `observe_only` + side effect: `denied`, not executable.
- `approval_gated` + no approval: `approval_required`, not executable.
- `approval_gated` + valid approval/profile/permissions: `allowed`, executable.
- `admin_auto` + medium-or-lower risk/profile/permissions: `allowed`, executable.
- `admin_auto` + high risk without approval: `approval_required`, not executable.
- Raw or unsupported action: `denied`, not executable.

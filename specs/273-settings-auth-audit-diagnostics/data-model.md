# Data Model: Settings Authorization Audit Diagnostics

## Settings Permission

- `name`: stable permission string.
- `description`: operator-readable capability summary.
- `actions`: settings API actions requiring the permission.

Validation rules:
- Unknown permissions do not grant access.
- Superuser/local admin mode may grant all permissions through server-side policy, not client-provided metadata.

## Settings Audit Event

- `id`: audit event identifier.
- `event_type`: settings event category.
- `key`: setting key.
- `scope`: setting scope.
- `workspace_id`: workspace subject.
- `user_id`: user subject for user-scope overrides.
- `actor_user_id`: actor when available.
- `old_value_json`: permitted old value or null when redacted/not stored.
- `new_value_json`: permitted new value or null when redacted/not stored.
- `redacted`: whether values were withheld by policy.
- `reason`: user/operator reason when supplied.
- `request_id`: request/source metadata when available.
- `validation_outcome`: validation result summary when available.
- `apply_mode`: apply/reload mode when available.
- `affected_systems`: bounded list of affected systems when available.
- `created_at`: creation timestamp.

Validation rules:
- Raw secrets, OAuth state, private keys, token-like values, sensitive generated config, provider-returned sensitive diagnostics, and descriptor-redacted values are not exposed in read models.
- SecretRef metadata is security-relevant and visible only when authorized by policy.

## Settings Diagnostic

- `code`: stable diagnostic code.
- `message`: sanitized actionable explanation.
- `severity`: info, warning, or error.
- `details`: sanitized structured metadata.

Validation rules:
- Diagnostics must not include raw secret values or alternate sensitive-source fallbacks.
- Launch-readiness blockers identify missing dependency category without exposing plaintext.

## Redaction Decision

- `redacted`: boolean indicating whether a field was withheld.
- `reason`: descriptor policy, secret-like value, caller permission, or unsupported sensitive source.
- `visible_value`: sanitized value or null.

State transitions:
- `visible` -> `redacted` when descriptor policy or caller permissions require withholding.
- `visible` -> `redacted` when value scanning identifies secret-like content.

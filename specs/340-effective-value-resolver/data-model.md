# Data Model: Effective Value Resolver With Source Explanation and Operator Locks

## Effective Setting Value

Purpose: Represents the fully explained value for one setting in one request context.

Fields:

- `key`: stable setting key.
- `scope`: requested scope.
- `value`: resolved value, or null when the value is intentionally null or unavailable.
- `source`: canonical source label from `default`, `config_file`, `environment`, `workspace_override`, `user_override`, `provider_profile`, `secret_ref`, or `operator_lock`.
- `source_explanation`: human-readable explanation for why this source won.
- `default_value`: catalog default when one exists.
- `inheritance_state`: whether the value is inherited, overridden, intentionally null, locked, missing, blocked, or invalid.
- `read_only`: whether the returned descriptor/effective value is read-only for the current editor class.
- `read_only_reason`: populated when `read_only` is true because of an operator lock or other policy.
- `apply_mode`: when the value takes effect.
- `activation_state`: whether the current value is active, pending activation, or blocked.
- `pending_value`: value awaiting reload/restart when applicable.
- `requires_reload`, `requires_worker_restart`, `requires_process_restart`: activation requirements.
- `affected_process_or_worker`: affected dependent systems in display-ready form.
- `value_version`: version of the winning override or baseline.
- `diagnostics`: zero or more resolution diagnostics.

Validation rules:

- Source labels must use the canonical vocabulary and should not introduce compatibility aliases for internal labels.
- SecretRef values are references only; plaintext is never included.
- Provider-profile values identify profile references or diagnostics only; profile internals are not embedded.
- Operator-locked values must set `source=operator_lock`, `read_only=true`, and a non-empty `read_only_reason` for non-operator editors.

## Resolution Candidate

Purpose: Internal candidate considered by the resolver before choosing the effective value.

Fields:

- `source`: candidate source label.
- `value`: candidate value.
- `scope`: source scope when applicable.
- `version`: override version when applicable.
- `available`: whether the candidate can be used.
- `diagnostic`: reason the candidate is missing, invalid, blocked, or unresolvable.

Precedence:

- Ordinary chain: built-in default < config file / environment default < workspace override < user override.
- Operator-locked chain: built-in default < workspace override < user override < operator lock.
- Missing, blocked, invalid, or unresolvable winning candidates produce explicit diagnostics instead of silent fallback.

## Operator Lock

Purpose: Represents a deployment or policy-enforced value that ordinary editors cannot override.

Fields:

- `key`: setting key.
- `value`: enforced value.
- `reason`: operator-visible reason for the lock.
- `source`: policy, deployment config, environment, or runtime safety constraint.
- `applies_to`: affected dependent systems.

Validation rules:

- Operator lock wins over user and workspace overrides.
- Non-operator outputs are read-only with a populated reason.
- Ordinary user/workspace writes cannot overwrite the lock.

## Resolution Diagnostic

Purpose: Explains missing, null, blocked, invalid, or unresolvable states.

Required diagnostic categories:

- `no_default`: no built-in, config, environment, workspace, user, or operator value exists.
- `inherited_null`: inherited value is null.
- `intentional_null_override`: a stored override intentionally sets null.
- `unresolved_secret_ref`: a SecretRef cannot be resolved at the relevant boundary.
- `provider_profile_not_found` or equivalent missing-provider-profile diagnostic.
- `policy_blocked`: policy blocks the winning value.
- `post_migration_invalid`: migrated or stored value is invalid for the current descriptor.

Validation rules:

- Diagnostics must be actionable and secret-safe.
- Different categories must not collapse into one generic fallback message.
- Diagnostics must preserve enough details for operators to remediate without exposing secrets.

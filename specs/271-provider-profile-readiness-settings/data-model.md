# Data Model: Provider Profile Management and Readiness in Settings

## ProviderProfile

Existing durable resource stored in `managed_agent_provider_profiles`.

Relevant fields:
- `profile_id`: stable identifier used by settings, runtime routing, and manager payloads.
- `runtime_id`: managed runtime such as `codex_cli` or `claude_code`.
- `provider_id` / `provider_label`: provider identity and display label.
- `default_model` / `model_overrides`: model intent for the runtime/provider pair.
- `credential_source`: `oauth_volume`, `secret_ref`, or `none`.
- `runtime_materialization_mode`: `oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, or `composite`.
- `secret_refs`: role-to-SecretRef mapping.
- `volume_ref`, `volume_mount_path`, `account_label`: OAuth-backed credential volume metadata.
- `max_parallel_runs`, `cooldown_after_429_seconds`, `rate_limit_policy`, `max_lease_duration_seconds`: execution policy metadata.
- `enabled`, `is_default`, `tags`, `priority`: routing and operator-visible metadata.
- `command_behavior`: runtime strategy metadata, including provider-specific auth/readiness diagnostics.

Validation rules:
- `profile_id`, `runtime_id`, and `provider_id` are required on create.
- `secret_refs` values must parse as SecretRefs.
- Codex OAuth profiles require both `volume_ref` and `volume_mount_path`.
- Browser-visible payloads redact secret-like runtime fields.

## ProviderProfileReadiness

New response-only diagnostic object. It is not persisted.

Fields:
- `status`: `ready`, `warning`, or `blocked`.
- `launch_ready`: boolean.
- `summary`: short sanitized status.
- `checks`: ordered list of readiness checks.

Readiness checks:
- `schema`: required profile fields and enum-valid profile shape.
- `required_fields`: runtime/provider/materialization fields required for launch intent.
- `secret_refs`: SecretRef syntax and managed secret existence/status when the backend can inspect it.
- `oauth_volume`: OAuth volume metadata presence for OAuth-backed profiles.
- `provider_validation`: provider-specific validation state, including Claude auth readiness when present.
- `enabled`: enabled/disabled state.
- `concurrency`: configured capacity is positive.
- `cooldown`: cooldown policy/state metadata is known and non-negative.

State transitions:
- Any error severity check makes `status=blocked` and `launch_ready=false`.
- Warning-only checks make `status=warning` and `launch_ready=true`.
- All passing checks make `status=ready` and `launch_ready=true`.

## ProviderReadinessCheck

Fields:
- `id`: stable check identifier.
- `label`: operator-visible label.
- `status`: `pass`, `warning`, or `error`.
- `message`: sanitized operator-facing explanation.

## SecretRefRoleBinding

Existing JSON mapping from required role name to SecretRef.

Validation rules:
- Role names are object keys and must be strings.
- Values must be SecretRef strings.
- The UI may display role names and SecretRef strings but must not display plaintext.

## LaunchBlockerDiagnostic

Diagnostic emitted when a profile cannot be used for launch.

Fields:
- `profile_id`
- `reason_code`
- `message`
- `readiness_check_id`

This story models blocker causes through `ProviderProfileReadiness`; runtime strategies still own final launch behavior.

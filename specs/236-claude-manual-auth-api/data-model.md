# Data Model: Claude Manual Auth API

## Claude Manual Auth Commit Request

Represents the operator-submitted manual token enrollment payload.

Fields:
- `token`: Required secret-bearing string. It is validated and then stored only in Managed Secrets.
- `account_label`: Optional operator-facing label to apply to the provider profile.

Validation rules:
- `token` must be present and non-blank.
- `token` must match the accepted Claude manual token shape before upstream validation is attempted.
- Request handling must not log or return the token.

## Managed Claude Token Secret

Represents the durable encrypted credential record created or updated by a successful commit.

Fields:
- `slug`: Stable slug derived from the provider profile id and suitable for a `db://` secret reference.
- `ciphertext`: Encrypted submitted token material.
- `status`: Active when the commit succeeds.
- `details`: Non-secret metadata such as provider profile id, runtime id, provider id, auth strategy, and validation timestamp.

Relationships:
- Referenced by the provider profile through `secret_refs.anthropic_api_key`.

Validation rules:
- Existing active secret records for the same slug are updated rather than creating duplicate launch bindings.
- Secret metadata must not contain raw token material.

## Claude Anthropic Provider Profile Binding

Represents the launch profile state after a successful commit.

Fields:
- `credential_source`: `secret_ref`.
- `runtime_materialization_mode`: `api_key_env`.
- `volume_ref`: Empty after conversion.
- `volume_mount_path`: Empty after conversion.
- `secret_refs`: Contains `anthropic_api_key` pointing to the managed secret reference.
- `clear_env_keys`: Includes conflicting Anthropic/OpenAI environment keys that must be removed before launch materialization.
- `env_template`: Binds the resolved `anthropic_api_key` secret to the Anthropic runtime environment variable.
- `command_behavior`: Contains non-secret manual-auth state, supported actions, status label, and readiness metadata.

Validation rules:
- Profile mutation is allowed only for authorized callers managing Claude Code Anthropic profiles.
- The profile row must never contain raw token material.
- Runtime-visible payloads must carry only secret references and non-secret readiness metadata.

## Claude Manual Auth Readiness Metadata

Represents the secret-free result returned to Mission Control and stored in provider profile behavior metadata.

Fields:
- `connected`: Whether the commit established a usable binding.
- `last_validated_at`: Timestamp of successful validation.
- `backing_secret_exists`: Whether the managed secret was written or updated.
- `launch_ready`: Whether the profile is ready for runtime launch.
- `failure_reason`: Optional redacted failure detail; absent on success.

Validation rules:
- Readiness responses must not include token material.
- Failure text must remain generic or redacted.

## State Transitions

1. Existing profile may start as volume-backed (`oauth_volume` / `oauth_home`).
2. Commit request is authorized and token shape is checked.
3. Upstream validation succeeds.
4. Managed Secret is created or updated and marked active.
5. Provider profile is converted to `secret_ref` / `api_key_env`.
6. Provider profile manager receives a sync signal with secret-reference profile data.
7. Mission Control receives a secret-free ready response.

Failure transitions:
- Authorization failure stops before token validation or mutation.
- Unsupported profile failure stops before token validation or mutation.
- Malformed token failure stops before upstream validation or mutation.
- Upstream validation failure stops before successful profile binding and returns a secret-free error.

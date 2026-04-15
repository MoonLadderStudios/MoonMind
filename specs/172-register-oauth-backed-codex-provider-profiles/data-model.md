# Data Model: Register OAuth-backed Codex Provider Profiles

## ManagedAgentOAuthSession

- `runtime_id`: `codex_cli`
- `profile_id`: target Provider Profile identifier.
- `volume_ref`: durable auth volume ref, default `codex_auth_volume`.
- `volume_mount_path`: enrollment and verification mount path, default `/home/app/.codex`.
- `metadata_json.provider_id`: compact provider id, default `openai`.
- `metadata_json.provider_label`: compact display label, default `OpenAI`.
- `status`: fails deterministically when verification fails.
- `failure_reason`: bounded reason string, no credential contents.

## ManagedAgentProviderProfile

- `runtime_id`: `codex_cli`
- `provider_id`: `openai`
- `credential_source`: `oauth_volume`
- `runtime_materialization_mode`: `oauth_home`
- `volume_ref`: durable auth volume ref.
- `volume_mount_path`: enrollment and verification path.
- `max_parallel_runs`, `cooldown_after_429_seconds`, `rate_limit_policy`: copied from session metadata.
- No raw credentials are stored.


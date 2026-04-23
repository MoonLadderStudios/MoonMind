# Data Model: Claude OAuth Verification and Profile Registration

## Claude OAuth Session

- `session_id`: identifies the OAuth finalization target.
- `runtime_id`: must be `claude_code` for this story.
- `profile_id`: expected profile target, normally `claude_anthropic`.
- `volume_ref`: auth volume reference, normally `claude_auth_volume`.
- `volume_mount_path`: mounted Claude home path, normally `/home/app/.claude`.
- `status`: finalization accepts `awaiting_user` or `verifying`; success moves to `succeeded`; failed verification moves to `failed`.
- `requested_by_user_id`: owner used for finalize authorization.
- `metadata_json`: provider metadata and rate-limit defaults used during profile registration.

Validation rules:

- Finalization must reject sessions not owned by the current operator.
- Finalization must reject sessions outside allowed finalization states.
- Failed verification must not mutate provider profiles.

## Claude Auth Verification Result

- `verified`: boolean.
- `status`: `verified` or `failed`.
- `runtime_id`: `claude_code`.
- `reason`: compact reason such as `ok`, `no_credentials_found`, `settings_not_qualified`, `docker_not_available`, or `verification_error`.
- `credentials_found_count`: count of accepted credential artifacts.
- `credentials_missing_count`: count of expected missing artifacts.

Validation rules:

- Results must not contain credential contents, token values, raw settings values, raw directory listings, or environment dumps.
- `credentials.json` counts as accepted account-auth material when present.
- `settings.json` counts only when it contains documented evidence that Claude account setup completed.

## OAuth-backed Provider Profile

- `profile_id`: `claude_anthropic`.
- `runtime_id`: `claude_code`.
- `provider_id`: `anthropic`.
- `provider_label`: `Anthropic`.
- `credential_source`: `oauth_volume`.
- `runtime_materialization_mode`: `oauth_home`.
- `volume_ref`: `claude_auth_volume`.
- `volume_mount_path`: `/home/app/.claude`.
- `enabled`: true after successful finalization.

Validation rules:

- Profile registration or update happens only after verified auth volume metadata.
- Provider profile rows store refs and metadata only, never credential file contents.
- Provider Profile Manager sync runs for `claude_code` after registration or update succeeds.

## State Transitions

```text
awaiting_user/verifying
  -> verify auth volume
  -> failed when verification fails
  -> register/update claude_anthropic when verification succeeds
  -> sync Provider Profile Manager for claude_code
  -> succeeded
```

# Contract: Codex OAuth Provider Profile Registration

## Create OAuth Session

Request values:

- `runtime_id`: required, `codex_cli` for this story.
- `profile_id`: required.
- `volume_ref`: optional; defaults to `codex_auth_volume` for Codex.
- `volume_mount_path`: optional; defaults to `/home/app/.codex` for Codex.
- `provider_id`: optional; defaults to `openai` for Codex.
- `provider_label`: optional; defaults to `OpenAI` for Codex.
- `account_label`: required.

Persisted session values must contain refs and compact metadata only.

## Finalize OAuth Session

Finalization must:

1. Verify durable auth-volume credentials for `runtime_id`, `volume_ref`, and `volume_mount_path`.
2. Fail the OAuth session when verification returns false or cannot run.
3. Register or update a Provider Profile only after successful verification.
4. Persist the Provider Profile as `oauth_volume` plus `oauth_home`.

Finalization must not return, log, or persist raw credential file content.


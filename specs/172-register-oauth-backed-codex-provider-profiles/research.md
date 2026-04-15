# Research: Register OAuth-backed Codex Provider Profiles

## Existing State

- OAuth sessions are represented by `ManagedAgentOAuthSession` and can carry `volume_ref`, `volume_mount_path`, status, and compact metadata.
- Provider Profiles already support `credential_source=oauth_volume`, `runtime_materialization_mode=oauth_home`, `volume_ref`, and `volume_mount_path`.
- The Temporal workflow path verifies before invoking `oauth_session.register_profile`.
- The API `finalize` endpoint duplicated registration logic and treated verification as best-effort.
- Codex verifier paths checked `.codex/auth.json` beneath the mounted path, which is wrong when the volume itself is mounted at `/home/app/.codex`.

## Decisions

- Keep OAuth session metadata compact and provider-profile-shaped.
- Fail finalization when durable auth-volume verification fails or cannot execute.
- Include root-level Codex auth files in verification paths.
- Do not introduce new tables or artifact records.

## Alternatives Rejected

- **Continue best-effort API verification**: rejected because it can register unverified OAuth-backed profiles.
- **Store fingerprint details or credential content**: rejected because design requires credential contents stay out of payloads, artifacts, logs, and UI responses.
- **Move auth volume into task state**: rejected because auth volumes are provider-profile credential stores, not task workspaces.


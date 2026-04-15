# Contract: Managed Codex Auth Materialization

## Launch Payload

The adapter sends `agent_runtime.launch_session` a payload with:

- `request.codexHomePath`: per-run task workspace Codex home
- `request.environment.MANAGED_AUTH_VOLUME_PATH`: explicit auth target when the selected OAuth-backed profile has a durable auth volume target
- `profile.profileId`: selected Provider Profile ID
- `profile.credentialSource`: `oauth_volume`
- `profile.runtimeMaterializationMode`: `oauth_home`

Raw credential contents are forbidden in both `request` and `profile`.

## Launcher Boundary

The managed session controller must:

- mount the shared workspace volume at the configured workspace root
- mount the durable auth volume only when `MANAGED_AUTH_VOLUME_PATH` is present
- mount the durable auth volume at exactly `MANAGED_AUTH_VOLUME_PATH`
- reject `MANAGED_AUTH_VOLUME_PATH == codexHomePath`
- pass reserved session path/control environment values into the container

## Runtime Boundary

The in-container Codex session runtime must:

- require workspace, session state, artifact spool, image, and Codex home environment values
- create the per-run Codex home when needed
- reject an auth volume path equal to the per-run Codex home
- copy eligible auth entries one way from `MANAGED_AUTH_VOLUME_PATH` into the per-run Codex home
- start Codex App Server with `CODEX_HOME` set to the per-run Codex home

# Data Model: Launch Codex Auth Materialization

## Provider Profile

- `profile_id`: selected profile identifier
- `runtime_id`: managed runtime identifier, expected `codex_cli` for this story
- `credential_source`: `oauth_volume` for OAuth-backed profiles
- `runtime_materialization_mode`: `oauth_home` for Codex OAuth home materialization
- `volume_ref`: durable auth volume reference
- `volume_mount_path`: explicit auth target exposed as `MANAGED_AUTH_VOLUME_PATH`

Validation:

- Profile launch metadata must not include credential file contents.
- Auth target metadata is compact path metadata only.

## Managed Session Launch Request

- `taskRunId`
- `sessionId`
- `workspacePath`
- `sessionWorkspacePath`
- `artifactSpoolPath`
- `codexHomePath`
- `imageRef`
- `environment.MANAGED_AUTH_VOLUME_PATH` when a selected profile requires a durable auth source

Validation:

- Workspace, session state, artifact spool, and Codex home paths are absolute task-scoped paths.
- Reserved session environment values are set by the launcher, not overridden by profile environment.
- `MANAGED_AUTH_VOLUME_PATH` must be absolute and must not equal `codexHomePath`.

## Per-Run Codex Home

- Directory under the task workspace, conventionally `.moonmind/codex-home`
- Receives eligible auth entries from the durable auth source
- Used as `CODEX_HOME` for Codex App Server

Validation:

- Directory must be writable.
- Durable auth source is copied one way into this directory.
- Generated config and runtime logs are not overwritten or copied from the durable auth source.

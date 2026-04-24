# Data Model: Claude OAuth Runtime Launch Materialization

## OAuth-backed Claude Provider Profile

- `profile_id`: `claude_anthropic`.
- `runtime_id`: `claude_code`.
- `provider_id`: `anthropic`.
- `credential_source`: `oauth_volume`.
- `runtime_materialization_mode`: `oauth_home`.
- `volume_ref`: `claude_auth_volume`.
- `volume_mount_path`: `/home/app/.claude`.
- `clear_env_keys`: must include `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`.
- `account_label`: operator-visible Claude OAuth label.

Validation rules:

- OAuth-home profiles require non-blank `volume_ref` and `volume_mount_path`.
- Launch must consume the profile as the source of truth rather than reconstructing equivalent values ad hoc.
- Profile metadata may store refs and labels, never raw credential contents.

## Claude Runtime Launch Context

- `execution_profile_ref`: selected profile identifier carried into launch.
- `runtime_id`: `claude_code`.
- `workspace_path`: repo workspace path for task files only.
- `runtime_support_dir`: sidecar runtime support directory under the run root.
- `MANAGED_AUTH_VOLUME_PATH`: non-secret mount target derived from `volume_mount_path`.
- `CLAUDE_HOME`: Claude home path used for OAuth-backed runtime execution.
- `CLAUDE_VOLUME_PATH`: Claude volume path alias used by the runtime.
- `clear_env_keys`: list of ambient auth variables removed before process start.

Validation rules:

- `MANAGED_AUTH_VOLUME_PATH`, `CLAUDE_HOME`, and `CLAUDE_VOLUME_PATH` must resolve to the configured Claude home path for OAuth-backed launch.
- `workspace_path` must remain distinct from the auth-volume path.
- Ambient `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY` must not survive into the final runtime environment when the OAuth-backed profile is selected.

## Managed Session Auth Diagnostics

- `component`: `managed_session_controller`.
- `readiness`: launch/session readiness state.
- `profileRef`: selected profile identifier.
- `runtimeId`: runtime identifier.
- `providerId`: provider identifier.
- `credentialSource`: profile credential source.
- `runtimeMaterializationMode`: profile materialization mode.
- `volumeRef`: auth volume reference.
- `authMountTarget`: sanitized auth-volume mount target.

Validation rules:

- Diagnostics may include compact refs and paths needed for operator debugging.
- Diagnostics must not include raw auth file contents, raw auth file paths beneath the mount root, token values, environment dumps, or directory listings.

## Launch Diagnostics Surface

- Managed runtime stdout/stderr summaries.
- Managed-session metadata and error summaries.
- Workflow history fields and activity diagnostics.
- Artifact metadata related to launch support.

Validation rules:

- Auth-volume contents are never treated as workspace files or publishable artifacts.
- Sanitization applies before any operator-visible error or metadata payload leaves the launch boundary.

## State Transitions

```text
profile selected
  -> provider profile resolved
  -> auth-volume mount target derived
  -> clear_env_keys applied
  -> Claude home environment set
  -> runtime/session launch begins
  -> safe diagnostics emitted
  -> running
```

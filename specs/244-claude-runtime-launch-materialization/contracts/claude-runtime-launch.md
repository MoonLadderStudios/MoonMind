# Contract: Claude OAuth Runtime Launch Materialization

## Claude OAuth-backed Task Launch

Boundary: managed runtime launch and managed-session launch for `runtime_id = "claude_code"` with selected profile `claude_anthropic`.

Preconditions:

- The selected provider profile is `claude_anthropic`.
- The profile stores `credential_source = oauth_volume`.
- The profile stores `runtime_materialization_mode = oauth_home`.
- The profile stores `volume_ref = claude_auth_volume`.
- The profile stores `volume_mount_path = /home/app/.claude`.

Required launch behavior:

- Resolve the selected provider profile before runtime startup.
- Derive a non-secret auth mount target from the profile volume mount path.
- Remove `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY` from the runtime launch environment before `claude_code` starts.
- Set `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH` to `/home/app/.claude` for the Claude OAuth-backed launch path.
- Keep the auth volume separate from the task workspace and artifact-backed paths.

Allowed operator-visible diagnostics:

```json
{
  "component": "managed_session_controller",
  "readiness": "ready",
  "profileRef": "claude_anthropic",
  "runtimeId": "claude_code",
  "providerId": "anthropic",
  "credentialSource": "oauth_volume",
  "runtimeMaterializationMode": "oauth_home",
  "volumeRef": "claude_auth_volume",
  "authMountTarget": "/home/app/.claude"
}
```

Forbidden diagnostic or artifact content:

- Raw credential file contents.
- Token values.
- Environment dumps.
- Raw auth file paths below the mount root.
- Raw directory listings from the auth volume.
- Treating the auth volume as the workspace root or an artifact publication root.

## Unit-Test Contract

Focused unit coverage for MM-481 must prove:

- Claude OAuth-backed launch resolves the intended profile before startup.
- The final launch environment excludes `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`.
- The final launch/session environment includes Claude home variables consistent with `/home/app/.claude`.
- Safe diagnostics report `volumeRef` and `authMountTarget` without leaking sensitive values.
- Workspace and artifact surfaces remain distinct from the auth-volume path.

## Integration-Test Contingency

Run hermetic integration coverage when implementation changes:

- managed runtime launch-to-artifact behavior,
- worker topology or launch activity wiring,
- or any compose-backed seam that unit tests cannot prove safely.

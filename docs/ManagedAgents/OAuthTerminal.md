# OAuth Terminal and Managed Session Auth Volumes

**Replaces:** `docs/ManagedAgents/TmateArchitecture.md`
**Status:** Desired state, Codex-focused current target
**Owners:** MoonMind Engineering
**Last updated:** 2026-05-04

Related:
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)

## 1. Purpose

MoonMind needs a first-party way to enroll OAuth credentials for managed CLI
runtimes and then target the resulting credential volume into managed runtime
containers. This document defines the desired auth-terminal and volume-targeting
contract.

The current concrete managed-session target is **Codex only**. Claude Code and
Gemini CLI can still have auth volumes and provider profiles, but they are not
yet the fully updated task-scoped managed-session plane described here.

For Codex, the important boundary is:

- OAuth or manual auth writes durable credential material into a provider-profile
  auth volume such as `codex_auth_volume`.
- A task-scoped managed Codex session container receives that auth volume only
  through an explicit auth-volume mount target.
- The Codex App Server runs from a per-run `CODEX_HOME` under the shared task
  workspace, not directly from the durable auth volume.
- Logs, summaries, diagnostics, and continuity artifacts remain the durable
  operator/audit truth. Runtime home directories and auth volumes are not
  presentation artifacts.

## 2. Scope

This document covers:

- browser-initiated OAuth or terminal-auth enrollment
- persistent runtime auth volumes
- Provider Profile registration for OAuth-backed profiles
- Codex managed-session volume targeting
- the separation between auth volumes, task workspaces, and workload containers

This document does not define:

- PTY attach for ordinary managed task runs
- Live Logs transport for managed runs
- a generic remote shell product
- Claude/Gemini task-scoped managed-session parity
- Docker-backed workload container auth inheritance

OAuth can require an interactive terminal. Managed Codex task execution should
not. Codex managed sessions use the Codex App Server protocol, with operator
observability through artifacts and normalized session events.

## 3. Current Codex Volume Model

### 3.1 Durable auth volume

The durable Codex OAuth home is a Docker named volume:

- default volume name: `codex_auth_volume`
- configurable compose name: `CODEX_VOLUME_NAME`
- conventional auth path for enrollment/verification: `/home/app/.codex`
- provider profile field: `volume_ref`
- provider profile materialization mode: `oauth_home`

This volume stores reusable auth material for a Codex provider profile. It is a
credential backing store, not a task workspace and not an audit artifact.

The default Codex OAuth provider profile shape is:

```yaml
runtime_id: codex_cli
provider_id: openai
credential_source: oauth_volume
runtime_materialization_mode: oauth_home
volume_ref: codex_auth_volume
volume_mount_path: /home/app/.codex
```

That shape describes the credential enrollment and verification home only. It
does not set `CODEX_HOME` for managed sessions. The managed-session launcher
maps the durable auth volume through the managed-session volume rules below and
keeps the live session `CODEX_HOME` under the task workspace.

### 3.2 Shared task workspace volume

The managed Codex session container receives the shared task workspace volume:

- default volume name: `agent_workspaces`
- configurable compose name: `MOONMIND_AGENT_WORKSPACES_VOLUME_NAME`
- container mount root: `/work/agent_jobs`

The per-task layout under that root is:

- `repo/` for the checked-out task repository
- `session/` for session-local state
- `artifacts/` for artifact spooling
- `.moonmind/codex-home/` for the managed session's per-run Codex home

The per-run `codexHomePath` lives under the workspace volume. It is writable by
the managed-session container user and is the `CODEX_HOME` used by Codex App
Server for that task-scoped session.

### 3.3 Explicit auth-volume target

A Codex managed-session container may receive the durable auth volume at an
explicit, separate target path through `MANAGED_AUTH_VOLUME_PATH`, for example:

```text
MANAGED_AUTH_VOLUME_PATH=/home/app/.codex-auth
```

When that environment value is present, the launcher mounts:

```text
type=volume,src=codex_auth_volume,dst=/home/app/.codex-auth
```

The target path must be an absolute path and must not equal `codexHomePath`.
This prevents the durable auth volume from becoming the live mutable Codex home
for a task session.

At session startup, the Codex session runtime copies eligible auth entries from
`MANAGED_AUTH_VOLUME_PATH` into the per-run `codexHomePath`, then starts Codex
App Server with `CODEX_HOME` pointing at that per-run home.

## 4. Volume Targeting Rules

1. **Auth volumes are provider-profile credential stores.**
   They are referenced by `volume_ref` and verified by OAuth/provider-profile
   workflows. They are not task workspaces.

2. **Managed-session containers always receive the shared task workspace.**
   For Codex, the task workspace volume is the only required managed-session
   mount.

3. **Auth volume mounts are explicit.**
   A managed-session container receives `codex_auth_volume` only when the
   selected provider profile and launcher policy require it.

4. **Auth volume target paths are separate from runtime homes.**
   `MANAGED_AUTH_VOLUME_PATH` must not equal `codexHomePath`.

5. **The per-run Codex home is materialized under the task workspace.**
   Codex App Server uses the per-run `codexHomePath` as `CODEX_HOME`.

6. **Credential copying is one-way for session startup.**
   Eligible auth files may be seeded from the durable auth volume into the
   per-run Codex home. Session-local runtime state must not be treated as the
   source of truth for provider-profile credentials.

7. **Workload containers do not inherit auth volumes by default.**
   Docker-backed workload containers launched from a managed session receive
   only their declared workspace and cache mounts. Any credential mount must be
   explicitly declared and justified by the workload profile.

8. **No raw credentials in workflow history, logs, or artifacts.**
   Workflow payloads may carry compact refs such as `profile_id`, `volume_ref`,
   and mount targets. They must not carry credential file contents.

## 5. OAuth Terminal Contract

MoonMind should use first-party browser terminal infrastructure for interactive
OAuth flows instead of external terminal handoff services.

The desired OAuth terminal architecture is:

```text
Settings / Mission Control UI
  -> OAuth Session API
  -> MoonMind.OAuthSession workflow
  -> short-lived auth runner container
  -> MoonMind PTY/WebSocket bridge
  -> provider terminal page with xterm.js
  -> provider login CLI
  -> mounted auth volume
  -> terminal-page finalization action
  -> verification
  -> Provider Profile registration
```

The OAuth terminal is only for credential enrollment or repair. It does not
become the runtime surface for managed Codex task execution.

### 5.1 Auth runner container

The auth runner container is short-lived and scoped to one OAuth session.

It should:

- mount the target auth volume at the provider's enrollment path
- run the provider bootstrap command inside a PTY
- expose terminal I/O only through MoonMind's authenticated bridge
- stop after success, cancellation, expiry, or failure
- leave credentials in the durable auth volume for later verification

For Codex, the auth runner targets `codex_auth_volume` at `/home/app/.codex`
while enrollment is happening. This is separate from the later managed-session
container target path.

### 5.2 Terminal bridge

The terminal bridge should:

- allocate or attach to the auth runner PTY
- proxy browser input/output over an authenticated WebSocket
- handle resize and heartbeat frames
- enforce session TTL and ownership
- close on workflow completion, cancellation, or expiry
- persist metadata about connections, disconnections, and close reasons

The bridge must not expose generic Docker exec access or ordinary task-run
terminal attachment.

### 5.3 Session transport state

OAuth session state should be transport-neutral:

- `pending`
- `starting`
- `bridge_ready`
- `awaiting_user`
- `verifying`
- `registering_profile`
- `succeeded`
- `failed`
- `cancelled`
- `expired`

Runtime provider registry entries may use `session_transport = "none"` while the
interactive PTY bridge is unavailable or intentionally disabled. When the bridge
is enabled, the transport identifier should be a MoonMind-owned value such as
`moonmind_pty_ws`; provider profile and workflow semantics should not depend on
the old `tmate` URL model.

### 5.4 Provider terminal finalization workflow

The launched provider terminal page should let the operator finish provider
profile setup in the same browser surface where interactive provider auth is
completed. Operators should not have to switch back to the Settings page only to
finalize a profile after using the terminal.

The provider terminal page should own a safe status projection for its OAuth
session and expose session actions that are already valid for the authenticated
actor:

- show the selected provider profile label, runtime, provider, session status,
  expiry, and sanitized failure or success summary
- attach to the PTY/WebSocket bridge when the session is terminal-attachable
- show **Finalize Provider Profile** once the session status indicates the
  provider login has completed or the session is otherwise eligible for
  verification/finalization
- call `POST /api/v1/oauth-sessions/{session_id}/finalize` from that button
- show `verifying` while the finalize endpoint validates durable auth material
  and `registering_profile` while that same endpoint registers or updates the
  Provider Profile
- show the safe registered provider-profile summary on success, with a return to
  Settings or manage-profile action as a convenience rather than a required
  step
- expose Cancel, Retry, or Reconnect actions when the current session state
  allows them

The Settings page may continue to start OAuth sessions, poll session status,
invalidate Provider Profile query data, and offer its own finalize action for
operators who stay on Settings. Finalization is not Settings-only. The terminal
page and Settings page should call the same finalize endpoint and observe the
same session state transitions. The finalize operation owns the transition from
an eligible post-login state into `verifying`, then into `registering_profile`,
then into `succeeded` or `failed`; callers only request finalization and render
the projected session status.

The terminal-page finalize button must be duplicate-click and race safe. A
second finalize request for a session already in `verifying`,
`registering_profile`, or `succeeded` should not create duplicate Provider
Profiles or mutate a different profile. Finalization must still fail safely if
the session has been cancelled, expired, or superseded. The terminal page must
not allow changing `profile_id`, `volume_ref`, `volume_mount_path`, or provider
identity for the active session; those values come from the OAuth session that
Settings created.

## 6. Provider Profile Registration

After OAuth verification succeeds, MoonMind registers or updates a Provider
Profile instead of inventing a parallel auth store.

Finalization may be initiated either from Settings or from the launched provider
terminal page. In both cases the same OAuth session metadata is used, and the
finalize operation only verifies the durable auth volume and registers or
updates the selected Provider Profile. The provider terminal page is a
completion surface for the existing session, not a separate profile editor or a
parallel credential store.

For Codex OAuth, the resulting profile should preserve:

- `runtime_id = "codex_cli"`
- `provider_id = "openai"` or another concrete Codex-supported provider
- `credential_source = "oauth_volume"`
- `runtime_materialization_mode = "oauth_home"`
- `volume_ref = "codex_auth_volume"` or the selected volume name
- `volume_mount_path = "/home/app/.codex"` for auth enrollment/verification
- provider-profile slot policy such as `max_parallel_runs`, cooldown, and lease
  duration

Managed-session launch then resolves the selected profile and applies the
Codex-specific volume targeting rules in this document.

## 7. Managed Codex Session Launch

The Codex managed-session launcher should build the container with these mount
classes:

| Mount | Required | Target | Purpose |
| --- | --- | --- | --- |
| `agent_workspaces` | Yes | `/work/agent_jobs` | task repo, session state, artifact spool, per-run Codex home |
| `codex_auth_volume` | Conditional | `MANAGED_AUTH_VOLUME_PATH`, for example `/home/app/.codex-auth` | source credential material for seeding |
| workload/cache volumes | No | runner-profile-specific | specialized workload containers only |

The launcher must pass these reserved environment values into the session
container:

- `MOONMIND_SESSION_WORKSPACE_PATH`
- `MOONMIND_SESSION_WORKSPACE_STATE_PATH`
- `MOONMIND_SESSION_ARTIFACT_SPOOL_PATH`
- `MOONMIND_SESSION_CODEX_HOME_PATH`
- `MOONMIND_SESSION_CONTROL_URL`

The session runtime then:

1. validates that workspace paths exist under the workspace volume
2. validates that the optional auth-volume path is separate from `codexHomePath`
3. creates the per-run `codexHomePath`
4. seeds eligible auth entries from `MANAGED_AUTH_VOLUME_PATH`
5. starts Codex App Server with `CODEX_HOME = codexHomePath`
6. publishes logs and summaries through MoonMind artifacts and session metadata

## 8. Verification

Credential verification should run at two boundaries:

1. **OAuth/Profile boundary:** verify the durable auth volume before registering
   or updating the Provider Profile.
2. **Managed-session launch boundary:** verify the selected provider profile can
   materialize into the task-scoped session container before marking the session
   ready.

Verification may inspect expected file presence or CLI fingerprint/status
signals, but it must not copy credential contents into workflow payloads,
artifacts, logs, or UI responses.

## 9. Security Model

Only authenticated users with provider-profile management permission can:

- create OAuth sessions
- attach to OAuth terminal WebSockets
- cancel or finalize OAuth sessions
- select or mutate provider profile auth volumes

The browser may see:

- OAuth session status
- terminal I/O generated by the provider login CLI
- timestamps
- failure reason
- registered profile summary

The browser must not receive credential files, token values, environment dumps,
or raw auth-volume listings.

## 10. Operator Behavior

For Codex OAuth enrollment:

1. The operator starts a Codex OAuth session from Settings, Mission Control, or
   an operator helper.
2. MoonMind opens the launched provider terminal page for that session.
3. MoonMind creates or reuses the selected `codex_auth_volume`.
4. The auth runner writes credentials into that volume while the operator
   completes provider login in the terminal.
5. When the session is eligible for completion, the provider terminal page shows
   **Finalize Provider Profile**.
6. The operator finalizes from the terminal page; the finalize endpoint moves
   the session through `verifying` while it checks the durable auth volume.
7. MoonMind enters `registering_profile`, registers or updates the Codex
   Provider Profile, and refreshes any Settings-side profile views that are open.
   Returning to Settings is optional.
8. Later task-scoped Codex managed sessions target that profile and mount the
   auth volume at `MANAGED_AUTH_VOLUME_PATH` when needed.
9. The session runtime seeds the per-run `CODEX_HOME` under `agent_workspaces`
   and starts Codex App Server.

For ordinary task execution, operators should inspect Live Logs, artifacts,
session summaries, diagnostics, and reset/control-boundary artifacts. They should
not inspect terminal scrollback or auth volumes as the execution record.

## 11. Required Boundaries

- OAuth terminal code owns interactive enrollment only.
- Provider Profile code owns credential refs, slot policy, and profile metadata.
- Managed-session controller code owns Codex session container mounts.
- Codex session runtime code owns seeding the per-run Codex home and starting
  Codex App Server.
- Docker workload orchestration owns non-agent workload containers and must not
  implicitly inherit managed-runtime auth volumes.

These boundaries keep auth, session continuity, and workload execution separate
while still allowing the Codex managed-session plane to use durable OAuth
credentials safely.

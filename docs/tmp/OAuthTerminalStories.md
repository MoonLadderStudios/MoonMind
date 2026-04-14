# OAuth Terminal Stories

Last Updated: 2026-04-14

Source design: [`docs/ManagedAgents/OAuthTerminal.md`](../ManagedAgents/OAuthTerminal.md)

## Design Summary

`OAuthTerminal.md` defines the desired auth-terminal and volume-targeting contract
for managed CLI runtimes, with Codex as the current concrete managed-session
target. The design separates durable provider-profile auth volumes from
task-scoped managed-session workspaces, keeps Codex App Server on a per-run
`CODEX_HOME`, reserves interactive terminal transport for OAuth enrollment, and
requires credential refs and session observability to stay bounded, auditable,
and free of raw secrets.

The implementation stories below are ordered so the volume and profile contracts
land before session launch behavior and before the optional first-party terminal
bridge. Each story is independently testable and owns explicit source-design
coverage points.

## Coverage Points

| ID | Type | Source Section | Design Point |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | requirement | Purpose | Provide a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers. |
| DESIGN-REQ-002 | constraint | Purpose, Scope | Codex is the only fully updated task-scoped managed-session target for this contract; Claude/Gemini parity is out of scope. |
| DESIGN-REQ-003 | requirement | Current Codex Volume Model | Treat `codex_auth_volume` as durable provider-profile credential storage, configurable by `CODEX_VOLUME_NAME`, with enrollment path `/home/app/.codex`. |
| DESIGN-REQ-004 | requirement | Current Codex Volume Model | Treat `agent_workspaces` as the required managed-session workspace volume mounted at `/work/agent_jobs`. |
| DESIGN-REQ-005 | state-model | Current Codex Volume Model | Materialize per-task paths under `agent_workspaces`, including repo, session state, artifact spool, and `.moonmind/codex-home`. |
| DESIGN-REQ-006 | constraint | Explicit Auth-Volume Target, Rules | Mount auth volumes into managed Codex sessions only through an explicit `MANAGED_AUTH_VOLUME_PATH`, separate from `codexHomePath`. |
| DESIGN-REQ-007 | requirement | Volume Targeting Rules | Seed eligible auth entries one way from the durable auth volume into the per-run Codex home before starting Codex App Server. |
| DESIGN-REQ-008 | constraint | Volume Targeting Rules, Operator Behavior | Keep managed task execution on Codex App Server, not PTY attach or terminal scrollback. |
| DESIGN-REQ-009 | non-goal | Scope, Required Boundaries | Do not make Docker workload containers inherit managed-runtime auth volumes by default. |
| DESIGN-REQ-010 | security | Volume Targeting Rules, Verification, Security Model | Never place raw credential contents in workflow history, logs, artifacts, or UI responses. |
| DESIGN-REQ-011 | integration | OAuth Terminal Contract | Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, `MoonMind.OAuthSession`, short-lived auth runner, PTY/WebSocket bridge, and `xterm.js`. |
| DESIGN-REQ-012 | requirement | Auth Runner Container | Run a short-lived auth runner container that mounts the auth volume at the provider enrollment path and tears down on success, cancellation, expiry, or failure. |
| DESIGN-REQ-013 | integration | Terminal Bridge | Provide authenticated PTY/WebSocket terminal I/O with resize, heartbeat, TTL, ownership enforcement, and close metadata. |
| DESIGN-REQ-014 | constraint | Terminal Bridge | Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge. |
| DESIGN-REQ-015 | state-model | Session Transport State | Use transport-neutral OAuth statuses and allow `session_transport = "none"` while the interactive bridge is disabled. |
| DESIGN-REQ-016 | integration | Provider Profile Registration | Register or update Provider Profiles after OAuth verification, preserving Codex OAuth fields and slot policy. |
| DESIGN-REQ-017 | integration | Managed Codex Session Launch | Launch managed Codex session containers with required workspace mount, conditional auth-volume mount, and reserved session environment values. |
| DESIGN-REQ-018 | verification | Verification | Verify credentials at both the OAuth/profile boundary and the managed-session launch boundary without leaking credential contents. |
| DESIGN-REQ-019 | observability | Purpose, Operator Behavior | Present Live Logs, artifacts, session summaries, diagnostics, and reset/control-boundary artifacts as execution evidence instead of runtime homes or auth volumes. |
| DESIGN-REQ-020 | architecture | Required Boundaries | Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. |

## Ordered Stories

### Story 1: Codex Auth Volume Profile Contract

Short name: `codex-auth-profile`

Why: Operators need a durable, selectable Codex OAuth profile that points to the
right credential volume without exposing credential contents or confusing auth
storage with task execution state.

Scope:
- Define and enforce the Codex OAuth Provider Profile shape for
  `credential_source = oauth_volume` and `runtime_materialization_mode = oauth_home`.
- Preserve `volume_ref`, `volume_mount_path`, and slot policy fields during
  profile registration/update.
- Keep raw credential file contents out of API responses, workflow payloads,
  logs, and artifacts.

Out of scope:
- Interactive OAuth terminal UI.
- Managed-session container launch.
- Claude/Gemini managed-session parity.

Independent test:
- Create or update a Codex OAuth profile from verified OAuth session data, then
  assert the stored/profile API representation contains only refs and policy
  metadata, not credential contents.

Acceptance criteria:
- Given verified Codex OAuth session data, when the profile registrar runs, then
  a Provider Profile exists with `runtime_id = codex_cli`,
  `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`,
  `volume_ref`, `volume_mount_path`, and slot policy.
- Given profile data is returned through an API or workflow snapshot, then raw
  token values and auth file contents are absent.
- Given a non-Codex runtime profile is processed, then this story does not imply
  task-scoped managed-session parity.

Dependencies: None.

Risks or open questions:
- The exact API response redaction boundary should be covered by router tests and
  provider-profile service tests.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003,
DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020.

Handoff:
Implement the Codex OAuth Provider Profile registration contract so verified
OAuth sessions produce durable profile metadata and refs for later managed
session launch, with no credential contents leaving the auth volume boundary.

### Story 2: Codex Managed Session Volume Targeting

Short name: `codex-volume-targeting`

Why: Managed Codex sessions need credentials from `codex_auth_volume`, but the
live Codex App Server must run from a per-task `CODEX_HOME` under
`agent_workspaces` so session-local state is isolated and audit truth stays in
artifacts.

Scope:
- Mount `agent_workspaces` into every managed Codex session container.
- Conditionally mount `codex_auth_volume` only when `MANAGED_AUTH_VOLUME_PATH`
  is explicitly set by selected profile/launcher policy.
- Reject auth-volume targets that equal `codexHomePath`.
- Pass reserved session environment values into the container.

Out of scope:
- OAuth terminal enrollment flow.
- Credential verification implementation.
- Workload container launches.

Independent test:
- Launch a managed Codex session with and without `MANAGED_AUTH_VOLUME_PATH` and
  inspect the generated Docker command plus validation failures for invalid mount
  targets.

Acceptance criteria:
- Given a managed Codex session launch request, then the Docker command mounts
  `agent_workspaces` at `/work/agent_jobs`.
- Given `MANAGED_AUTH_VOLUME_PATH` is absent, then `codex_auth_volume` is not
  mounted.
- Given `MANAGED_AUTH_VOLUME_PATH` is present, then `codex_auth_volume` is
  mounted at that path and not at `codexHomePath`.
- Given `MANAGED_AUTH_VOLUME_PATH` equals `codexHomePath`, then launch fails
  before creating the container.
- Given a managed-session container starts, then it receives the reserved
  `MOONMIND_SESSION_*` environment values needed by the session runtime.

Dependencies: Story 1 for profile refs and policy inputs.

Risks or open questions:
- If future provider profiles produce dynamic auth mount paths, validation must
  remain fail-fast and path-normalized.

Owned coverage: DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006,
DESIGN-REQ-017, DESIGN-REQ-020.

Handoff:
Implement and test the Codex managed-session launch contract so the workspace
volume is always mounted, the auth volume is opt-in and separate from the live
Codex home, and reserved session environment values remain launcher-owned.

### Story 3: Per-Run Codex Home Seeding

Short name: `codex-home-seeding`

Why: The Codex session runtime must bootstrap from durable OAuth credentials
without treating the auth volume as live runtime state or writing session-local
state back into durable credentials.

Scope:
- Create the per-run `codexHomePath` under the task workspace.
- Copy only eligible auth entries from `MANAGED_AUTH_VOLUME_PATH` into
  `codexHomePath`.
- Start Codex App Server with `CODEX_HOME = codexHomePath`.
- Keep runtime home directories out of operator/audit presentation.

Out of scope:
- Deciding which provider profile is selected.
- OAuth enrollment and profile registration.
- Live Logs UI implementation.

Independent test:
- Run the session runtime with a fake auth-volume directory and assert eligible
  files are copied, excluded files are not copied, and Codex App Server receives
  the per-run `CODEX_HOME`.

Acceptance criteria:
- Given `MANAGED_AUTH_VOLUME_PATH` points to a valid directory, then eligible auth
  entries are copied into `codexHomePath` before Codex App Server starts.
- Given the auth-volume path is missing or not a directory, then startup fails
  with an actionable error.
- Given excluded entries exist in the auth volume, then they are not copied into
  the per-run home.
- Given Codex App Server starts, then its environment uses the per-run
  `CODEX_HOME`, not the durable auth-volume mount.
- Given operators inspect execution evidence, then session summaries,
  diagnostics, logs, and artifacts are the intended surfaces, not runtime homes
  or auth volumes.

Dependencies: Story 2.

Risks or open questions:
- The eligible/excluded file policy must stay aligned with Codex CLI auth layout
  changes.

Owned coverage: DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008,
DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020.

Handoff:
Implement the session-runtime seeding path that copies durable auth material into
a per-run Codex home, starts Codex App Server from that home, and keeps
operator-facing evidence artifact-backed.

### Story 4: OAuth Terminal Enrollment Flow

Short name: `oauth-terminal-flow`

Why: Operators need a first-party browser terminal flow to enroll or repair OAuth
credentials without relying on external terminal handoff services or exposing
generic shell access.

Scope:
- Create OAuth sessions through the API.
- Start a short-lived auth runner container with the target auth volume mounted
  at the provider enrollment path.
- Attach Mission Control through an authenticated PTY/WebSocket bridge rendered
  with `xterm.js`.
- Enforce terminal session TTL, ownership, resize/heartbeat handling, and close
  metadata.
- Tear down the auth runner on success, cancellation, expiry, or failure.

Out of scope:
- Ordinary managed task-run terminal attach.
- Generic Docker exec.
- Codex App Server managed task execution.

Independent test:
- Start an OAuth session with a fake provider bootstrap command, attach through
  the WebSocket protocol, complete/finalize the session, and assert terminal
  metadata, auth runner cleanup, and status transitions.

Acceptance criteria:
- Given an authorized operator starts Codex OAuth enrollment, then MoonMind starts
  an OAuth session and auth runner scoped to the selected volume.
- Given the OAuth session reaches bridge readiness, then Mission Control can
  attach through an authenticated terminal WebSocket.
- Given terminal resize/input/output/heartbeat frames occur, then the bridge
  routes them only to the session PTY and records connection metadata.
- Given the session succeeds, fails, expires, or is cancelled, then the bridge and
  auth runner are closed and no generic Docker exec endpoint remains available.
- Given managed task execution runs later, then it uses Codex App Server rather
  than OAuth terminal transport.

Dependencies: Story 1 for profile refs; Story 5 for status model if implemented
separately.

Risks or open questions:
- Browser terminal security and one-time attach-token behavior need explicit
  negative tests.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011,
DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020.

Handoff:
Implement the first-party OAuth terminal flow for credential enrollment only,
with a short-lived auth runner, authenticated PTY/WebSocket bridge, Mission
Control terminal rendering, and strict non-exposure of ordinary task terminals
or generic Docker exec.

### Story 5: OAuth Session State and Verification Boundaries

Short name: `oauth-state-verify`

Why: OAuth enrollment and managed-session launch need explicit statuses and
verification boundaries so operators can reason about credential readiness
without leaking secrets or conflating terminal transport with profile state.

Scope:
- Use transport-neutral OAuth statuses.
- Allow `session_transport = none` while the interactive bridge is disabled.
- Verify durable auth volume credentials before Provider Profile registration.
- Verify selected profile materialization at managed-session launch.
- Keep verification outputs compact and secret-free.

Out of scope:
- Full terminal UI implementation.
- Codex App Server turn execution.
- Provider-specific auth UX copy.

Independent test:
- Exercise OAuth session success, cancel, expire, and disabled-bridge paths with
  mocked volume verification and assert status transitions plus redacted
  verification outputs.

Acceptance criteria:
- Given an OAuth session runs, then status progresses through transport-neutral
  states such as `pending`, `starting`, `bridge_ready`, `awaiting_user`,
  `verifying`, `registering_profile`, and terminal states.
- Given the PTY bridge is disabled, then `session_transport = none` is valid and
  does not imply tmate URL semantics.
- Given OAuth verification fails, then profile registration is not performed and
  the failure is visible without secret leakage.
- Given managed-session launch selects a profile, then materialization is
  verified before marking the session ready.
- Given verification output is persisted or returned, then it contains compact
  status/failure metadata only.

Dependencies: Story 1; Story 2 for launch-boundary materialization.

Risks or open questions:
- In-flight workflow compatibility may require a versioned cutover if status
  payload shapes change.

Owned coverage: DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016,
DESIGN-REQ-018, DESIGN-REQ-020.

Handoff:
Implement transport-neutral OAuth session state and verification boundaries for
both profile registration and managed-session launch, including disabled-bridge
behavior and secret-free verification results.

### Story 6: Workload Auth-Volume Guardrails

Short name: `workload-auth-guardrails`

Why: Docker-backed workload containers launched from managed Codex sessions must
not accidentally receive Codex, Claude, Gemini, or other managed-runtime auth
volumes just because they were requested by an agent session.

Scope:
- Enforce workload profile mount allowlists.
- Reject implicit managed-runtime auth-volume inheritance.
- Require explicit justification/profile declaration for any credential mount.
- Keep workload containers separate from `session_id`, `session_epoch`,
  `container_id`, `thread_id`, and `active_turn_id` identity.

Out of scope:
- Managed Codex session container launch itself.
- OAuth terminal enrollment.
- Specialized workload runner internals beyond mount policy.

Independent test:
- Launch workload profiles from a simulated managed-session-assisted step and
  assert auth-volume mounts are rejected unless explicitly declared by approved
  workload policy.

Acceptance criteria:
- Given a workload launch is requested from a managed Codex session, then no
  managed-runtime auth volume is inherited by default.
- Given a workload profile declares ordinary workspace/cache mounts, then launch
  proceeds without auth volumes.
- Given a workload profile requests an auth or credential mount without explicit
  approval, then launch is rejected with policy metadata.
- Given a workload container runs, then Mission Control and APIs do not present
  it as the managed Codex session identity.

Dependencies: None, though it complements Story 2.

Risks or open questions:
- Approved credential mount policy should stay narrow enough for future
  workload-specific exceptions.

Owned coverage: DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-020.

Handoff:
Implement workload mount-policy guardrails so non-agent Docker workloads remain
adjacent to, but separate from, managed session identity and do not inherit
provider auth volumes by default.

## Coverage Matrix

| Coverage Point | Owning Stories |
| --- | --- |
| DESIGN-REQ-001 | Story 1, Story 4 |
| DESIGN-REQ-002 | Story 1 |
| DESIGN-REQ-003 | Story 1 |
| DESIGN-REQ-004 | Story 2 |
| DESIGN-REQ-005 | Story 2, Story 3 |
| DESIGN-REQ-006 | Story 2 |
| DESIGN-REQ-007 | Story 3 |
| DESIGN-REQ-008 | Story 3, Story 4 |
| DESIGN-REQ-009 | Story 6 |
| DESIGN-REQ-010 | Story 1, Story 3, Story 5, Story 6 |
| DESIGN-REQ-011 | Story 4 |
| DESIGN-REQ-012 | Story 4 |
| DESIGN-REQ-013 | Story 4 |
| DESIGN-REQ-014 | Story 4 |
| DESIGN-REQ-015 | Story 5 |
| DESIGN-REQ-016 | Story 1, Story 5 |
| DESIGN-REQ-017 | Story 2 |
| DESIGN-REQ-018 | Story 5 |
| DESIGN-REQ-019 | Story 3 |
| DESIGN-REQ-020 | Story 1, Story 2, Story 3, Story 4, Story 5, Story 6 |

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Recommended First Story

Start with **Story 1: Codex Auth Volume Profile Contract**. It establishes the
durable profile refs and secret-free API/workflow boundary that later stories
consume for launch-time mount targeting and verification.

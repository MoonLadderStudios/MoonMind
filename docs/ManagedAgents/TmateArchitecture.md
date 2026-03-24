# Tmate Architecture in MoonMind

**Implementation plan:** [`docs/tmp/TmatePlan.md`](../tmp/TmatePlan.md)  
**Remaining-work tracker:** [`docs/tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md`](../tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md)

Status: **Design Draft**
Owners: MoonMind Engineering

> [!NOTE]
> Auth profile management, OAuth volume details, and profile assignment logic are covered in [AuthProfiles.md](../Security/AuthProfiles.md). The shared `TmateSessionManager` abstraction and low-level session lifecycle are defined in [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md).

---

## 1. Summary

Tmate is integrated into MoonMind as a **first-class tool** that provides terminal multiplexing over SSH. It serves three operational roles within the platform:

1. **Live logging** — every managed agent run is wrapped in a tmate session, enabling real-time log tailing from Mission Control.
2. **Live terminal handoff** — operators can escalate from read-only log viewing to interactive read-write terminal access for debugging or manual intervention.
3. **OAuth authentication** — short-lived tmate sessions inside Docker containers give users an interactive terminal to complete provider OAuth login flows.

The MVP transport for all three use cases is tmate. The architecture standardizes the **session transport**, while provider-specific behavior is limited to the commands that run inside the terminal and the verification logic.

---

## 2. Use Cases

| Use Case | Description | Consumer |
|---|---|---|
| **Runtime wrapping** | Every managed agent run (Gemini, Codex, Claude, Cursor) is wrapped in a tmate session to enable live log tailing and terminal handoff from Mission Control. | `ManagedRuntimeLauncher.launch()` |
| **OAuth sessions** | Short-lived Docker containers with tmate give users an interactive terminal to complete provider OAuth login flows. | `oauth_session_activities.start_auth_runner()` |

Both share identical lifecycle concerns (session creation, readiness detection, endpoint extraction, teardown). The `TmateSessionManager` defined in [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md) §4 is the **target abstraction** that unifies them.

---

## 3. Mission Control Integration

### 3.1 Live Log Tailing

When a managed agent run starts, the worker launches a tmate session wrapping the CLI process. The tmate read-only web endpoint (`web_ro`) is persisted to the `workflow_live_sessions` table and exposed via `GET /api/workflows/{id}/live-session`. The Mission Control dashboard embeds this endpoint in the Live Output panel, giving operators real-time visibility into agent execution without SSH access to the worker.

See [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md) §5 for the endpoint persistence data model and API.

### 3.2 Live Terminal Handoff

Operators can escalate from read-only log viewing to interactive read-write terminal access. The RW endpoint (`web_rw`) is stored encrypted at rest and only revealed via explicit operator grant with a TTL (default: 15 minutes). This enables debugging stuck agents, manually completing interactive prompts, or inspecting runtime state.

See [LiveTaskManagement.md](../Temporal/LiveTaskManagement.md) for the full handoff UX design including operator messages, pause/resume, and session revocation.

### 3.3 OAuth Session UX

In **System Settings → Auth Profiles**, each runtime gets:

* **Connect with OAuth**
* **Reconnect**
* **Check volume**
* **Disable profile**
* **Delete profile**

Clicking **Connect with OAuth** opens a modal:

* runtime: Codex / Gemini / Claude / Cursor
* profile id
* account label
* volume name
* max parallel runs
* cooldown policy
* button: **Start OAuth Session**

After creation, the modal shows:

* session status
* tmate web URL
* optional ssh URL
* session expiration
* provider-specific short instructions
* button: **Open Terminal**
* button: **Cancel Session**
* button: **Refresh Status**

---

## 4. Architecture

### 4.1 Core Idea

A first-class **OAuth Session** layer sits between Mission Control and the auth profile registry (see [AuthProfiles.md](../Security/AuthProfiles.md)):

```text
Mission Control UI
  -> Auth Session API
  -> OAuth session orchestrator
  -> Auth Runner Container + mounted auth volume
  -> tmate session
  -> provider login bootstrap
  -> credential verification
  -> auth profile create/update
```

### 4.2 Main Components

#### A. Auth Session API

Responsible for:

* creating OAuth sessions
* fetching session status
* cancelling sessions
* finalizing sessions
* exposing tmate connection metadata to the UI

#### B. OAuth Session Orchestrator

Responsible for:

* creating the Docker volume if needed
* starting the auth runner
* waiting for tmate readiness
* monitoring session status
* verifying auth files
* registering/updating the auth profile (via [AuthProfiles.md](../Security/AuthProfiles.md))
* tearing down the session

For MoonMind, this should be a **Temporal workflow** even in MVP.

#### C. Auth Runner Container

A short-lived container whose only job is to:

* mount the target auth volume
* set provider-specific env vars
* start tmate
* run the provider bootstrap command in the tmate session
* leave the shell open for user interaction

#### D. Provider Registry

A small provider-specific contract that defines:

* runtime id
* default volume name
* default mount path
* bootstrap command
* success verifier
* optional post-login command
* profile defaults

#### E. Profile Registrar

Calls the existing auth-profile registration path (see [AuthProfiles.md](../Security/AuthProfiles.md)) after successful verification.

#### F. TmateSessionManager (shared — target architecture)

> [!NOTE]
> `TmateSessionManager` does not exist yet. Currently, `oauth_session_activities.py` uses a Docker-exec polling approach with hardcoded `/tmp/tmate.sock`. The target architecture delegates tmate lifecycle management to the shared abstraction defined in [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md) §4.

In the target state, the session orchestrator will delegate tmate lifecycle management to `TmateSessionManager`. This includes:

* session creation with per-session config (including self-hosted server options)
* readiness detection via `tmate wait tmate-ready`
* endpoint extraction (`web_ro`, `web_rw`, `ssh_ro`, `ssh_rw`)
* teardown and socket cleanup

The same abstraction will also be used by `ManagedRuntimeLauncher` for runtime session wrapping, ensuring consistent behavior across both use cases.

---

## 5. Provider Registry

Create a provider registry with one entry per runtime.

Example shape:

```python
class OAuthProviderSpec(TypedDict):
    runtime_id: str
    auth_mode: str
    session_transport: str            # "tmate"
    default_volume_name: str
    default_mount_path: str
    bootstrap_command: list[str]
    success_check: str                # symbolic verifier id
    account_label_prefix: str
```

Example entries:

### Codex

* `runtime_id = "codex_cli"`
* `default_volume_name = "codex_auth_volume"`
* `default_mount_path = "/home/app/.codex"`
* bootstrap: launch shell and run Codex login helper

### Gemini

* `runtime_id = "gemini_cli"`
* `default_volume_name = "gemini_auth_volume"`
* `default_mount_path = "/var/lib/gemini-auth"`
* bootstrap: launch shell and run Gemini helper

### Claude

* `runtime_id = "claude_code"`
* `default_volume_name = "claude_auth_volume"`
* `default_mount_path = "/home/app/.claude"`
* bootstrap: launch shell and run Claude helper

### Cursor

* `runtime_id = "cursor_cli"`
* `default_volume_name = "cursor_auth_volume"`
* `default_mount_path = "/home/app/.cursor"`
* bootstrap: placeholder until runtime support exists

All providers use `auth_mode = "oauth"` and `session_transport = "tmate"` for the MVP.

---

## 6. Provider Bootstrap Behavior

The MVP does not try to fully unify provider commands. Instead, it unifies the shell contract.

Each provider gets a bootstrap script with the same shape:

1. print standardized MoonMind auth banner
2. print provider-specific instructions
3. export the provider's OAuth env vars
4. launch provider login command
5. print next steps on success/failure

Example conceptual commands:

* Codex: start Codex login flow in the mounted auth home
* Gemini: start Gemini CLI in OAuth mode with API keys cleared
* Claude: start Claude and guide the user into login
* Cursor: start Cursor CLI login flow when runtime support lands

The UI is universal. The bootstrap script is provider-specific.

---

## 7. OAuth Session Data Model

### `managed_agent_oauth_sessions` Table

Fields:

* `session_id` — PK
* `runtime_id`
* `profile_id`
* `auth_mode` — always `oauth`
* `session_transport` — `tmate`
* `volume_ref`
* `volume_mount_path`
* `status`
* `requested_by_user_id`
* `account_label`
* `tmate_web_url`
* `tmate_ssh_url`
* `container_name`
* `worker_service`
* `expires_at`
* `started_at`
* `completed_at`
* `cancelled_at`
* `failure_reason`
* `metadata_json`

### Status Enum

* `pending`
* `starting`
* `tmate_ready`
* `awaiting_user`
* `verifying`
* `registering_profile`
* `succeeded`
* `failed`
* `cancelled`
* `expired`

---

## 8. Session Flow

### 8.1 Start Session

1. User clicks **Connect with OAuth**
2. UI sends `POST /api/v1/oauth-sessions`
3. Backend validates:
   * runtime is supported
   * profile id is valid
   * no conflicting active session for same profile
4. Backend creates DB row with `pending`
5. Orchestrator starts

### 8.2 Provision Auth Runner

The orchestrator:

1. ensures target Docker volume exists
2. picks the auth runner image/service
3. launches a short-lived auth container
4. mounts the auth volume at the provider's expected path
5. injects environment shaping needed for OAuth mode
6. starts tmate inside the container
7. waits for tmate readiness
8. stores web/ssh URLs in the session row
9. marks status `tmate_ready` then `awaiting_user`

### 8.3 User Completes Login

The user opens the tmate web terminal from Mission Control.

Inside the terminal, a bootstrap script runs automatically. It should:

* print a banner
* show what provider is being authenticated
* show where credentials will be stored
* clear conflicting API key env vars when needed
* start the provider login command
* leave the user in a real shell if further interaction is needed

After the user finishes login, the provider stores credentials in the mounted volume.

### 8.4 Verification and Profile Registration

The orchestrator either polls or is manually nudged by UI refresh.

Verification step:

1. inspect the mounted volume
2. run provider-specific success check
3. if successful, mark `verifying`
4. create or update the auth profile (see [AuthProfiles.md](../Security/AuthProfiles.md))
5. mark `registering_profile`
6. mark `succeeded`
7. tear down tmate/container

### 8.5 Failure / Cancel

If the user cancels:

1. mark `cancelled`
2. stop auth runner
3. retain session row for audit
4. do not modify auth profile

If session expires:

1. mark `expired`
2. stop auth runner
3. leave volume intact
4. do not register profile

If verification fails:

1. mark `failed`
2. preserve failure reason
3. optionally keep the tmate session alive for a short retry window

---

## 9. API Design

### 9.1 Create Session

`POST /api/v1/oauth-sessions`

Request:

```json
{
  "runtime_id": "gemini_cli",
  "profile_id": "gemini_nsticco",
  "volume_ref": "gemini_auth_vol_nsticco",
  "account_label": "Nathaniel Gemini",
  "max_parallel_runs": 1,
  "cooldown_after_429_seconds": 300,
  "rate_limit_policy": "backoff"
}
```

Response:

```json
{
  "session_id": "oas_123",
  "runtime_id": "gemini_cli",
  "profile_id": "gemini_nsticco",
  "status": "starting"
}
```

### 9.2 Get Session

`GET /api/v1/oauth-sessions/{session_id}`

Response:

```json
{
  "session_id": "oas_123",
  "runtime_id": "gemini_cli",
  "profile_id": "gemini_nsticco",
  "status": "awaiting_user",
  "tmate_web_url": "https://...",
  "tmate_ssh_url": "ssh ...",
  "expires_at": "2026-03-21T18:00:00Z",
  "failure_reason": null
}
```

### 9.3 Cancel Session

`POST /api/v1/oauth-sessions/{session_id}/cancel`

### 9.4 Optional Manual Finalize

`POST /api/v1/oauth-sessions/{session_id}/finalize`

Useful if the system uses optimistic verification and the user wants to push re-check explicitly.

---

## 10. Temporal Workflow Design

### `MoonMind.OAuthSession`

Input:

* session id
* runtime id
* profile id
* volume ref
* volume mount path
* requested-by user id
* profile settings

Activities:

1. `oauth_session.ensure_volume`
2. `oauth_session.start_auth_runner`
3. `oauth_session.wait_for_tmate_ready`
4. `oauth_session.update_session_urls`
5. `oauth_session.verify_volume`
6. `oauth_session.register_profile`
7. `oauth_session.stop_auth_runner`
8. `oauth_session.mark_failed`

Why Temporal even for MVP:

* durable status transitions
* retries on container startup
* clearer operational story
* fits MoonMind's existing workflow-driven architecture

---

## 11. Security

### 11.1 Access Control

Only authenticated MoonMind users with admin or auth-management permission can start OAuth sessions.

### 11.2 Session Lifetime

Tmate sessions should be short-lived:

* default 20–30 minutes
* auto-expire if idle
* auto-destroy after success

### 11.3 Secret Handling

Do not send credential contents to the UI.

The UI only ever sees:

* session status
* tmate URLs
* timestamps
* failure messages

Credentials remain in the mounted Docker volume.

### 11.4 Self-Hosted Tmate Server

For production deployments, configure `MOONMIND_TMATE_SERVER_HOST` and related environment variables to use a private relay server. Sessions on the public `tmate.io` infrastructure traverse third-party servers. See [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md) §4.3 for configuration details.

### 11.5 Audit

Store:

* who started the session
* what runtime
* what profile
* when it started
* when it ended
* whether it succeeded

---

## 12. Suggested Repo Structure

### Backend

* `api_service/api/routers/oauth_sessions.py`
* `api_service/api/schemas_oauth_sessions.py`
* `api_service/services/oauth_session_service.py`

### Auth Runtime Logic

* `moonmind/auth/providers/base.py`
* `moonmind/auth/providers/registry.py`
* `moonmind/auth/providers/codex_cli.py`
* `moonmind/auth/providers/gemini_cli.py`
* `moonmind/auth/providers/claude_code.py`
* `moonmind/auth/providers/cursor_cli.py`
* `moonmind/auth/tmate_runner.py`
* `moonmind/auth/volume_verifiers.py`

### Temporal

* `moonmind/workflows/temporal/workflows/oauth_session.py`
* `moonmind/workflows/temporal/activities/oauth_session_activities.py`

### UI

* `api_service/static/task_dashboard/dashboard.js`
* `api_service/templates/task_dashboard.html`

### Scripts

* `tools/auth-tmate-bootstrap-codex.sh`
* `tools/auth-tmate-bootstrap-gemini.sh`
* `tools/auth-tmate-bootstrap-claude.sh`
* `tools/auth-tmate-bootstrap-cursor.sh`

The bootstrap scripts can initially wrap the existing volume conventions rather than replace them.

---

## 13. Operational Behavior

### 13.1 Single Active Session Per Profile

For MVP, allow only one active OAuth session per `profile_id`.

### 13.2 Session Cleanup

A periodic cleanup job should expire stale sessions and stop abandoned auth containers.

### 13.3 Restart Behavior

For MVP, acceptable behavior is:

* session row survives restart
* active runner may be lost
* UI can show `failed` or `expired` and require restart

Full runner reattachment can wait until later.

---

## 14. Why Universal Tmate Is Acceptable for MVP

It is not the ideal end state, but it is a good first shippable system because it gives you:

* one flow for all providers
* one backend state machine
* one UI pattern
* one operational story
* one path to auth-volume registration

It also creates a clean future seam: later you can swap only the **session transport** for a provider without changing the rest of the system.

Examples of later improvements:

* Codex moves from `tmate` to `device_code`
* Gemini gets a better browser-assisted flow later
* Claude remains `tmate`
* Cursor gets its own specialized driver

That migration becomes small if the rest of the system already thinks in terms of **OAuth Sessions**.

---

## 15. Tradeoffs

### Pros

* fastest unified OAuth MVP
* works for interactive CLI auth flows
* keeps Mission Control consistent
* matches existing auth-volume outcome
* preserves future provider-specific optimization

### Cons

* tmate is heavier than native auth for some providers
* session cleanup and access control matter
* UX is less polished than provider-native browser/device flows
* not ideal for large-scale multi-instance orchestration yet
* Cursor support remains conceptual until runtime support exists

---

## 16. Delivery Milestones

**MVP:** OAuth session store + API + Mission Control modal + tmate-backed runner (Gemini first), then Codex/Claude via the same transport with profile registration, then cleanup/audit/hardening/reconnect flows, then optional provider-specific driver splits. Task-level tracking: [`docs/tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md`](../tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md).

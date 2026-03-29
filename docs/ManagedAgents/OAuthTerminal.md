# OAuth Terminal Architecture in MoonMind

**Replaces:** `docs/ManagedAgents/TmateArchitecture.md`
**Status:** Design Draft
**Owners:** MoonMind Engineering

## 1. Summary

MoonMind should stop using `tmate` for browser-triggered OAuth flows for managed CLI runtimes.

For CLI-based authentication flows such as Claude Code, Gemini CLI, and Codex CLI support, the correct long-term transport is:

* **`xterm.js` in Mission Control**
* **a MoonMind-owned PTY/WebSocket bridge**
* **a short-lived auth container**
* **a persistent auth volume or provider-profile backing store**

The user experience becomes:

1. The operator starts an OAuth session from Mission Control.
2. MoonMind creates a short-lived auth container and mounts the target auth volume.
3. MoonMind allocates a PTY for that container’s login shell or bootstrap command.
4. Mission Control attaches to that PTY through `xterm.js` over a MoonMind WebSocket.
5. The CLI drives the login flow in a browser-native terminal.
6. MoonMind verifies the credential material in the mounted volume.
7. MoonMind registers or updates the Provider Profile.
8. MoonMind tears down the terminal bridge and auth container.

This keeps OAuth interactive, browser-native, auditable, and fully first-party.

---

## 2. Why this replaces tmate

The current design uses tmate as the session transport for OAuth and stores `tmate_web_url` / `tmate_ssh_url` in the OAuth session row, with `session_transport` defaulting to `"tmate"` and a `TMATE_READY` lifecycle state  . The current `oauth_session.start_auth_runner` activity launches a Docker container, starts tmate inside it, polls tmate URLs with `docker exec`, and writes those URLs back into the database .

That is workable, but it is the wrong abstraction for this product surface.

OAuth triggered from Mission Control is not a remote shell handoff problem. It is a **first-party browser terminal problem**. MoonMind should own:

* terminal rendering contract
* session authentication
* PTY lifecycle
* audit trail
* cleanup and expiry behavior
* future product UX improvements

Using `xterm.js` plus a MoonMind PTY bridge gives MoonMind direct control over the exact thing the user is experiencing.

---

## 3. Goals

### 3.1 Primary goals

MoonMind OAuth sessions should provide:

* a browser-native terminal experience
* first-party session lifecycle control
* volume-backed credential persistence
* explicit verification and profile registration
* a short-lived, tightly scoped security model
* consistent UX across Claude Code, Gemini CLI, and Codex CLI auth flows

### 3.2 Non-goals

This design does not attempt to:

* reintroduce terminal attachment for ordinary managed task runs
* build a generic remote shell product for all workers
* replace every provider’s auth flow with browser redirects
* solve all intervention use cases through terminal access

The PTY/WebSocket bridge defined here is for **OAuth sessions only**.

---

## 4. Core design

## 4.1 High-level architecture

```text
Mission Control UI
  -> OAuth Session API
  -> MoonMind.OAuthSession workflow
  -> Auth runner container
  -> PTY allocator / terminal bridge
  -> WebSocket session
  -> xterm.js in browser
  -> provider login CLI
  -> mounted auth volume
  -> verification
  -> Provider Profile registration
```

## 4.2 Main components

### A. OAuth Session API

Responsible for:

* creating OAuth sessions
* fetching OAuth session state
* cancelling OAuth sessions
* optionally forcing re-verification or finalize
* issuing terminal session tokens or upgrade parameters for the WebSocket bridge

### B. OAuth Session Workflow

MoonMind should keep a dedicated Temporal workflow for OAuth session orchestration.

It should own:

* session status transitions
* volume provisioning
* auth container startup
* PTY bridge startup
* timeout handling
* verification retries
* profile registration
* final teardown

### C. Auth Runner Container

A short-lived container whose only purpose is to host the runtime’s login CLI against the mounted auth volume.

Responsibilities:

* mount auth volume at runtime-appropriate path
* set provider-specific environment shaping for OAuth mode
* start the shell or bootstrap command inside a PTY
* keep the environment constrained to the auth session
* exit cleanly after success, cancel, or expiry

### D. PTY Bridge Service

A MoonMind-owned backend component that:

* allocates a PTY for the auth shell
* connects browser input/output through WebSocket
* handles resize events from `xterm.js`
* enforces session auth and TTL
* records session connection metadata
* closes on workflow completion, timeout, or user cancel

### E. Provider Registry

A provider-specific contract that defines:

* runtime id
* default volume name
* default mount path
* bootstrap command
* verification logic
* optional post-login validation command
* profile defaults

### F. Provider Profile Registrar

Calls the existing provider-profile registration/update path after verification succeeds.

MoonMind already has the right destination object for this in the provider-profile system; OAuth sessions should feed that system, not invent a parallel auth store .

---

## 5. Session transport model

## 5.1 Transport identifier

Replace the OAuth session transport from `"tmate"` to a MoonMind-owned transport identifier such as:

* `moonmind_pty_ws`

## 5.2 Browser transport

Mission Control should use:

* `xterm.js` for rendering
* a MoonMind WebSocket endpoint for bidirectional terminal traffic

### WebSocket responsibilities

The WebSocket channel must carry:

* terminal output frames
* user input frames
* terminal resize frames
* keepalive/heartbeat
* terminal close/error notifications

### Example frame shapes

```json
{ "type": "input", "data": "y" }
{ "type": "resize", "cols": 120, "rows": 36 }
{ "type": "stdout", "data": "Open this URL in your browser: ..." }
{ "type": "status", "status": "awaiting_user" }
{ "type": "closed", "reason": "verification_succeeded" }
```

## 5.3 PTY semantics

The PTY bridge should connect the browser to a real shell or provider bootstrap process, not to detached file tails.

This is important because CLI auth flows often involve:

* device-code instructions
* interactive confirmations
* fallback prompts
* browser-open messages
* multi-step terminal state

A plain log stream is not enough.

---

## 6. Provider registry

The current tmate design already assumes a per-runtime provider registry with default volume name, mount path, bootstrap command, and success verifier . That still makes sense and should remain, but the transport-specific field changes.

Suggested shape:

```python id="3e8tcf"
class OAuthProviderSpec(TypedDict):
    runtime_id: str
    auth_mode: str
    session_transport: str          # "moonmind_pty_ws"
    default_volume_name: str
    default_mount_path: str
    bootstrap_command: list[str]
    success_check: str
    account_label_prefix: str
```

### Expected providers

#### Codex CLI

* `runtime_id = "codex_cli"`
* volume-backed auth home
* provider-specific login bootstrap command

#### Gemini CLI

* `runtime_id = "gemini_cli"`
* volume-backed auth home
* OAuth-mode environment shaping with conflicting API-key env cleared

#### Claude Code

* `runtime_id = "claude_code"`
* volume-backed auth home
* provider-specific login entrypoint


* placeholder until runtime support is real
* same transport model once supported

---

## 7. OAuth session data model

The current `ManagedAgentOAuthSession` table is explicitly tmate-shaped: `session_transport` defaults to `"tmate"`, it stores `tmate_web_url` and `tmate_ssh_url`, and uses a `TMATE_READY` lifecycle enum value .

This should be rewritten.

## 7.1 `managed_agent_oauth_sessions`

Suggested fields:

* `session_id`
* `runtime_id`
* `profile_id`
* `auth_mode`
* `session_transport` — `moonmind_pty_ws`
* `volume_ref`
* `volume_mount_path`
* `status`
* `requested_by_user_id`
* `account_label`
* `container_name`
* `worker_service`
* `terminal_session_id`
* `terminal_bridge_id`
* `connected_at`
* `disconnected_at`
* `expires_at`
* `started_at`
* `completed_at`
* `cancelled_at`
* `failure_reason`
* `metadata_json`

## 7.2 Status enum

Replace:

* `tmate_ready`

with something transport-neutral, such as:

* `bridge_ready`

Recommended enum:

* `pending`
* `starting`
* `bridge_ready`
* `awaiting_user`
* `verifying`
* `registering_profile`
* `succeeded`
* `failed`
* `cancelled`
* `expired`

That preserves the current lifecycle intent while removing transport leakage from the domain model.

## 7.3 Terminal session row

For cleaner separation, add a dedicated terminal-session table rather than overloading the OAuth session row forever.

Suggested table: `oauth_terminal_sessions`

Fields:

* `terminal_session_id`
* `oauth_session_id`
* `transport`
* `status`
* `websocket_path`
* `created_at`
* `connected_at`
* `last_activity_at`
* `closed_at`
* `close_reason`
* `cols`
* `rows`
* `metadata_json`

This keeps terminal transport details decoupled from OAuth business state.

---

## 8. Session lifecycle

## 8.1 Create session

1. User clicks **Connect with OAuth** in Mission Control.
2. UI calls `POST /api/v1/oauth-sessions`.
3. Backend validates:

   * runtime is supported
   * profile id is valid or creatable
   * no conflicting active session exists for the same profile
4. Backend creates the session row with `pending`.
5. `MoonMind.OAuthSession` starts.

## 8.2 Start auth environment

The workflow:

1. ensures the target auth volume exists
2. starts the auth runner container
3. starts the PTY bridge
4. allocates a terminal session id
5. marks the session `bridge_ready`
6. marks the session `awaiting_user`

## 8.3 Browser attaches

1. Mission Control fetches the OAuth session.
2. Mission Control receives terminal connection metadata from MoonMind.
3. `xterm.js` opens a WebSocket to MoonMind.
4. MoonMind attaches that socket to the PTY.
5. The provider bootstrap command prints instructions and begins the auth flow.

## 8.4 User completes login

The CLI drives the login flow in the embedded terminal.

Examples of interaction:

* “Open this URL in your browser”
* “Enter device code”
* “Press Enter to continue”
* “Authentication complete”

Credentials are written into the mounted auth volume or equivalent runtime materialization store.

## 8.5 Verification and registration

The workflow then:

1. moves to `verifying`
2. runs provider-specific credential verification
3. if verification passes, moves to `registering_profile`
4. creates or updates the Provider Profile
5. marks the session `succeeded`
6. closes terminal bridge
7. stops auth container

## 8.6 Cancel / expire / fail

### Cancel

* mark `cancelled`
* close bridge
* stop container
* do not update profile

### Expire

* mark `expired`
* close bridge
* stop container
* preserve volume
* do not update profile

### Fail

* mark `failed`
* store failure reason
* preserve enough metadata for support/audit
* optionally keep container briefly for diagnosis if policy allows, but default to teardown

---

## 9. API design

## 9.1 Create session

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

## 9.2 Get session

`GET /api/v1/oauth-sessions/{session_id}`

Response:

```json
{
  "session_id": "oas_123",
  "runtime_id": "gemini_cli",
  "profile_id": "gemini_nsticco",
  "status": "awaiting_user",
  "terminal": {
    "transport": "moonmind_pty_ws",
    "terminal_session_id": "term_123",
    "websocket_url": "/api/v1/oauth-sessions/oas_123/terminal/ws",
    "connected": false
  },
  "expires_at": "2026-03-21T18:00:00Z",
  "failure_reason": null
}
```

## 9.3 Terminal WebSocket

`GET /api/v1/oauth-sessions/{session_id}/terminal/ws`

Requirements:

* authenticated MoonMind user
* user must have auth-management permission
* session must be active and belong to an allowed scope
* short-lived signed terminal token or equivalent should be required

## 9.4 Cancel session

`POST /api/v1/oauth-sessions/{session_id}/cancel`

## 9.5 Optional finalize / verify now

`POST /api/v1/oauth-sessions/{session_id}/finalize`

Useful when the system wants a manual “re-check now” button.

---

## 10. Temporal workflow design

The current design already wants a `MoonMind.OAuthSession` workflow with activities like `ensure_volume`, `start_auth_runner`, `verify_volume`, `register_profile`, and `stop_auth_runner` . That remains correct; only the terminal transport activities change.

## 10.1 `MoonMind.OAuthSession`

Input:

* `session_id`
* `runtime_id`
* `profile_id`
* `volume_ref`
* `volume_mount_path`
* `requested_by_user_id`
* profile defaults / policy

## 10.2 Activities

Suggested activities:

1. `oauth_session.ensure_volume`
2. `oauth_session.start_auth_runner_container`
3. `oauth_session.start_terminal_bridge`
4. `oauth_session.update_session_status`
5. `oauth_session.verify_volume`
6. `oauth_session.register_profile`
7. `oauth_session.stop_terminal_bridge`
8. `oauth_session.stop_auth_runner_container`
9. `oauth_session.mark_failed`

## 10.3 Signals / updates

Optional workflow interactions:

* `cancel_session`
* `request_finalize`
* `terminal_connected`
* `terminal_disconnected`

This gives the workflow better visibility into user presence and cleanup decisions.

---

## 11. Backend PTY bridge design

## 11.1 Scope

This should be implemented as a narrow subsystem for OAuth sessions only.

It should not expose:

* arbitrary worker terminal access
* generic Docker exec for users
* reusable shell access across the platform

## 11.2 Responsibilities

The bridge must:

* create or attach to the auth session PTY
* proxy output to WebSocket clients
* proxy input from the browser into the PTY
* handle terminal resize
* enforce auth and session ownership
* terminate on session close
* record audit metadata

## 11.3 Container interaction model

Preferred model:

* auth runner container starts under MoonMind control
* PTY process is launched as the container’s main login shell or a provider bootstrap shell
* MoonMind controls stdin/stdout/stderr through the PTY rather than polling detached session URLs

This is simpler and more first-party than `docker exec` polling for tmate endpoints.

## 11.4 Authentication

WebSocket attach should require:

* authenticated MoonMind session
* explicit authorization to manage auth profiles
* session-scoped token or one-time attach token
* expiry enforcement

---

## 12. Mission Control UX

## 12.1 Auth Profiles page

The current OAuth modal concept remains good, but the terminal button changes meaning.

Instead of showing:

* tmate web URL
* optional SSH URL
* “Open Terminal” to external session

Mission Control should show:

* session status
* embedded terminal panel using `xterm.js`
* session expiration
* runtime/provider-specific short instructions
* `Cancel Session`
* `Refresh Status`
* `Finalize / Verify Now` if needed

## 12.2 Embedded terminal behavior

Default behavior:

* terminal starts disconnected until session reaches `bridge_ready`
* UI auto-connects when the session becomes ready
* browser terminal displays provider bootstrap output
* disconnects are recoverable while the session remains active
* once verification succeeds, terminal closes with a clear success message

## 12.3 Post-session behavior

On success:

* show “OAuth completed successfully”
* show registered/updated Provider Profile
* offer “Close”

On failure:

* show failure reason
* offer restart or cancel

---

## 13. Security model

## 13.1 Access control

Only authenticated users with auth-management permission can:

* create OAuth sessions
* attach to terminal WebSocket
* cancel/finalize OAuth sessions

## 13.2 Session scope

Each terminal session is bound to exactly one OAuth session.

No cross-session terminal reuse.

## 13.3 Session lifetime

OAuth terminal sessions should be short-lived:

* default 20–30 minutes
* auto-expire if idle
* auto-close on success or cancel

## 13.4 Secret handling

The browser must never receive credential contents.

The UI may see only:

* status
* timestamps
* terminal I/O generated by the CLI itself
* failure reason
* profile registration result

Credentials remain in the mounted auth volume or equivalent provider-profile backing material.

## 13.5 Audit trail

Persist:

* who started the session
* which runtime and profile
* when browser connected
* when browser disconnected
* when verification succeeded or failed
* why the session ended

---

## 14. Repository structure

Suggested additions/renames:

### Backend API

* `api_service/api/routers/oauth_sessions.py`
* `api_service/api/schemas_oauth_sessions.py`
* `api_service/services/oauth_session_service.py`
* `api_service/services/oauth_terminal_service.py`

### Terminal bridge

* `moonmind/auth/terminal/pty_bridge.py`
* `moonmind/auth/terminal/session_manager.py`
* `moonmind/auth/terminal/ws_protocol.py`

### Auth runtime logic

* `moonmind/auth/providers/base.py`
* `moonmind/auth/providers/registry.py`
* `moonmind/auth/providers/codex_cli.py`
* `moonmind/auth/providers/gemini_cli.py`
* `moonmind/auth/providers/claude_code.py`

* `moonmind/auth/volume_verifiers.py`

### Temporal

* `moonmind/workflows/temporal/workflows/oauth_session.py`
* `moonmind/workflows/temporal/activities/oauth_session_activities.py`

### UI

* Mission Control terminal component using `xterm.js`
* Auth session modal/state integration

---

## 15. Migration plan

## 15.1 Phase 1 — Domain cleanup

* introduce transport-neutral OAuth session statuses
* replace `tmate_ready` with `bridge_ready`
* stop treating tmate URLs as the core session payload
* add terminal session metadata fields

## 15.2 Phase 2 — Backend bridge

* implement PTY bridge
* implement session-scoped WebSocket attach
* wire auth runner container lifecycle to the bridge

## 15.3 Phase 3 — UI terminal

* add `xterm.js` terminal component to Mission Control
* connect to session WebSocket
* handle resize/reconnect/close states

## 15.4 Phase 4 — Workflow/activity swap

* replace `start_auth_runner` tmate URL extraction path
* replace `update_session_urls` with terminal session metadata updates
* keep `ensure_volume`, `verify_volume`, and `register_profile`

## 15.5 Phase 5 — Remove tmate

* remove OAuth dependence on tmate
* remove related bootstrap scripts and activity logic
* narrow remaining tmate code to any legacy debug-only paths still intentionally kept elsewhere
* ideally remove tmate entirely from MoonMind

---

## 16. Why this is the right end state

MoonMind is already moving toward first-party, artifact-backed, explicit control surfaces for managed execution. OAuth should follow the same philosophy.

The good parts of the current design stay:

* ephemeral auth container
* mounted auth volume
* provider registry
* verification
* Provider Profile registration
* workflow-driven lifecycle

The part that changes is just the session transport.

That is exactly the seam you want.

---

## 17. Summary

MoonMind should replace tmate-based OAuth sessions with a first-party terminal architecture built on:

* `xterm.js`
* a MoonMind-owned PTY/WebSocket bridge
* a short-lived auth container
* persistent auth volume verification
* Provider Profile registration through the existing provider-profile system

This gives MoonMind a cleaner product, better UX, stronger control, and a more coherent architecture than keeping tmate as the last remaining terminal transport in the system.

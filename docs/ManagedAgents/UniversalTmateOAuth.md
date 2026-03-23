# Universal Tmate OAuth Sessions — MVP Design for MoonMind

Status: **Design Draft**
Owners: MoonMind Engineering
Scope: Mission Control auth UX for managed CLI runtimes
Applies to: `codex_cli`, `gemini_cli`, `claude_code`, future `cursor_cli`

---

## 1. Summary

Build a single **OAuth Session** system in Mission Control where the MVP transport is always **tmate**.

From the user’s perspective, every managed CLI runtime gets the same flow:

1. Click **Connect Account** in Mission Control.
2. MoonMind creates or selects an auth volume.
3. MoonMind launches a short-lived auth container with that volume mounted.
4. MoonMind starts a **tmate session** inside that container.
5. The user opens the tmate terminal from the UI.
6. The terminal auto-launches the provider’s login bootstrap.
7. The provider writes credentials into the mounted volume.
8. MoonMind verifies the volume and registers or updates the normal auth profile.
9. MoonMind closes the session and marks it complete.

This gives MoonMind a single shippable OAuth story for all providers now, while preserving the option to introduce cleaner provider-specific drivers later.

---

## 2. Product goal

The MVP goal is not “perfect provider-native auth.”

The MVP goal is:

* one **consistent Mission Control OAuth button**
* one **consistent backend session model**
* one **consistent volume outcome**
* one **consistent auth profile registration path**

The universal behavior is the **session transport** and **operator UX**.
The provider-specific behavior is limited to the command that runs inside the tmate terminal and the verification logic.

---

## 3. Why this MVP

This design is a good MVP because it:

* works with providers that expect interactive terminal login
* avoids blocking on a polished device-code or browser callback flow for each CLI
* reuses the existing auth-volume model
* fits the existing auth-profile model
* keeps the future path open for Codex-native, Gemini-specific, or Claude-specific auth drivers later

The key idea is:

**standardize the session architecture, not the provider ritual**

---

## 4. Non-goals for MVP

This MVP does **not** try to:

* make every provider authenticate the exact same way internally
* eliminate interactive terminal use
* support multi-API-instance resilient session recovery on day one
* replace API-key auth flows
* solve all enterprise auth cases
* dynamically mount arbitrary volumes into already-running workers

---

## 5. User experience

## 5.1 Mission Control UI

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

## 6. Architecture

## 6.1 Core idea

Introduce a first-class **OAuth Session** layer that sits between Mission Control and the existing auth profile registry.

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

---

## 6.2 Main components

### A. Auth Session API

Responsible for:

* creating OAuth sessions
* fetching session status
* cancelling sessions
* finalizing sessions
* exposing tmate connection metadata to the UI

### B. OAuth session orchestrator

Responsible for:

* creating the Docker volume if needed
* starting the auth runner
* waiting for tmate readiness
* monitoring session status
* verifying auth files
* registering/updating the auth profile
* tearing down the session

For MoonMind, this should be a **Temporal workflow** even in MVP.

### C. Auth Runner Container

A short-lived container whose only job is to:

* mount the target auth volume
* set provider-specific env vars
* start tmate
* run the provider bootstrap command in the tmate session
* leave the shell open for user interaction

### D. Provider Registry

A small provider-specific contract that defines:

* runtime id
* default volume name
* default mount path
* bootstrap command
* success verifier
* optional post-login command
* profile defaults

### E. Profile Registrar

Calls the existing auth-profile registration path after successful verification.

---

## 7. Proposed runtime contract

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

The important part is that all of them use:

* `auth_mode = "oauth"`
* `session_transport = "tmate"`

for the MVP.

---

## 8. Data model

## 8.1 Keep existing auth profile model

Do not complicate the existing auth profile model for MVP.

Continue to use the current auth profile record for durable runtime assignment:

* `profile_id`
* `runtime_id`
* `auth_mode = oauth`
* `volume_ref`
* `volume_mount_path`
* `account_label`
* `max_parallel_runs`
* `cooldown_after_429_seconds`
* `rate_limit_policy`
* `enabled`

## 8.2 Add OAuth session table

Add a new table:

### `managed_agent_oauth_sessions`

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

### Status enum

Suggested states:

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

This is the key missing abstraction that makes the UI and backend manageable.

---

## 9. Session flow

## 9.1 Start session

1. User clicks **Connect with OAuth**
2. UI sends `POST /api/v1/oauth-sessions`
3. Backend validates:

   * runtime is supported
   * profile id is valid
   * no conflicting active session for same profile
4. Backend creates DB row with `pending`
5. Orchestrator starts

## 9.2 Provision auth runner

The orchestrator:

1. ensures target Docker volume exists
2. picks the auth runner image/service
3. launches a short-lived auth container
4. mounts the auth volume at the provider’s expected path
5. injects environment shaping needed for OAuth mode
6. starts tmate inside the container
7. waits for tmate readiness
8. stores web/ssh URLs in the session row
9. marks status `tmate_ready` then `awaiting_user`

## 9.3 User completes login

The user opens the tmate web terminal from Mission Control.

Inside the terminal, a bootstrap script runs automatically. It should:

* print a banner
* show what provider is being authenticated
* show where credentials will be stored
* clear conflicting API key env vars when needed
* start the provider login command
* leave the user in a real shell if further interaction is needed

After the user finishes login, the provider stores credentials in the mounted volume.

## 9.4 Verification

The orchestrator either polls or is manually nudged by UI refresh.

Verification step:

1. inspect the mounted volume
2. run provider-specific success check
3. if successful, mark `verifying`
4. create or update the auth profile
5. mark `registering_profile`
6. mark `succeeded`
7. tear down tmate/container

## 9.5 Failure / cancel

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

## 10. Provider bootstrap behavior

The MVP should not try to fully unify provider commands.

Instead, unify the shell contract.

Each provider gets a bootstrap script with the same shape:

1. print standardized MoonMind auth banner
2. print provider-specific instructions
3. export the provider’s OAuth env vars
4. launch provider login command
5. print next steps on success/failure

Example conceptual commands:

* Codex: start Codex login flow in the mounted auth home
* Gemini: start Gemini CLI in OAuth mode with API keys cleared
* Claude: start Claude and guide the user into login
* Cursor: start Cursor CLI login flow when runtime support lands

The UI is universal.
The bootstrap script is provider-specific.

---

## 11. API design

## 11.1 Create session

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

## 11.2 Get session

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

## 11.3 Cancel session

`POST /api/v1/oauth-sessions/{session_id}/cancel`

## 11.4 Optional manual finalize

`POST /api/v1/oauth-sessions/{session_id}/finalize`

Useful if the system uses optimistic verification and the user wants to push re-check explicitly.

---

## 12. Temporal design

Create a workflow like:

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
* fits MoonMind’s existing workflow-driven architecture

---

## 13. Suggested repo structure

### Backend

* `api_service/api/routers/oauth_sessions.py`
* `api_service/api/schemas_oauth_sessions.py`
* `api_service/services/oauth_session_service.py`

### Auth runtime logic

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

## 14. Security model

## 14.1 Access control

Only authenticated MoonMind users with admin or auth-management permission can start OAuth sessions.

## 14.2 Session lifetime

tmate sessions should be short-lived:

* default 20–30 minutes
* auto-expire if idle
* auto-destroy after success

## 14.3 Secret handling

Do not send credential contents to the UI.

The UI only ever sees:

* session status
* tmate URLs
* timestamps
* failure messages

Credentials remain in the mounted Docker volume.

## 14.4 Volume ownership

Preserve the current pattern of writing auth state into dedicated named volumes with correct ownership and file permissions.

## 14.5 Audit

Store:

* who started the session
* what runtime
* what profile
* when it started
* when it ended
* whether it succeeded

---

## 15. Operational behavior

## 15.1 Single active session per profile

For MVP, allow only one active OAuth session per `profile_id`.

## 15.2 Session cleanup

A periodic cleanup job should expire stale sessions and stop abandoned auth containers.

## 15.3 Restart behavior

For MVP, acceptable behavior is:

* session row survives restart
* active runner may be lost
* UI can show `failed` or `expired` and require restart

Full runner reattachment can wait until later.

---

## 16. Why universal tmate is acceptable for MVP

It is not the ideal end state, but it is a good first shippable system because it gives you:

* one flow for all providers
* one backend state machine
* one UI pattern
* one operational story
* one path to auth-volume registration

It also creates a clean future seam:

later you can swap only the **session transport** for a provider without changing the rest of the system.

Examples of later improvements:

* Codex moves from `tmate` to `device_code`
* Gemini gets a better browser-assisted flow later
* Claude remains `tmate`
* Cursor gets its own specialized driver

That migration becomes small if the rest of the system already thinks in terms of **OAuth Sessions**.

---

## 17. Tradeoffs

## Pros

* fastest unified OAuth MVP
* works for interactive CLI auth flows
* keeps Mission Control consistent
* matches existing auth-volume outcome
* preserves future provider-specific optimization

## Cons

* tmate is heavier than native auth for some providers
* session cleanup and access control matter
* UX is less polished than provider-native browser/device flows
* not ideal for large-scale multi-instance orchestration yet
* Cursor support remains conceptual until runtime support exists

---

## 18. MVP implementation sequence

### Phase 1

* add session table
* add OAuth Session API
* add UI modal
* add one tmate-backed auth runner
* support Gemini first

### Phase 2

* add Codex via same session transport
* add Claude via same session transport
* profile registration on success

### Phase 3

* add cleanup job
* add session audit history
* add verification hardening
* add reconnect/retry actions

### Phase 4

* split provider-specific drivers where worthwhile

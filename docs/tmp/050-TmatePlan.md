# Tmate Architecture — Implementation Plan

**Source:** [`docs/ManagedAgents/TmateArchitecture.md`](../ManagedAgents/TmateArchitecture.md)
**Last synced:** 2026-03-24

---

## What Exists Today

| Component | Status | Files |
|-----------|--------|-------|
| `TmateSessionManager` | ✅ Implemented (379 LOC) | `moonmind/workflows/temporal/runtime/tmate_session.py` |
| `TmateServerConfig` / self-hosted relay | ✅ Implemented | `tmate_session.py`, `.env-template` |
| OAuth session Temporal workflow | ✅ Implemented | `moonmind/workflows/temporal/workflows/oauth_session.py` |
| OAuth session activities (5 of 8) | ✅ `ensure_volume`, `start_auth_runner`, `stop_auth_runner`, `update_status`, `mark_failed` | `activities/oauth_session_activities.py` |
| OAuth session cleanup activity | ✅ Implemented | `activities/oauth_session_cleanup.py` |
| OAuth session DB table + migration | ✅ `managed_agent_oauth_sessions` | `api_service/db/models.py`, migration `f1b2c3d4e5f6` |
| OAuth session API router | ✅ Create, Get, Cancel, Finalize | `api_service/api/routers/oauth_sessions.py` |
| OAuth session Pydantic schemas | ✅ Implemented | `api_service/api/schemas_oauth_sessions.py` |
| Bootstrap scripts (all 4 runtimes) | ✅ Implemented | `tools/auth-tmate-bootstrap-{codex,gemini,claude,cursor}.sh` |
| Activity catalog entries | ✅ 4 entries registered | `activity_catalog.py` |
| Auth providers module | ✅ Exists (not OAuth-specific) | `moonmind/auth/providers.py` |

## What's Missing

| Component | Status | Spec Reference |
|-----------|--------|----------------|
| OAuth provider registry (`OAuthProviderSpec`) | ❌ Not implemented | §5 |
| Volume verifiers | ❌ Not implemented | §8.4, §12 |
| `verify_volume` activity | ❌ Not implemented | §10 |
| `register_profile` activity | ❌ Not implemented | §10 |
| `update_session_urls` activity | ❌ Not implemented | §10 |
| Workflow wiring for verification + profile registration | ❌ Gap in `oauth_session.py` | §8.4 |
| `TmateSessionManager` integration into `oauth_session_activities` | ❌ Still uses Docker-exec polling | §4.2-F |
| `TmateSessionManager` integration into `ManagedRuntimeLauncher` | ❌ Still uses inline tmate logic | §4.2-F |
| `workflow_live_sessions` table + endpoint persistence | ❌ Not implemented | §3.1, `TmateSessionArchitecture.md` §5 |
| `report_live_session` / `end_live_session` activities | ❌ Not implemented | `TmateSessionArchitecture.md` §5.2 |
| Live session API (`GET /api/workflows/{id}/live-session`) | ❌ Not implemented | §3.1 |
| Mission Control — Live Output panel | ❌ Not implemented | §3.1 |
| Mission Control — OAuth Session modal | ❌ Not implemented | §3.3 |
| Mission Control — RW handoff grant UI | ❌ Not implemented | §3.2 |
| Session cleanup hardening (docker stop/rm) | ⚠️ Partial | §13.2 |

---

## Phase 1 — OAuth Provider Registry and Volume Verification

**Goal:** Fill the verification gap so OAuth sessions can complete end-to-end (create → tmate → login → verify → register profile → succeed).

- [ ] **1.1** Create `moonmind/auth/providers/registry.py` with `OAuthProviderSpec` typed dict and entries for `gemini_cli`, `codex_cli`, `claude_code`, `cursor_cli`
- [ ] **1.2** Create `moonmind/auth/providers/base.py` defining the `VolumeVerifier` ABC (`verify(volume_mount_path) → bool`)
- [ ] **1.3** Implement verifiers per runtime in `moonmind/auth/volume_verifiers.py` (or per-runtime files): check expected credential files exist in the mounted volume
- [ ] **1.4** Implement `oauth_session.verify_volume` activity — calls the provider's verifier, returns pass/fail with details
- [ ] **1.5** Implement `oauth_session.update_session_urls` activity — write extracted tmate web/ssh URLs to the session DB row
- [ ] **1.6** Implement `oauth_session.register_profile` activity — create or update `managed_agent_auth_profiles` row from session data
- [ ] **1.7** Wire the new activities into `oauth_session.py` workflow (verification → profile registration → succeeded terminal state)
- [ ] **1.8** Add unit tests for verifiers, registry, and the three new activities
- [ ] **1.9** Integration test: simulate full OAuth session lifecycle (pending → starting → tmate_ready → awaiting_user → verifying → registering_profile → succeeded)

---

## Phase 2 — TmateSessionManager Consolidation

**Goal:** Replace inline tmate logic in both consumers with the shared `TmateSessionManager`.

- [ ] **2.1** Refactor `ManagedRuntimeLauncher.launch()` to use `TmateSessionManager` instead of inline tmate socket/config/wait/extract logic (~100 lines removed)
- [ ] **2.2** Refactor `oauth_session_activities.start_auth_runner()` to use `TmateSessionManager` inside the auth container entrypoint (or use docker exec to extract endpoints via the manager)
- [ ] **2.3** Verify `TmateSessionManager.teardown()` is called in supervisor `finally` block for both consumers
- [ ] **2.4** Verify orphaned socket GC on worker startup handles both use cases
- [ ] **2.5** Add regression tests confirming endpoint extraction and teardown work identically via the shared manager
- [ ] **2.6** Harden `oauth_session_cleanup.cleanup_stale` activity to also call `docker stop` + `docker rm` for stale sessions' `container_name`

---

## Phase 3 — Endpoint Persistence and Live Log Tailing

**Goal:** Persist tmate endpoints for running tasks and expose them to Mission Control for live output.

- [ ] **3.1** Create `workflow_live_sessions` table (schema per `TmateSessionArchitecture.md` §5.1) + Alembic migration
- [ ] **3.2** Create SQLAlchemy model for `workflow_live_sessions`
- [ ] **3.3** Implement `agent_runtime.report_live_session` activity — persist `TmateEndpoints` to DB after session `READY`
- [ ] **3.4** Implement `agent_runtime.end_live_session` activity — transition session to `ENDED` on run completion
- [ ] **3.5** Wire report/end activities into the managed agent run workflow (after `TmateSessionManager.start()` succeeds and in the teardown path)
- [ ] **3.6** Implement `GET /api/workflows/{id}/live-session` API endpoint
- [ ] **3.7** Add unit tests for the new activities, model, and API endpoint

---

## Phase 4 — Mission Control Dashboard Integration

**Goal:** Add UI surfaces for live output viewing and OAuth session management.

- [ ] **4.1** Live Output panel — embed `web_ro` tmate URL in task detail view (iframe or xterm.js); poll live-session API for endpoint availability
- [ ] **4.2** OAuth Session modal — create/cancel/refresh session from System Settings → Auth Profiles; wire to `POST/GET/POST` OAuth session API endpoints
- [ ] **4.3** Session status display — show status badge, tmate URLs, expiration countdown, and failure reason in the modal
- [ ] **4.4** "Open Terminal" button — open `web_rw` in new tab (for OAuth sessions)
- [ ] **4.5** Auth Profile management actions — Connect, Reconnect, Check Volume, Disable, Delete (wire to existing auth profile API + new OAuth session API)

---

## Phase 5 — Live Terminal Handoff (RW Grants)

**Goal:** Enable operator escalation from read-only log viewing to interactive RW terminal access.

- [ ] **5.1** RW grant API — endpoint to decrypt and serve `web_rw` / `attach_rw` with a TTL (default 15 min)
- [ ] **5.2** Grant audit logging — record who requested RW access, when, and for which workflow
- [ ] **5.3** Mission Control — "Request Terminal Access" button in task detail, with TTL countdown
- [ ] **5.4** Auto-revoke — expire RW grants and clear decrypted URLs after TTL
- [ ] **5.5** Operator messages — send text to the tmate session (e.g., instructions or pause requests)
- [ ] **5.6** Pause/resume — operator can signal the workflow to pause execution while terminal is in use

---

## Phase 6 — Provider-Specific Driver Splits (Post-MVP)

**Goal:** Replace tmate transport with native auth flows where viable.

- [ ] **6.1** Codex `device_code` driver — use Codex's device-code OAuth flow instead of tmate
- [ ] **6.2** Gemini browser-assisted driver — redirect-based OAuth via browser callback
- [ ] **6.3** Swap `session_transport` field in `OAuthProviderSpec` per provider without changing the rest of the system
- [ ] **6.4** Retain tmate as the default fallback for providers without native drivers

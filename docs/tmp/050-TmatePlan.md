# Tmate Architecture — Implementation Plan

**Source:** [`docs/ManagedAgents/TmateArchitecture.md`](../ManagedAgents/TmateArchitecture.md)
**Last synced:** 2026-03-25

**Phase rollout (status):** **Phase 1** complete (integration test 1.9 still open). **Phase 2** in progress (2.2 `start_auth_runner` still uses Docker-exec polling; other Phase 2 items done). **Phase 3** partial — live persistence follows [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/) (`task_run_live_sessions`, worker HTTP `report_live_session`), not the older `workflow_live_sessions` + Temporal `agent_runtime.*` naming in some architecture docs. **Phase 4** largely done in Mission Control (Live Output + OAuth modal wired to `/api/v1/oauth-sessions`). **Phase 5–6** not started.

---

## What Exists Today

| Component | Status | Files |
|-----------|--------|-------|
| `TmateSessionManager` | ✅ Implemented (379 LOC) | `moonmind/workflows/temporal/runtime/tmate_session.py` |
| `TmateServerConfig` / self-hosted relay | ✅ Implemented | `tmate_session.py`, `.env-template` |
| OAuth session Temporal workflow | ✅ Implemented | `moonmind/workflows/temporal/workflows/oauth_session.py` |
| OAuth session activities (8 of 8) | ✅ `ensure_volume`, `start_auth_runner`, `stop_auth_runner`, `update_status`, `mark_failed`, `update_session_urls`, `verify_volume`, `register_profile` | `activities/oauth_session_activities.py` |
| OAuth session cleanup activity | ✅ Implemented (+ docker `stop`/`rm` on stale rows) | `activities/oauth_session_cleanup.py` |
| OAuth session DB table + migration | ✅ `managed_agent_oauth_sessions` | `api_service/db/models.py`, migration `f1b2c3d4e5f6` |
| OAuth session API router | ✅ Create, Get, Cancel, Finalize | `api_service/api/routers/oauth_sessions.py` |
| OAuth session Pydantic schemas | ✅ Implemented | `api_service/api/schemas_oauth_sessions.py` |
| `OAuthProviderSpec` + provider registry | ✅ Implemented | `moonmind/workflows/temporal/runtime/providers/base.py`, `registry.py` |
| Volume credential verification | ✅ Implemented | `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` |
| Bootstrap scripts (all 4 runtimes) | ✅ Implemented | `tools/auth-tmate-bootstrap-{codex,gemini,claude,cursor}.sh` |
| Activity catalog entries | ✅ Registered (OAuth + new activities) | `activity_catalog.py` |
| Auth providers module | ✅ Exists (not OAuth-specific) | `moonmind/auth/providers.py` |
| `ManagedRuntimeLauncher` + tmate | ✅ Delegates to `TmateSessionManager` | `moonmind/workflows/temporal/runtime/launcher.py` |
| Supervisor tmate teardown + socket GC | ✅ `teardown()` in finally; `gc_orphaned_sockets` on reconcile | `moonmind/workflows/temporal/runtime/supervisor.py` |
| Mission Control — Live Output panel | ✅ iframe + polling (task-run live-session URL from view model) | `api_service/static/task_dashboard/dashboard.js` |
| Mission Control — OAuth Session modal | ✅ Create / poll / cancel wired to `/api/v1/oauth-sessions` | `dashboard.js` (`oauth-session-modal`) |
| Worker live-session reporting (HTTP) | ✅ `report_live_session` / `heartbeat_live_session` → `/api/task-runs/.../live-session/...` | `moonmind/agents/codex_worker/worker.py` |

## What's Missing

| Component | Status | Notes |
|-----------|--------|-------|
| `TmateSessionManager` inside `start_auth_runner` | ❌ Open | Still bash + `docker exec` polling; shares `_ENDPOINT_KEYS` with manager only |
| End-to-end OAuth session **integration** test | ❌ Open | Phase 1.9 — unit coverage exists; full lifecycle test still absent |
| Live session stack completion | ⚠️ Partial | `task_run_live_sessions` in migrations; ORM + router tests skipped / router removed per `test_task_runs.py`; align with `specs/024-live-task-handoff` |
| `GET /api/workflows/{id}/live-session` (doc name) | ⚠️ Superseded | Product path is **`/api/task-runs/{id}/live-session`** (see `TaskRunsApi.md`, OpenAPI in spec 024) |
| Phase 5 — RW grant API, audit, auto-revoke, operator messages | ❌ Not implemented | UI grant button not present in dashboard JS grep |
| Phase 6 — native provider OAuth drivers | ❌ Not implemented | Post-MVP |

---

## Phase 1 — OAuth Provider Registry and Volume Verification

**Goal:** Fill the verification gap so OAuth sessions can complete end-to-end (create → tmate → login → verify → register profile → succeed).

**Phase status:** **Complete**, except **1.9** (integration test).

- [x] **1.1** `OAuthProviderSpec` + registry entries — implemented at `moonmind/workflows/temporal/runtime/providers/registry.py` and `base.py` (not under `moonmind/auth/providers/` as originally sketched).
- [x] **1.2** Volume verification contract — implemented as `verify_volume_credentials` + path maps in `volume_verifiers.py` (no separate `VolumeVerifier` ABC).
- [x] **1.3** Per-runtime credential checks in `volume_verifiers.py`.
- [x] **1.4** `oauth_session.verify_volume` activity.
- [x] **1.5** `oauth_session.update_session_urls` activity.
- [x] **1.6** `oauth_session.register_profile` activity.
- [x] **1.7** Workflow wiring in `oauth_session.py` (verify → register → succeeded).
- [x] **1.8** Unit tests — `tests/unit/auth/test_volume_verifiers.py`, `tests/unit/auth/test_oauth_session_activities.py`, etc.
- [ ] **1.9** Integration test: full OAuth session lifecycle (pending → … → succeeded).

---

## Phase 2 — TmateSessionManager Consolidation

**Goal:** Replace inline tmate logic in both consumers with the shared `TmateSessionManager`.

**Phase status:** **In progress** — **2.2** remains.

- [x] **2.1** Refactor `ManagedRuntimeLauncher.launch()` to use `TmateSessionManager`.
- [ ] **2.2** Refactor `oauth_session_activities.start_auth_runner()` to use `TmateSessionManager` inside the auth container entrypoint (or docker exec via manager); still Docker-exec polling today.
- [x] **2.3** `TmateSessionManager.teardown()` in supervisor `finally` for managed runs; OAuth path uses `stop_auth_runner` (docker stop/rm).
- [x] **2.4** Orphaned socket GC on worker startup (`gc_orphaned_sockets` in supervisor reconcile).
- [x] **2.5** Regression tests — `tests/unit/workflows/temporal/runtime/test_tmate_session.py`, `tests/unit/services/temporal/runtime/test_launcher.py`.
- [x] **2.6** `oauth_session_cleanup.cleanup_stale` calls `docker stop` + `docker rm` when `container_name` is set.

---

## Phase 3 — Endpoint Persistence and Live Log Tailing

**Goal:** Persist tmate endpoints for running tasks and expose them to Mission Control for live output.

**Phase status:** **Partial** — align tasks with **`task_run_live_sessions`** + worker HTTP reporting (`specs/024-live-task-handoff`); the table name `workflow_live_sessions` in older docs is not the implemented schema name.

- [x] **3.1** DB table + migration — `task_run_live_sessions` (see Alembic history); not named `workflow_live_sessions`.
- [ ] **3.2** SQLAlchemy model for live sessions — not present in `api_service/db/models.py` at last sync (verify when completing stack).
- [ ] **3.3** `agent_runtime.report_live_session` activity — **not implemented as named**; worker uses `POST /api/task-runs/{id}/live-session/report` instead.
- [ ] **3.4** `agent_runtime.end_live_session` activity — same drift; end-of-session behavior may live in worker/API paths outside Temporal activity names here.
- [ ] **3.5** Wire report/end into managed agent run — worker calls exist; full persistence/UI contract still incomplete (see Phase 3 partial note above).
- [ ] **3.6** Operator live-session GET API — target **`GET /api/task-runs/{id}/live-session`** (not `/api/workflows/...`); router coverage skipped in `test_task_runs.py`.
- [ ] **3.7** Unit tests for activities/model/API — `test_task_runs.py` currently skipped (`task_runs router has been removed`).

---

## Phase 4 — Mission Control Dashboard Integration

**Goal:** Add UI surfaces for live output viewing and OAuth session management.

**Phase status:** **Largely complete** for MVP surfaces; polish and parity with the full §3 spec may remain.

- [x] **4.1** Live Output panel — `web_ro` iframe, polls live-session payload from task detail flow.
- [x] **4.2** OAuth Session modal — `/api/v1/oauth-sessions` create + poll + cancel.
- [x] **4.3** Session status display — status text + tmate web link when URL present (full countdown/failure UX may be thinner than spec).
- [x] **4.4** “Open” link — tmate web URL opens in new tab from modal.
- [x] **4.5** Auth Profile actions — enable/disable and related handlers in same dashboard section (full “Check Volume” parity TBD).

---

## Phase 5 — Live Terminal Handoff (RW Grants)

**Goal:** Enable operator escalation from read-only log viewing to interactive RW terminal access.

**Phase status:** **Not started** (OpenAPI/spec artifacts may exist; product UI and server paths not verified wired).

- [ ] **5.1** RW grant API — decrypt/serve `web_rw` / `attach_rw` with TTL.
- [ ] **5.2** Grant audit logging.
- [ ] **5.3** Mission Control — “Request Terminal Access” + TTL countdown.
- [ ] **5.4** Auto-revoke after TTL.
- [ ] **5.5** Operator messages into tmate session.
- [ ] **5.6** Pause/resume workflow signals.

---

## Phase 6 — Provider-Specific Driver Splits (Post-MVP)

**Goal:** Replace tmate transport with native auth flows where viable.

**Phase status:** **Not started.**

- [ ] **6.1** Codex `device_code` driver.
- [ ] **6.2** Gemini browser-assisted driver.
- [ ] **6.3** Swap `session_transport` in `OAuthProviderSpec` per provider.
- [ ] **6.4** Retain tmate as default fallback.

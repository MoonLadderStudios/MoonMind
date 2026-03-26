# Tmate Architecture — Implementation Plan

**Source:** [`docs/ManagedAgents/TmateArchitecture.md`](../ManagedAgents/TmateArchitecture.md)
**Last synced:** 2026-03-25

**Phase rollout (status):** **Phase 1** complete (integration test 1.9 still open). **Phase 2** in progress (2.2 `start_auth_runner` still uses Docker-exec polling; other Phase 2 items done). **Phase 3** largely complete — `TaskRunLiveSession` ORM model, `task_runs` router (`GET`, `report`, `heartbeat`, worker endpoints), worker HTTP reporting, and unit tests all implemented; live persistence follows [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/). **Phase 4** complete for MVP surfaces (Live Output + OAuth modal wired to `/api/v1/oauth-sessions`). **Phase 5** not started.

---

## What Exists Today

| Component | Status | Files |
|-----------|--------|-------|
| `TmateSessionManager` | ✅ Implemented (405 LOC) | `moonmind/workflows/temporal/runtime/tmate_session.py` |
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
| `TaskRunLiveSession` ORM model | ✅ Implemented (with encrypted RW fields, `rw_granted_until`) | `api_service/db/models.py` (lines 1939-1986) |
| `task_runs` API router (live-session) | ✅ `GET`, `report`, `heartbeat`, worker-get endpoints with auth | `api_service/api/routers/task_runs.py` |
| Live-session Pydantic schemas | ✅ Request/response models implemented | `api_service/api/schemas_task_runs.py` |
| Live-session unit tests | ✅ 7 tests covering GET, report, heartbeat, 404, worker mismatch | `tests/unit/api/routers/test_task_runs.py` |

## What's Missing

| Component | Status | Notes |
|-----------|--------|-------|
| `TmateSessionManager` inside `start_auth_runner` | ❌ Open | Still bash + `docker exec` polling; shares `_ENDPOINT_KEYS` with manager only |
| End-to-end OAuth session **integration** test | ❌ Open | Phase 1.9 — unit coverage exists; full lifecycle test still absent |
| Phase 5 — RW grant API, audit, auto-revoke, operator messages | ❌ Not implemented | `rw_granted_until` column exists in `TaskRunLiveSession` schema (ready for Phase 5); no grant button in dashboard JS |


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

**Phase status:** **Complete** for core stack. Live-session persistence follows [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/).

- [x] **3.1** DB table + migration — `task_run_live_sessions` (present in initial clean migration `594fc88de6eb`; dropped once from legacy queue backend migration `b92f4891f27c` but model retained and table recreated).
- [x] **3.2** SQLAlchemy model — `TaskRunLiveSession` at `api_service/db/models.py` (lines 1939-1986). Includes encrypted RW fields (`attach_rw_encrypted`, `web_rw_encrypted` via `StringEncryptedType`), `rw_granted_until`, heartbeat/status/worker tracking.
- [x] **3.3** Worker reports live-session via HTTP — `report_live_session` / `heartbeat_live_session` in `codex_worker/worker.py` → `POST /api/task-runs/{id}/live-session/report` and `/heartbeat`. **No Temporal activity wrapper** — direct HTTP reporting.
- [x] **3.4** End-of-session behaviour — worker sends `status: ended` via `report_live_session`; `ended_at` auto-set by router.
- [x] **3.5** Wire report/end into managed agent run — worker calls active (`report_live_session` at lines 7627-7833 of `worker.py`).
- [x] **3.6** Operator live-session GET API — `GET /api/task-runs/{id}/live-session` with user auth; `GET /api/task-runs/{id}/live-session/worker` with worker auth. Both in `task_runs.py` router.
- [x] **3.7** Unit tests — `tests/unit/api/routers/test_task_runs.py` (7 tests: GET 200/404, worker endpoint with encrypted fields, report create, provider validation, heartbeat 200, heartbeat worker-id mismatch 403).

---

## Phase 4 — Mission Control Dashboard Integration

**Goal:** Add UI surfaces for live output viewing and OAuth session management.

**Phase status:** **Complete** for MVP surfaces; polish and parity with the full §3 spec may remain.

- [x] **4.1** Live Output panel — `web_ro` iframe, polls live-session payload from task detail flow.
- [x] **4.2** OAuth Session modal — `/api/v1/oauth-sessions` create + poll + cancel.
- [x] **4.3** Session status display — status text + tmate web link when URL present (full countdown/failure UX may be thinner than spec).
- [x] **4.4** "Open" link — tmate web URL opens in new tab from modal.
- [x] **4.5** Auth Profile actions — enable/disable and related handlers in same dashboard section (full "Check Volume" parity TBD).

---

## Phase 5 — Live Terminal Handoff (RW Grants)

**Goal:** Enable operator escalation from read-only log viewing to interactive RW terminal access.

**Phase status:** **Not started** (schema groundwork present — `rw_granted_until` column, encrypted RW endpoint fields — but no API/UI wired).

- [ ] **5.1** RW grant API — decrypt/serve `web_rw` / `attach_rw` with TTL.
- [ ] **5.2** Grant audit logging.
- [ ] **5.3** Mission Control — "Request Terminal Access" + TTL countdown.
- [ ] **5.4** Auto-revoke after TTL.
- [ ] **5.5** Operator messages into tmate session.
- [ ] **5.6** Pause/resume workflow signals.

---

## Diagnostics — Tmate Production Startup Failures

**Problem:** tmate is installed (2.4.0) and `is_available()` returns `True`, but `start()` fails in the `temporal-worker-agent-runtime` container, causing silent fallback to plain subprocess. Live Output never appears in Mission Control.

**Failure chain:** `launcher.py:409` → `TmateSessionManager.start()` → `tmate -S <sock> -f <conf> -F new-session` → `tmate wait tmate-ready` (30s timeout) → exception → catch at line 430 → teardown → `use_tmate = False` → plain subprocess.

### Step 1 — Check worker logs for the exception

```bash
docker logs moonmind-temporal-worker-agent-runtime-1 2>&1 | grep -A10 "Tmate session failed"
```

The launcher logs `exc_info=True` at `WARNING` level, so the full traceback will show the actual error.

### Step 2 — Manual tmate test inside the container

```bash
# As root:
docker exec -it moonmind-temporal-worker-agent-runtime-1 tmate -F new-session

# As the app user (uid 1000) — matches runtime context:
docker exec -it moonmind-temporal-worker-agent-runtime-1 runuser -u app -- tmate -F new-session
```

If tmate hangs without printing session URLs, the relay connection is failing.

### Step 3 — Test outbound SSH connectivity

```bash
docker exec moonmind-temporal-worker-agent-runtime-1 \
  bash -c "timeout 5 bash -c 'echo | nc -w3 ssh.tmate.io 22' && echo OK || echo BLOCKED"
```

### Likely causes (version 2.4.0 confirmed)

| # | Cause | Fix |
|---|-------|-----|
| 1 | **Outbound port 22 blocked** (firewall/security group) | Open egress to `ssh.tmate.io:22`, or deploy self-hosted relay |
| 2 | **Public `tmate.io` relay unreliable** (rate limits, capacity) | Deploy self-hosted relay; set `MOONMIND_TMATE_SERVER_HOST` |
| 3 | **`app` user context issue** | Check if tmate works as root but not as `app` (step 2) |

### Configuration gap

`docker-compose.yaml` does **not** pass `MOONMIND_TMATE_SERVER_*` env vars to `temporal-worker-agent-runtime`. The service relies on `.env` via `env_file`, but `MOONMIND_TMATE_SERVER_HOST` defaults to `""` in `.env-template`. To use a self-hosted relay, add to `.env`:

```
MOONMIND_TMATE_SERVER_HOST=your-relay.example.com
MOONMIND_TMATE_SERVER_PORT=22
MOONMIND_TMATE_SERVER_RSA_FINGERPRINT=SHA256:...
MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT=SHA256:...
```

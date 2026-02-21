# Quickstart: Worker Pause System

## Prerequisites
- MoonMind services running locally (`docker compose up api rabbitmq celery-worker orchestrator ...`).
- Operator credentials (OIDC session in the dashboard or an API token for curl) when auth is enabled.
- At least one Codex worker pointed at the local API so claim/heartbeat loops are active.
- Dashboard path: `https://localhost:8443/tasks` (or your local equivalent). The Worker Pause banner/controls are part of this global dashboard shell and remain visible even when no jobs are running.

If you run with `AUTH_PROVIDER=disabled`, omit API credentials and use the curl commands below without `Authorization` headers; this remains fully functional for local/dev mode.

```bash
AUTH_HEADER_ARGS=()
if [ -n "${TOKEN:-}" ]; then
  AUTH_HEADER_ARGS=(-H "Authorization: Bearer $TOKEN")
fi
```

## 1. Inspect the current state
```bash
curl -sS "${AUTH_HEADER_ARGS[@]}" \
  https://localhost:8443/api/system/worker-pause | jq
```
- Expect `workersPaused: false`, `version >= 1`, and `metrics` populated (`queued`, `running`, `staleRunning`, `isDrained`).
- The dashboard (“Tasks” tab) should show “Workers: Running” in the new global banner.

## 2. Pause in Drain mode
```bash
curl -sS -X POST \
  "${AUTH_HEADER_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d '{
        "action": "pause",
        "mode": "drain",
        "reason": "Rolling API migration"
      }' \
  https://localhost:8443/api/system/worker-pause | jq
```
- Response shows `workersPaused: true`, `mode: "drain"`, incremented `version`, and the request actor.
- Dashboard banner flips to “Workers: Paused (Drain)” and disables the Pause button until resume.

## 3. Verify claim guard + worker behavior
- Tail worker logs; each worker should log the pause reason once per `system.version` and sleep for `pause_poll_interval_ms` between claim attempts.
- Hitting the claim endpoint manually (`curl -sS -X POST /api/queue/jobs/claim ...`) returns `{ "job": null, "system": { ... } }` without invoking repository claim logic.
- Existing running jobs continue naturally and eventually finish, driving `metrics.running` toward 0.

## 4. Monitor drain progress & stale leases
- Re-run GET `/api/system/worker-pause` or watch the dashboard’s drain card. `metrics.isDrained` becomes `true` when both `running` and `staleRunning` reach 0.
- If you intentionally stop a worker mid-job, its lease shows up under `staleRunning` until the worker heartbeats again or resumes after maintenance.

## 5. Exercise Quiesce mode (optional but recommended)
```bash
curl -sS -X POST \
  "${AUTH_HEADER_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d '{
        "action": "pause",
        "mode": "quiesce",
        "reason": "Short-lived infra tweak"
      }' \
  https://localhost:8443/api/system/worker-pause | jq
```
- Heartbeat responses now contain `system.mode = "quiesce"`; workers pause at their next checkpoint while continuing to heartbeat (no lease expiry).
- Use the dashboard to confirm queued counts stay flat while running jobs report “Paused at checkpoint” in their logs.

## 6. Resume work
```bash
curl -sS -X POST \
  "${AUTH_HEADER_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d '{
        "action": "resume",
        "reason": "Maintenance complete"
      }' \
  https://localhost:8443/api/system/worker-pause | jq
```
- Response clears `mode`, sets `workersPaused: false`, and bumps `version` again. Workers log that the pause cleared and resume claiming immediately.
- If `metrics.isDrained` was `false`, the API returns a warning message and the dashboard highlights that you resumed before full drain.

## 7. Audit the control log
- Query the audit endpoint (GET) and confirm `audit.latest` includes both pause and resume entries with the supplied reasons.
- Optionally inspect the DB (`SELECT * FROM system_control_events ORDER BY created_at DESC LIMIT 5;`) to verify persistence, or watch the dashboard history list.

## 8. Dashboard banner & worker polling
- The Tasks dashboard now displays a global “Workers” banner with drain metrics and Pause/Resume forms. The controls talk to `/api/system/worker-pause`, so you can confirm end-to-end behavior without leaving the UI.
- Resuming while `isDrained=false` triggers a confirmation dialog; choosing “Continue” sets `forceResume=true` on the POST payload so the backend records that risk acceptance in the audit log.
- Workers respect pauses by default and back off according to `MOONMIND_PAUSE_POLL_INTERVAL_MS` (default `5000`). To shorten the polling cadence in local dev, export a different value before starting the Codex worker:  
  ```bash
  export MOONMIND_PAUSE_POLL_INTERVAL_MS=2000
  ```

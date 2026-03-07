# Quickstart: Worker Pause System

## Prerequisites
- MoonMind stack running locally (`docker compose up api queue-worker ...`).
- Operator auth token for calling `/api/system/worker-pause` in authenticated deployments.
- A queue worker (Codex worker CLI) pointed at the local API service.
- Dashboard path: `https://localhost:8443/tasks` (or your local equivalent). The Worker Pause controls are mounted in this global dashboard shell and remain visible even when no jobs are running.

With `AUTH_PROVIDER=disabled`, API requests may be sent without auth headers.

## Steps
1. **Verify baseline**
```bash
AUTH_HEADER_ARGS=()
if [ -n "${TOKEN:-}" ]; then
  AUTH_HEADER_ARGS=(-H "Authorization: Bearer $TOKEN")
fi

curl -sS "${AUTH_HEADER_ARGS[@]}" https://localhost:8443/api/system/worker-pause | jq
```
   - Expect `"workersPaused": false`, `version >= 1`, and the drain metrics.

2. **Pause in Drain mode**
```bash
curl -sS -X POST \
  "${AUTH_HEADER_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d '{
       "action": "pause",
       "mode": "drain",
       "reason": "Upgrading worker images"
     }' \
     https://localhost:8443/api/system/worker-pause | jq
   ```
   - Response shows `"workersPaused": true` and increments `version`.
   - Dashboard banner flips to “Workers: Paused (Drain)”.

3. **Confirm guards work**
   - Watch worker logs; new claim attempts print a pause message and sleep for the pause poll interval instead of erroring.
   - Issue `curl /api/queue/jobs/claim` manually and confirm response body contains `{ "job": null, "system": { ... } }` and HTTP 200.

4. **Observe drain progress**
   - Call `GET /api/system/worker-pause` periodically; `running` drops to 0 while `queued` stays constant.
   - The dashboard Drain panel shows “Safe to upgrade” when both `running` and `staleRunning` reach zero.

5. **Resume**
```bash
curl -sS -X POST \
  "${AUTH_HEADER_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d '{
       "action": "resume",
       "reason": "Deployment complete"
     }' \
     https://localhost:8443/api/system/worker-pause | jq
   ```
   - Response clears `mode`, sets `workersPaused` back to false, and increments `version` again.
   - Worker logs show “pause cleared” from heartbeat loop; claims resume immediately.

6. **Audit trail**
   - Query `system_control_events` via psql or SQLAlchemy shell to confirm both pause and resume actions recorded with actor + reason.

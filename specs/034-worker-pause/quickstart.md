# Quickstart: Worker Pause System

## Prerequisites
- MoonMind stack running locally (`docker compose up api queue-worker ...`).
- Operator auth token for calling `/api/system/worker-pause`.
- A queue worker (Codex worker CLI) pointed at the local API service.

## Steps
1. **Verify baseline**
   ```bash
   curl -sS -H "Authorization: Bearer $TOKEN" https://localhost:8443/api/system/worker-pause | jq
   ```
   - Expect `"workersPaused": false`, `version >= 1`, and the drain metrics.

2. **Pause in Drain mode**
   ```bash
   curl -sS -X POST \
     -H "Authorization: Bearer $TOKEN" \
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
     -H "Authorization: Bearer $TOKEN" \
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

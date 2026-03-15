# Quickstart: Projection Sync

This feature ensures that when you read an execution via the MoonMind API, the data is accurate according to the Temporal server, self-healing any local DB inconsistencies.

## Verification Steps
1. Start the services: `docker compose up -d`
2. Launch a workflow bypassing the local DB (e.g., via Temporal CLI directly).
3. Query the MoonMind API `/api/executions/{workflow_id}`.
4. Verify that the MoonMind API returns the correct data and successfully rehydrated the local DB projection.
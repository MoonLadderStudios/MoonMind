# Quickstart: Testing Temporal Execution Service Refactor

## Setup
1. Ensure Temporal server and MoonMind stack are running:
   ```bash
   docker compose up -d
   ```
2. Verify Temporal Web UI is accessible (usually `http://localhost:8088`).

## Validation Scenarios

### 1. View Executions (Read Authority)
- Open Mission Control UI (or hit the `/api/executions` list endpoint).
- Directly start a workflow via Temporal CLI or SDK, bypassing the MoonMind `/api/executions` create endpoint.
- Verify the new workflow correctly appears in the MoonMind execution list and detail views, showing that the system uses Temporal as the authoritative source of truth.

### 2. Trigger Execution Actions (Write Routing)
- Find an actively running workflow in Mission Control.
- Issue a `pause` or `cancel` action from the UI.
- Verify in Temporal Web UI that a corresponding signal (`pause`) or cancel request was received by the workflow and the state changed accordingly, rather than only the local database updating.
- Attempt an invalid action (e.g., `resume` on a completed workflow) and verify the error comes from Temporal's validation, not a local DB validation check.
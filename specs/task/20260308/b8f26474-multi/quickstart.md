# Quickstart

To validate the Temporal API consistency changes locally:

1. **Start the environment**:
   ```bash
   docker compose --profile temporal up -d
   ```

2. **Trigger a Temporal Execution**:
   Submit a task that invokes a Temporal worker. E.g.
   ```bash
   curl -X POST http://localhost:8000/api/executions -H "Content-Type: application/json" -d '{"workflowType": "MoonMind.Run"}'
   ```

3. **Verify Authoritative List**:
   ```bash
   curl http://localhost:8000/api/executions?source=temporal
   ```
   Check that `status`, `rawState`, `closeStatus`, and `waitingReason` reflect accurate state from the Temporal server.

4. **Verify Filters**:
   ```bash
   curl "http://localhost:8000/api/executions?source=temporal&state=running"
   ```

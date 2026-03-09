# Quickstart: Wire Temporal Artifacts

This quickstart explains how to verify that large payloads are no longer polluting the Temporal execution history.

## Running the Verification

1. Start the local Temporal server and MoonMind workers:
   ```bash
   docker compose --profile temporal-ui up -d
   ```

2. Trigger a `MoonMind.Run` task using the API (or CLI if unified):
   ```bash
   curl -X POST http://localhost:8000/api/executions 
     -H "Content-Type: application/json" 
     -d '{"workflowType": "MoonMind.Run", "initialParameters": {}}'
   ```

3. Open the Temporal Web UI (`http://localhost:8088`) and locate the execution.
4. Inspect the `ActivityTaskCompleted` events in the workflow history.
5. Verify that the `result` field of the event only contains short UUID-based reference strings (e.g. `plan_ref: "..."`) rather than raw MBs of text.
6. Check your configured Artifact Store (e.g., MinIO or local temp directory) to confirm that the raw output actually exists there under the generated reference ID.

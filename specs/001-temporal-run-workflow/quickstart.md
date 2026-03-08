# Quickstart

This guide shows how to test the new `MoonMind.Run` Temporal workflow.

1. **Start the Temporal Stack:**
   ```bash
   docker compose --profile temporal up -d
   ```

2. **Start the Worker:**
   ```bash
   # Ensure the worker is polling the correct queues
   docker compose up worker
   ```

3. **Trigger a Run:**
   ```bash
   curl -X POST http://localhost:8000/api/executions 
        -H "Content-Type: application/json" 
        -d '{"workflowType": "MoonMind.Run", "parameters": {}}'
   ```

4. **Verify in Temporal UI:**
   Navigate to `http://localhost:8088` and verify the execution exists and transitions through its phases.

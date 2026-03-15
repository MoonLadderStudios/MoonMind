# Quickstart: MoonMind.AgentRun Workflow

To test the newly created workflow locally using the integration testing framework:

1. Build and run the local Temporal cluster using:
   ```bash
   docker compose up -d temporal
   ```
2. Run the specific unit and integration tests using:
   ```bash
   ./tools/test_unit.sh -k "test_agent_run_workflow"
   ```
3. Look for the successful completion of tests demonstrating basic managed agent interaction and cancellation logic.

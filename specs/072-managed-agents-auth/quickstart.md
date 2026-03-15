# Quickstart: Managed Agents Authentication

1. Provision profiles in the database:
   Create entries in `managed_agent_auth_profiles` with varying `volume_ref` IDs.
2. Update `.env`:
   Make sure `GEMINI_VOLUME_PATH` or similar keys logic is cleared when appropriate.
3. Test a workflow:
   Launch a `MoonMind.AgentRun` via `/api/executions` to verify it can acquire a slot successfully.

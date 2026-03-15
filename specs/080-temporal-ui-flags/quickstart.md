# Quickstart: Temporal UI Actions and Submission Flags

## Running the Application with Temporal Features Enabled

1. By default, `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` and `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` are now set to `true`.
2. Start the local environment using:
   ```bash
   docker compose up --build
   ```
3. Navigate to `/tasks/new` to submit a task. The submission will directly trigger a Temporal workflow.
4. Open the task detail view. Depending on the current Temporal execution state, action buttons (e.g., Pause, Resume) will be visible and will interact with the backend API.

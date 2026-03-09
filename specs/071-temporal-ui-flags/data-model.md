# Data Model: Temporal UI Actions and Submission Flags

No database schema changes are required for this feature. The data model changes are limited to runtime configuration:

## Runtime Configuration `TemporalDashboardSettings`

- `actions_enabled` (bool): Defaults to `True`. Controls whether the UI exposes operational buttons like Pause, Resume, and Approve for Temporal workflows.
- `submit_enabled` (bool): Defaults to `True`. Controls whether `/tasks/new` submissions are routed directly to Temporal.

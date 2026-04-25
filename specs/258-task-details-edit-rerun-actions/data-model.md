# Data Model: Task Details Edit and Rerun Actions

## ExecutionActionCapabilityModel

Adds:

- `canEditForRerun: boolean`

Semantics:

- True for terminal `MoonMind.Run` executions where editing should create a new run from the original submission draft.
- False for non-terminal update-in-place statuses.
- Gated by the same task editing feature flag, workflow type, and original task input snapshot rules as rerun.

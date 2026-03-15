# Data Model: Temporal Projection Sync

## `TemporalExecutionRecord` (Existing with Sync Updates)
The following fields will be synchronized from Temporal:
- `workflow_id` (Primary correlation key)
- `run_id` (Mapped from latest Temporal Run ID)
- `status` (Mapped from Temporal workflow execution status)
- `state_machine_phase` (Mapped from `mm_state` search attribute)
- `waiting_reason` (Mapped from `mm_waiting_reason` search attribute, if any)
- `artifacts` (Mapped from `mm_artifacts` search attribute or execution results)

## Validation Rules
- Upsert logic requires `workflow_id` to be unique.
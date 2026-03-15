# Data Model: Refactor Execution Service to Temporal Authority

## Entities

### Execution
Represents a Temporal workflow run.

**Fields**:
- `workflowId` (string): Unique identifier for the workflow within the Temporal namespace.
- `runId` (string): Unique identifier for a specific run of the workflow.
- `status` (string): Current status of the workflow (e.g., RUNNING, COMPLETED, FAILED, CANCELED), sourced from Temporal.
- `state` (string): Business-level state machine phase.
- `searchAttributes` (dict): Attributes configured for filtering and searching in Temporal.

**Relationships**:
- N/A

**Validation Rules**:
- `workflowId` must be present for all Temporal API operations.

### Action / Signal
An operator-initiated command routed to a specific Temporal workflow execution.

**Fields**:
- `action_type` (enum): Type of action (e.g., pause, resume, cancel, update_title).
- `payload` (dict): Data associated with the action (if any).

**Relationships**:
- Targets a specific `Execution` via `workflowId`.

**Validation Rules**:
- Validated by Temporal workflow; the DB cache does not perform pre-validation on whether a signal is allowed at a given state.

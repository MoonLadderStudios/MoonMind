# Data Model

No new database tables are strictly required for this feature, as it primarily involves proxying requests and changing read-paths for existing models.

## Enhanced Execution Model (Python Pydantic)
- `TemporalExecutionRecord` mappings will need to cleanly reflect:
  - `status`: String (e.g. `running`, `completed`) mapped from Temporal's execution status.
  - `rawState`: Current state in the state machine (stored in Temporal Search Attributes or Memo).
  - `closeStatus`: Mapped from Temporal `WorkflowExecutionStatus`.
  - `waitingReason`: Extracted from workflow history or pending activities.

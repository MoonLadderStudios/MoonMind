# Data Model: MoonMind.AgentRun Workflow

## Entities

### `AgentExecutionRequest`
- **Fields**: `agent_kind`, `agent_id`, `execution_profile_ref`, `instruction_ref`, `input_refs`, `parameters`, `timeout_policy`, etc.
- **Role**: Defines the canonical request to execute a true agent runtime.

### `AgentRunHandle`
- **Fields**: `run_id`, `agent_kind`, `agent_id`, `status`, `started_at`
- **Role**: Used to track a launched run. Returned by adapter `start` operations.

### `AgentRunResult`
- **Fields**: `output_refs`, `summary`, `metrics`, `failure_class`, `provider_error_code`
- **Role**: Final result surface.

### `AgentRunStatus`
- **Role**: Enum representing lifecycle states (e.g. `queued`, `running`, `completed`, `failed`).

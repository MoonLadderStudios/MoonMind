# Data Model: Wire Temporal Artifacts

## Entities

### `ExecutionRef` (Value Object)
- **Description**: Identifies the context of an executing workflow. Used to build artifact paths.
- **Fields**:
  - `namespace` (string)
  - `workflow_id` (string)
  - `run_id` (string)
  - `link_type` (string): Identifies the artifact role (e.g. `input.plan`, `output.logs`).

### `ActivityOutput` (Interfaces)
- **Description**: The updated response signatures from activities, specifically designed to contain reference pointers rather than massive string blobs.
- **Fields**:
  - `plan.generate` returns `{"plan_ref": string}`
  - `sandbox.run_command` returns `{"diagnostics_ref": string}`
  - `integration.*.start` returns `{"tracking_ref": string}`
  - `manifest.process` (new) returns `{"summary_ref": string, "nodes_ref": string}`

## State Transitions
- Workflows fetch data from `ArtifactService` via the `*_ref` pointers when required for logic (typically they don't, they pass it to the UI or next activity).
- Activities push data to `ArtifactService` and return the `*_ref` to the workflow.

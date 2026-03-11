# Data Model: Skills and Plans Runtime Contracts

## Entity: ArtifactRef

- **Description**: Immutable reference envelope for large payloads moved outside workflow history.
- **Fields**:
  - `artifact_ref` (string; prefix `art:sha256:`)
  - `content_type` (string)
  - `bytes` (integer, `>= 0`)
  - `created_at` (ISO-8601 string)
  - `metadata` (object)
- **Rules**:
  - `artifact_ref` is opaque and immutable.
  - Artifact content for a digest cannot be overwritten with different bytes.
  - Large outputs are represented by artifact refs, not expanded inline.

## Entity: ToolDefinition

- **Description**: Registry contract describing one executable capability.
- **Fields**:
  - `name` (string)
  - `version` (string)
  - `description` (string)
  - `inputs.schema` (JSON schema object)
  - `outputs.schema` (JSON schema object)
  - `executor.activity_type` (string)
  - `executor.selector.mode` (string)
  - `requirements.capabilities` (non-empty array of strings)
  - `policies.timeouts.start_to_close_seconds` (integer > 0)
  - `policies.timeouts.schedule_to_close_seconds` (integer >= `start_to_close_seconds`)
  - `policies.retries.max_attempts` (integer > 0)
  - `policies.retries.non_retryable_error_codes` (array of strings)
  - `security.allowed_roles` (array of strings, optional)
- **Rules**:
  - Required fields must exist before execution.
  - Registry key `(name, version)` must be unique.
  - Activity binding is mandatory and cannot be inferred by interpreter.

## Entity: ToolRegistrySnapshot

- **Description**: Immutable, digest-pinned snapshot used by a plan execution.
- **Fields**:
  - `digest` (string; prefix `reg:sha256:`)
  - `artifact_ref` (string; snapshot artifact)
  - `skills` (array of `ToolDefinition`)
- **Rules**:
  - Plan execution resolves skills from this snapshot only.
  - Snapshot digest must correspond to canonical registry serialization.

## Entity: StepNode

- **Description**: One plan node invoking a pinned skill contract.
- **Fields**:
  - `id` (unique string within plan)
  - `skill.name` (string)
  - `skill.version` (string)
  - `inputs` (object)
  - `options.timeouts_override` (object, optional)
  - `options.retries_override` (object, optional)
- **Rules**:
  - Skill key must exist in pinned snapshot.
  - Inputs must validate against referenced skill input schema.
  - Overrides must stay within policy safety limits.

## Entity: PlanDefinition

- **Description**: DAG artifact containing execution metadata, policy, nodes, and edges.
- **Fields**:
  - `plan_version` (string; supported: `1.0`)
  - `metadata.title` (string)
  - `metadata.created_at` (ISO-8601 string)
  - `metadata.registry_snapshot.digest` (string)
  - `metadata.registry_snapshot.artifact_ref` (string)
  - `policy.failure_mode` (`FAIL_FAST` | `CONTINUE`)
  - `policy.max_concurrency` (integer > 0)
  - `nodes` (array of `StepNode`)
  - `edges` (array of `{from,to}`)
- **Rules**:
  - Graph must be acyclic.
  - Edges must reference known node IDs.
  - v1 semantics require dependencies to succeed before scheduling dependents.

## Entity: NodeInputReference

- **Description**: Deterministic pointer to prior node output.
- **Fields**:
  - `ref.node` (string node id)
  - `ref.json_pointer` (JSON Pointer string)
- **Rules**:
  - Referenced node must exist and be upstream-reachable via dependencies.
  - Pointer must resolve to valid output path for referenced skill schema.
  - Self-references are invalid.

## Entity: ToolResult

- **Description**: Normalized node execution output envelope.
- **Fields**:
  - `status` (`SUCCEEDED` | `FAILED` | `CANCELLED`)
  - `outputs` (small JSON object)
  - `output_artifacts` (array of `ArtifactRef`)
  - `progress` (object, optional incremental payload)
- **Rules**:
  - Large outputs must be represented through `output_artifacts`.
  - Result status drives downstream scheduling/failure policy behavior.

## Entity: ToolFailure

- **Description**: Standardized error envelope for validation, dispatch, and execution failures.
- **Fields**:
  - `error_code` (enum: `INVALID_INPUT`, `PERMISSION_DENIED`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, `TRANSIENT`, `TIMEOUT`, `EXTERNAL_FAILED`, `CANCELLED`, `INTERNAL`)
  - `message` (string)
  - `retryable` (boolean)
  - `details` (object)
  - `cause` (`ToolFailure`, optional)
- **Rules**:
  - Policy configuration controls retry behavior.
  - Non-retryable code lists stop retries immediately.

## Entity: PlanProgress

- **Description**: Queryable progress state snapshot for active execution.
- **Fields**:
  - `total_nodes` (int)
  - `pending` (int)
  - `running` (int)
  - `succeeded` (int)
  - `failed` (int)
  - `last_event` (string)
  - `updated_at` (ISO-8601 string)
- **Rules**:
  - Counts must stay internally consistent with node state machine.
  - Optional durable artifact can mirror latest progress payload.

## Entity: PlanExecutionSummary

- **Description**: Terminal run summary including per-node outcomes and artifact refs.
- **Fields**:
  - `status` (`SUCCEEDED` | `FAILED` | `PARTIAL`)
  - `results` (map node id -> `ToolResult`)
  - `failures` (map node id -> `ToolFailure`)
  - `skipped` (array of node ids)
  - `progress` (`PlanProgress`)
  - `progress_artifact_ref` (string nullable)
  - `summary_artifact_ref` (string nullable)
- **Rules**:
  - `FAIL_FAST` plus any failure yields terminal failure with unscheduled nodes skipped/cancelled.
  - `CONTINUE` allows independent branches to complete; terminal can be partial.

## State Transitions

- **Node lifecycle**:
  - `pending -> running -> succeeded`
  - `pending -> running -> failed`
  - `pending -> skipped` (failure policy cancellation or blocked dependencies)
- **Plan lifecycle**:
  - `validating -> executing -> succeeded`
  - `validating -> failed` (validation failure, no execution start)
  - `executing -> failed` (`FAIL_FAST` with failure)
  - `executing -> partial` (`CONTINUE` with mixed outcomes)

## Invariants

- Execution must not begin until validation succeeds.
- Ready set contains only nodes whose dependencies all succeeded.
- Registry lookup for nodes must always use plan-pinned snapshot.
- Progress and summary data must remain structured and machine-readable.

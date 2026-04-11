# Data Model: DooD Executable Tool Exposure

## DockerBackedWorkloadToolDefinition

Executable tool definition for a Docker-backed workload.

- **Fields**:
  - `name`: tool name; initially `container.run_workload` or `unreal.run_tests`
  - `version`: stable tool version such as `1.0`
  - `type`: executable tool subtype, always `skill`
  - `input_schema`: JSON-schema-compatible input contract
  - `output_schema`: JSON-schema-compatible output contract
  - `executor_activity_type`: `mm.tool.execute` or `mm.skill.execute`
  - `required_capabilities`: includes `docker_workload`
  - `policies`: timeout and retry defaults
  - `allowed_roles`: roles permitted to invoke the tool
- **Validation rules**:
  - Must have non-empty name and version.
  - Must declare `docker_workload` as the routing capability.
  - Must not expose raw image, mount, device, or arbitrary Docker parameters.
  - Must resolve from the pinned registry snapshot before execution.

## GenericWorkloadToolInput

Input shape for `container.run_workload`.

- **Fields**:
  - `profileId`: approved runner profile ID
  - `taskRunId`: optional explicit task/run identifier; defaults from execution context
  - `stepId`: optional explicit step identifier; defaults from execution context
  - `attempt`: optional step attempt; defaults from execution context or `1`
  - `repoDir`: task repository directory
  - `artifactsDir`: task artifacts directory for the step
  - `command`: command arguments for the workload profile to execute
  - `envOverrides`: environment values filtered by the selected runner profile
  - `timeoutSeconds`: optional bounded timeout override
  - `resources`: optional bounded resource override
  - `sessionId`, `sessionEpoch`, `sourceTurnId`: optional session association metadata
- **Validation rules**:
  - Required fields must be present before launch.
  - Environment overrides must pass runner-profile allowlist validation.
  - Workspace paths must pass workload registry workspace-root validation.
  - Resource and timeout overrides must not exceed selected profile limits.

## UnrealRunTestsToolInput

Input shape for `unreal.run_tests`.

- **Fields**:
  - `profileId`: optional runner profile ID; defaults to the curated Unreal profile
  - `taskRunId`, `stepId`, `attempt`: optional context identifiers
  - `repoDir`: task repository directory
  - `artifactsDir`: task artifacts directory for the step
  - `projectPath`: Unreal project file or project-relative path
  - `target`: optional Unreal target name
  - `testSelector`: optional test selector
  - `timeoutSeconds`, `resources`, `envOverrides`: bounded execution overrides
  - `sessionId`, `sessionEpoch`, `sourceTurnId`: optional session association metadata
- **Validation rules**:
  - `projectPath`, `repoDir`, and `artifactsDir` are required.
  - Domain fields are converted into a curated workload command before `WorkloadRequest` validation.
  - Raw Docker parameters remain disallowed.

## WorkloadToolExecutionContext

Runtime context supplied by the plan execution activity.

- **Fields**:
  - `workflow_id`: task/run identifier used when tool input omits `taskRunId`
  - `run_id`: Temporal run identifier used for diagnostics
  - `node_id`: plan step identifier used when tool input omits `stepId`
  - `attempt`: optional activity or step attempt
  - `session_id`, `session_epoch`, `source_turn_id`: optional managed-session grouping metadata
- **Validation rules**:
  - `workflow_id` and `node_id` must be available when the tool input omits explicit task or step identifiers.
  - Session metadata is grouping context only and must not alter workload identity semantics.

## WorkloadToolResult

Normal executable tool result derived from `WorkloadResult`.

- **Fields**:
  - `status`: normal tool status (`COMPLETED`, `FAILED`, or `CANCELLED`)
  - `workloadResult`: bounded workload result payload
  - `requestId`: workload request/container identity
  - `profileId`: selected runner profile
  - `workloadStatus`: workload-specific status (`succeeded`, `failed`, `timed_out`, `canceled`)
  - `exitCode`: workload exit code when available
  - `outputRefs`: declared output references when available
- **State mapping**:
  - `succeeded` -> `COMPLETED`
  - `failed` -> `FAILED`
  - `timed_out` -> `FAILED`
  - `canceled` -> `CANCELLED`
- **Validation rules**:
  - Result payloads must remain bounded and safe for workflow history.
  - Large logs and rich artifact publication remain outside Phase 3.

## Relationships

- A `DockerBackedWorkloadToolDefinition` is loaded from the pinned executable tool registry.
- A tool input plus `WorkloadToolExecutionContext` forms a `WorkloadRequest`.
- `RunnerProfileRegistry` validates the `WorkloadRequest` and selected profile.
- `DockerWorkloadLauncher` returns `WorkloadResult`.
- `WorkloadResult` maps to `WorkloadToolResult`.

# Data Model: DooD Workload Observability

## Workload Execution Evidence

Represents the durable evidence set for one finalized Docker-backed workload run.

**Fields**:

- `stdout_ref`: Reference to the runtime stdout artifact.
- `stderr_ref`: Reference to the runtime stderr artifact.
- `diagnostics_ref`: Reference to the runtime diagnostics artifact.
- `output_refs`: Mapping from artifact class or declared output name to artifact reference.
- `published_at`: Time the evidence set was finalized.

**Validation rules**:

- `stdout_ref`, `stderr_ref`, and `diagnostics_ref` must point to durable artifacts when the workload reaches finalization.
- `output_refs` must remain bounded to references and must not embed large output payloads.
- Default workload output classes must not use `session.summary`, `session.step_checkpoint`, `session.control_event`, or `session.reset_boundary`.

## Workload Execution Metadata

Bounded metadata describing one workload outcome for tool results, step ledgers, API responses, and UI presentation.

**Fields**:

- `request_id`: Stable workload request/container identity for the attempt.
- `task_run_id`: Owning task run.
- `step_id`: Producing plan step.
- `attempt`: Step/workload attempt number.
- `tool_name`: Executable tool that launched the workload.
- `profile_id`: Selected runner profile.
- `image_ref`: Image reference selected by the profile.
- `status`: Final workload status.
- `exit_code`: Process exit code when available.
- `started_at`: Workload start time when available.
- `completed_at`: Workload completion time when available.
- `duration_seconds`: Runtime duration when available.
- `timeout_reason`: Timeout reason when applicable.
- `cancel_reason`: Cancel reason when available.
- `labels`: Ownership labels used for traceability and cleanup.
- `session_context`: Optional session association context.
- `evidence`: Workload execution evidence references.

**Validation rules**:

- Metadata must be compact enough for workflow/tool payloads.
- `task_run_id`, `step_id`, `attempt`, `tool_name`, and `profile_id` are required for step linkage.
- `session_context` is optional and cannot change workload identity.
- Unsupported or unknown statuses must fail validation rather than silently mapping to success.

## Declared Output Artifact

Represents an output the workload caller expects the workload to produce.

**Fields**:

- `artifact_class`: Output class such as `output.primary`, `output.logs`, `output.summary`, `test.report`, or a domain-specific class.
- `relative_path`: Expected path under the workload artifacts directory.
- `artifact_ref`: Durable artifact reference when the declared output exists.
- `missing_reason`: Diagnostic reason when the declared output is absent or invalid.

**Validation rules**:

- `relative_path` must be relative and must stay under the approved workload artifacts directory.
- Declared output keys must not use session continuity artifact classes.
- Missing declared outputs are recorded in diagnostics without preventing stdout, stderr, or diagnostics publication.

## Workload-Step Linkage

Represents the relationship between a workload run and the execution step that produced it.

**Fields**:

- `task_run_id`
- `step_id`
- `attempt`
- `tool_name`
- `request_id`
- `workload_status`
- `artifact_refs`
- `diagnostics_ref`

**Validation rules**:

- Linkage must be visible through execution detail or internal step projection.
- The producing step owns the workload output references.
- Linkage must not create a `MoonMind.AgentRun` identity for ordinary workload containers.

## Session Association Context

Optional grouping context for workloads launched from managed-session-assisted steps.

**Fields**:

- `session_id`: Managed session identifier.
- `session_epoch`: Session epoch active when the workload was requested.
- `source_turn_id`: Session turn that requested or caused the workload launch.

**Validation rules**:

- `session_epoch` and `source_turn_id` require `session_id`.
- Association context is metadata only.
- Workload artifacts remain workload outputs and are not session continuity artifacts by default.

## State Transitions

```text
requested
  -> running
  -> finalizing_artifacts
  -> succeeded
  -> failed
  -> timed_out
  -> canceled
  -> artifact_publication_failed
```

Rules:

- Every terminal workload state that reaches finalization attempts to publish stdout, stderr, and diagnostics.
- `artifact_publication_failed` is operator-visible and must preserve any available partial refs or error diagnostics.
- Container cleanup and artifact finalization are separate concerns; cleanup does not erase durable artifacts.

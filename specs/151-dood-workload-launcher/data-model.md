# Data Model: Docker-Out-of-Docker Workload Launcher

## Validated Workload Request

Represents one control-plane-approved request to run a bounded workload container.

**Key fields**

- `profile_id`: Runner profile selected by the validated request.
- `task_run_id`: Task run that owns the workload.
- `step_id`: Step that produces the workload.
- `attempt`: Step/workload attempt number.
- `tool_name`: Logical tool or workload producer name.
- `repo_dir`: Task repository directory used as workload working directory.
- `artifacts_dir`: Task artifacts directory available for workload outputs.
- `command`: Command arguments to run through the selected profile.
- `env_overrides`: Profile-allowlisted environment overrides.
- `timeout_seconds`: Optional request timeout bounded by profile policy.
- `resources`: Optional request resource overrides bounded by profile policy.
- `session_id`, `session_epoch`, `source_turn_id`: Optional session association metadata used only for grouping context.

**Validation rules**

- The request must already pass profile lookup and policy validation before launch.
- `repo_dir` and `artifacts_dir` must remain under the configured workspace root.
- Environment keys must be explicitly allowed by the selected profile.
- Timeout and resource overrides must not exceed profile maxima.
- Session association metadata must not imply a managed agent run.

## Runner Profile

Represents the deployment-owned workload shape allowed for a launch.

**Key fields**

- `id`: Stable profile identifier.
- `image`: Approved image reference.
- `entrypoint` and `command_wrapper`: Profile-defined invocation wrapper.
- `workdir_template`: Expected workdir shape for workspace execution.
- `required_mounts`: Required Docker volume mounts such as the shared workspace.
- `optional_mounts`: Approved cache volumes such as Unreal cache volumes.
- `env_allowlist`: Environment keys the request may override.
- `network_policy`: Allowed network posture.
- `resources`: Default and maximum CPU, memory, and shared-memory controls.
- `timeout_seconds`, `max_timeout_seconds`: Default and maximum timeout controls.
- `cleanup`: Remove-on-exit and termination grace policy.
- `device_policy`: Device access posture.

**Validation rules**

- Normal execution must select a profile rather than pass arbitrary images or mounts.
- Mount sources must be deployment-owned named volumes.
- Mount targets must not expose host Docker authority.
- Unsafe network or device policies are rejected by profile validation.

## Workload Container

Represents the one-shot container launched by MoonMind for the validated request.

**Key attributes**

- Deterministic container name.
- `moonmind.*` ownership labels.
- Selected runner profile and image.
- Profile-approved mounts and network/resource policy.
- Working directory set to the task repository directory.

**Lifecycle states**

- `launching`: Docker invocation has started.
- `succeeded`: Container exited with code 0.
- `failed`: Container exited with non-zero status or launcher failed after start.
- `timed_out`: Timeout cleanup was triggered.
- `canceled`: Cancellation cleanup was triggered.

## Workload Result

Represents bounded execution metadata returned to the caller.

**Key fields**

- `request_id`: Workload/container identifier.
- `profile_id`: Selected runner profile.
- `status`: Workload outcome.
- `labels`: Deterministic ownership labels.
- `exit_code`: Container exit code when available.
- `started_at`, `completed_at`, `duration_seconds`: Timing metadata.
- `timeout_reason`: Timeout reason when applicable.
- `stdout_ref`, `stderr_ref`, `diagnostics_ref`: Future artifact references.
- `output_refs`: Declared output references for later artifact integration.
- `metadata`: Bounded diagnostics such as selected image and captured stream tails.

**Validation rules**

- Result metadata must stay bounded and must not embed large logs.
- Successful and failed runs both return a result shape suitable for later artifact publication.

## Cleanup Lookup

Represents label-based lookup for workload containers that may need cleanup.

**Key fields**

- `moonmind.kind=workload`
- `moonmind.task_run_id`
- `moonmind.step_id`
- `moonmind.attempt`
- `moonmind.tool_name`
- `moonmind.workload_profile`

**Validation rules**

- Lookup must filter by MoonMind ownership labels rather than broad, unlabeled Docker queries.
- Cleanup commands must target explicit container IDs or deterministic names.

# Data Model: Docker-Out-of-Docker Workload Contract

## WorkloadRequest

- `profile_id`: Required runner profile ID.
- `task_run_id`: Required task/run owner identifier.
- `step_id`: Required producing step identifier.
- `attempt`: Required positive attempt number.
- `tool_name`: Required executable tool name used for labels and diagnostics.
- `repo_dir`: Required repository working directory inside the shared workspace root.
- `artifacts_dir`: Required output artifact directory inside the shared workspace root.
- `command`: Required non-empty command argument list.
- `env_overrides`: Optional string key/value overrides limited by the selected profile allowlist.
- `timeout_seconds`: Optional positive timeout, capped by the selected profile.
- `resources`: Optional CPU/memory/shm overrides, capped by the selected profile.
- `session_id`: Optional session association context.
- `session_epoch`: Optional positive session epoch; valid only with `session_id`.
- `source_turn_id`: Optional source turn association context; valid only with `session_id`.

Validation rules:

- Profile ID must exist in the registry.
- Command must contain at least one non-blank argument.
- `repo_dir` and `artifacts_dir` must resolve under the registry workspace root.
- Env override keys must be valid uppercase environment names and must appear in the profile allowlist.
- Timeout/resource overrides must not exceed profile ceilings.
- Session metadata is association context only and never creates an agent-run identity.

## WorkloadResult

- `request_id`: Deterministic workload request/name identifier.
- `profile_id`: Selected profile ID.
- `status`: `succeeded`, `failed`, `timed_out`, or `canceled`.
- `labels`: Deterministic `moonmind.*` labels.
- `exit_code`: Optional process exit code.
- `started_at`: Optional start timestamp.
- `completed_at`: Optional completion timestamp.
- `duration_seconds`: Optional non-negative duration.
- `timeout_reason`: Optional timeout/cancel explanation.
- `stdout_ref`: Optional durable stdout artifact ref.
- `stderr_ref`: Optional durable stderr artifact ref.
- `diagnostics_ref`: Optional durable diagnostics artifact ref.
- `output_refs`: Optional declared output artifact refs.
- `metadata`: Bounded structured metadata, not large log content.

## RunnerProfile

- `id`: Stable profile ID selected by requests/tools.
- `kind`: `one_shot` for Phase 1.
- `image`: Approved image reference with explicit tag or digest.
- `entrypoint`: Optional command wrapper list.
- `command_wrapper`: Optional wrapper list appended before request command.
- `workdir_template`: Required template rooted in the shared workspace contract.
- `required_mounts`: Required mount contracts, usually `agent_workspaces` to `/work/agent_jobs`.
- `optional_mounts`: Optional deployment-owned cache mounts.
- `env_allowlist`: Environment variable names allowed in request overrides.
- `network_policy`: `none` or `bridge` for Phase 1.
- `resources`: Default and maximum CPU/memory/shm values.
- `timeout_seconds`: Default timeout.
- `max_timeout_seconds`: Maximum allowed request timeout.
- `cleanup`: Remove-on-exit and kill-grace policy.
- `device_policy`: `none` for Phase 1 unless explicitly modeled later.

Validation rules:

- Image must be non-blank and pinned by tag or digest; `latest` is rejected.
- Mounts must be Docker named volumes and must target approved container paths only.
- Env allowlist entries must be uppercase environment names.
- Network policy defaults to `none`; host networking is rejected.
- Device policy defaults to `none`; privileged/GPU access is not accepted in Phase 1.
- Resource maxima must be greater than or equal to defaults.

## WorkloadOwnershipMetadata

- `kind`: Always `workload`.
- `task_run_id`: Owner task run.
- `step_id`: Owner step.
- `attempt`: Positive attempt number.
- `tool_name`: Producing tool name.
- `workload_profile`: Runner profile ID.
- `session_id`: Optional grouping context.
- `session_epoch`: Optional grouping context.

Derived labels:

- `moonmind.kind=workload`
- `moonmind.task_run_id`
- `moonmind.step_id`
- `moonmind.attempt`
- `moonmind.tool_name`
- `moonmind.workload_profile`
- optional `moonmind.session_id`
- optional `moonmind.session_epoch`

## RunnerProfileRegistry

- `workspace_root`: Allowed shared workspace root.
- `profiles`: Mapping from profile ID to `RunnerProfile`.

Validation rules:

- Duplicate profile IDs are rejected.
- Unknown profile selections are rejected.
- Absent configuration yields an empty registry unless the caller explicitly provides profiles.

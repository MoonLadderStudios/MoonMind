# Data Model: Generic Task Container Runner

## Entity: ContainerTaskSpec

Represents task-level container execution intent.

- `enabled` (bool, required)
- `image` (string, required when enabled)
- `command` (list[string], required when enabled)
- `workdir` (string, optional)
- `env` (object<string,string>, optional)
- `artifactsSubdir` (string, optional; default `container`)
- `timeoutSeconds` (int, optional; default worker/system default)
- `resources` (object, optional)
  - `cpus` (number, optional)
  - `memory` (string, optional)
- `pull` (string enum `if-missing|always`, optional)
- `cacheVolumes` (list of `{name,target}`, optional)

## Entity: ContainerExecutionRequest

Worker-internal normalized structure derived from `ContainerTaskSpec` + prepared workspace.

- `job_id` (UUID)
- `container_name` (string)
- `image` (string)
- `command` (list[string])
- `workdir` (string)
- `artifact_dir_in_container` (string)
- `artifact_subdir` (string)
- `timeout_seconds` (int)
- `pull_mode` (enum)
- `resources` (optional normalized cpu/memory)
- `env` (dict)
- `mount_strategy` (workspace volume/bind config)

## Entity: ContainerExecutionResult

Normalized outcome used for events and metadata artifacts.

- `exit_code` (int|null)
- `timed_out` (bool)
- `duration_seconds` (float)
- `image` (string)
- `command_summary` (string)
- `container_name` (string)
- `started_at` (RFC3339 timestamp)
- `finished_at` (RFC3339 timestamp)
- `error` (string|null)

## Entity: ContainerArtifactLayout

Filesystem contract under per-job artifact root.

- `logs/execute.log`
- `<artifactsSubdir>/metadata/run.json`
- `<artifactsSubdir>/logs/runner.log` (when produced by runner)
- Additional task-specific files (optional)

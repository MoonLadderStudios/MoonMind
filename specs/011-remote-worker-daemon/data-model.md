# Data Model: Agent Queue Remote Worker Daemon (Milestone 3)

## Overview

Milestone 3 introduces runtime worker-side models (not new database tables) used to process queue jobs safely and deterministically.

## Runtime Entities

### CodexWorkerConfig

Represents environment-driven daemon configuration.

- `moonmind_url` (str): Base URL for MoonMind API.
- `worker_id` (str): Worker identity for claim/heartbeat/terminal updates.
- `worker_token` (str | None): Optional bearer token for authenticated requests.
- `poll_interval_ms` (int): Poll delay when no work is available (default 1500).
- `lease_seconds` (int): Queue lease duration used for claim/heartbeat (default 120).
- `workdir` (Path): Local root for checkout and temporary artifacts.

**Validation rules**:
- `moonmind_url` must be non-empty and normalized.
- `worker_id` must be non-empty.
- `poll_interval_ms` and `lease_seconds` must be > 0.

### QueueJobEnvelope

Represents job payload returned by claim endpoint.

- `id` (UUID)
- `type` (str)
- `payload` (dict[str, Any])
- `attempt` (int)
- `max_attempts` (int)

**Validation rules**:
- `type` must be recognized by worker dispatch (`codex_exec` for milestone scope).

### CodexExecPayload

Structured representation of `codex_exec` job payload.

- `repository` (str): Repository spec accepted by handler checkout strategy.
- `ref` (str | None): Optional branch/ref override.
- `workdir_mode` (str): `fresh_clone` or `reuse` behavior.
- `instruction` (str): Codex execution instruction string.
- `publish_mode` (str): `none`, `branch`, or `pr`.
- `publish_base_branch` (str | None)

**Validation rules**:
- `repository` and `instruction` are required non-empty strings.
- `workdir_mode`/`publish_mode` must be within allowed value set.

### WorkerExecutionResult

Normalized handler output consumed by terminal queue updates.

- `succeeded` (bool)
- `summary` (str | None)
- `error_message` (str | None)
- `artifact_paths` (list[Path])

**Validation rules**:
- If `succeeded` is `False`, `error_message` must be non-empty.

## State Transitions

1. Worker loop claims a queued job (`queued` -> `running`) via API.
2. Handler returns `WorkerExecutionResult`.
3. Worker uploads artifacts (if any).
4. Worker posts terminal transition:
   - success: `running` -> `succeeded`
   - failure: `running` -> `failed`
5. Heartbeat loop runs only while state remains `running`.

## Artifact Outputs

Per-job local worker artifact directory (`<workdir>/<job_id>/artifacts`) may contain:

- `codex_exec.log` (stdout/stderr capture)
- `changes.patch` (`git diff` output)
- `execution_summary.json` (optional structured metadata)

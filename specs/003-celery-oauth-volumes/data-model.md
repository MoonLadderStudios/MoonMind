# Data Model: Celery OAuth Volume Mounts

## Entity: CodexAuthVolume
- **Purpose**: Represents a persistent ChatGPT OAuth credential store linked to a single Codex worker.
- **Primary Identifier**: `name` (e.g., `codex_auth_0`).
- **Key Attributes**:
  - `name` (string, required, pattern `codex_auth_[0-9]+`).
  - `worker_affinity` (string, required) – human-readable label matching the Celery worker (e.g., `celery-codex-0`).
  - `last_verified_at` (timestamp, nullable) – set when pre-flight login check succeeds.
  - `status` (enum: `ready`, `needs_auth`, `error`) – reflects latest health check result.
  - `notes` (text, optional) – operator-supplied remediation or context.
- **Relationships**:
  - One-to-many with `SpecAutomationRun` (a volume may serve many runs over time).
  - One-to-one logical mapping with `CodexWorkerShard` (each shard references exactly one volume).
- **Validation Rules**:
  - `status=ready` requires `last_verified_at` within configured freshness window (default 24h).
  - Volume name must remain unique and immutable once assigned to a shard.

## Entity: CodexWorkerShard
- **Purpose**: Describes a Celery worker dedicated to Codex processing.
- **Primary Identifier**: `queue_name` (e.g., `codex-0`).
- **Key Attributes**:
  - `queue_name` (string, pattern `codex-[0-9]+`).
  - `volume_name` (string, foreign key to `CodexAuthVolume.name`).
  - `hash_modulo` (integer, default 3) – number of shards used by routing helper at the time of the run.
  - `status` (enum: `active`, `draining`, `offline`).
  - `worker_hostname` (string) – runtime identifier of the Celery worker instance.
- **Relationships**:
  - One-to-one with `CodexAuthVolume` during active service.
  - One-to-many with `SpecAutomationRun` as runs are routed to the shard.
- **Validation Rules**:
  - `status=active` requires a mounted volume and heartbeat from Celery worker.
  - `hash_modulo` must match global routing configuration to keep deterministic mapping consistent.

## Entity: SpecAutomationRun (extended fields)
- **Purpose**: Existing record for Spec Kit executions augmented with Codex routing metadata.
- **Key Attributes (additions)**:
  - `codex_queue` (string) – queue the Codex phase executed on.
  - `codex_volume` (string) – identifier of the auth volume mounted for the run.
  - `codex_preflight_status` (enum: `passed`, `failed`, `skipped`).
  - `codex_preflight_message` (text) – operator-facing details on failure.
- **Relationships**:
  - Many-to-one with `CodexWorkerShard` and `CodexAuthVolume`.
- **Validation Rules**:
  - `codex_queue` and `codex_volume` must be populated whenever a run enters the Codex submission phase.
  - `codex_preflight_status=failed` requires a non-empty `codex_preflight_message`.

## Lifecycle & State Transitions

### CodexAuthVolume
- `needs_auth` → `ready`: Set after successful interactive `codex login` and pre-flight check passes.
- `ready` → `error`: Triggered when login status check fails (e.g., token expired) and run is blocked.
- `error` → `needs_auth`: Assigned when remediation is initiated but not yet validated.

### CodexWorkerShard
- `offline` → `draining`: Worker is booting or being removed; queue remains but jobs are avoided.
- `draining` → `active`: Worker online, heartbeat confirmed, volume mounted.
- `active` → `draining`: Maintenance mode or planned rotation.
- `draining` → `offline`: Worker stopped; routing should rebalance once hash configuration updates.

### SpecAutomationRun
- On entering Codex phase, record `codex_queue`, `codex_volume`, and `codex_preflight_status`.
- If pre-flight fails, run transitions to terminal error state with remediation message and does not execute Codex steps.
- Successful Codex execution logs artifacts referencing the same queue/volume for audit trails.

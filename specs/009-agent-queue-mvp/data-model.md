# Data Model: Agent Queue MVP (Milestone 1)

**Feature**: Agent Queue MVP  
**Branch**: `009-agent-queue-mvp`

## Core Entity: AgentJob

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | UUID | Yes | Primary key for queue jobs |
| `type` | String | Yes | Job type (`codex_exec`, `codex_skill`, `report`, etc.) |
| `status` | Enum | Yes | `queued`, `running`, `succeeded`, `failed`, `cancelled` |
| `priority` | Integer | Yes | Higher numbers are claimed first |
| `payload` | JSON | Yes | Job-specific request payload |
| `created_by_user_id` | UUID | No | User identity that created the job |
| `requested_by_user_id` | UUID | No | Upstream requester identity |
| `affinity_key` | String | No | Optional routing key |
| `claimed_by` | String | No | Worker id that currently owns the lease |
| `lease_expires_at` | Timestamp | No | Lease expiry for running jobs |
| `attempt` | Integer | Yes | Current attempt counter |
| `max_attempts` | Integer | Yes | Max attempts before hard failure |
| `result_summary` | Text | No | Completion summary |
| `error_message` | Text | No | Failure reason |
| `artifacts_path` | String | No | Reserved path for artifact location |
| `created_at` | Timestamp | Yes | Creation time |
| `updated_at` | Timestamp | Yes | Last update time |
| `started_at` | Timestamp | No | First run start time |
| `finished_at` | Timestamp | No | Terminal state time |

## Supporting Value Object: Claim Request

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `worker_id` | String | Yes | Worker identity claiming a job |
| `lease_seconds` | Integer | Yes | Lease duration to add on claim |
| `allowed_types` | List[String] | No | Optional type filter for eligible jobs |

## State Transitions

| From | To | Allowed | Notes |
|------|----|---------|-------|
| `queued` | `running` | Yes | Claim operation |
| `running` | `running` | Yes | Heartbeat lease extension only |
| `running` | `succeeded` | Yes | Complete operation |
| `running` | `failed` | Yes | Fail operation |
| `queued` | `cancelled` | Yes | Optional admin cancellation path |
| `succeeded` | Any | No | Terminal |
| `failed` | Any | No | Terminal |
| `cancelled` | Any | No | Terminal |

## Concurrency and Lease Rules

- Claim must be transactional with row locking (`FOR UPDATE SKIP LOCKED`).
- Expired running jobs are reprocessed before selecting a queued job.
- Heartbeat, complete, and fail require worker ownership (`claimed_by` matches caller).
- Lifecycle updates must refresh `updated_at`; terminal transitions set `finished_at`.

## Validation Rules

- `lease_seconds` must be positive and bounded by service policy.
- `priority` defaults to `0` when omitted.
- `attempt` starts at `1`; `max_attempts` defaults to `3`.
- `status` must be one of the queue enum values.

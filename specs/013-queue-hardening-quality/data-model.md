# Data Model: Agent Queue Hardening and Quality (Milestone 5)

## Entity: AgentWorkerToken

Represents one dedicated worker credential and policy envelope.

- **Fields**:
  - `id` (UUID, PK)
  - `worker_id` (string, required)
  - `token_hash` (string, required, unique)
  - `description` (string, optional)
  - `allowed_repositories` (JSON array of strings, optional)
  - `allowed_job_types` (JSON array of strings, optional)
  - `capabilities` (JSON array of strings, optional)
  - `is_active` (bool, default true)
  - `created_at`, `updated_at`
- **Validation rules**:
  - `token_hash` required and unique.
  - Arrays contain non-empty normalized strings.
  - Inactive tokens cannot authenticate worker operations.

## Entity: AgentJob (extensions)

Existing queue entity gains retry scheduling + dead-letter support.

- **New/updated fields**:
  - `status` adds `dead_letter` enum value.
  - `next_attempt_at` (timestamp nullable): earliest time job may be claimed.
- **Derived behavior**:
  - Claim eligibility requires `status = queued` and `(next_attempt_at is null or next_attempt_at <= now)`.

## Entity: AgentJobEvent

Append-only lifecycle/progress event per job.

- **Fields**:
  - `id` (UUID, PK)
  - `job_id` (UUID FK -> `agent_jobs.id`)
  - `level` (`info|warn|error`)
  - `message` (text, required)
  - `payload` (JSON object nullable)
  - `created_at` (timestamp)
- **Validation rules**:
  - Message must be non-empty.
  - Level constrained to enum set.
  - Events are immutable after insert.

## Value Object: WorkerAuthContext

Runtime context resolved from incoming credentials for worker endpoints.

- **Fields**:
  - `worker_id` (string)
  - `auth_source` (`worker_token` or `oidc`)
  - `allowed_repositories` (set[str] | None)
  - `allowed_job_types` (set[str] | None)
  - `capabilities` (set[str])
- **Usage**:
  - Enforces request worker identity match.
  - Provides policy filters for claim operations.

## Job State Transitions (Milestone 5)

- `queued -> running` on successful claim (eligible, authorized, capability match).
- `running -> succeeded` on complete.
- `running -> failed` on non-retryable fail.
- `running -> queued` on retryable fail when attempts remain (`attempt++`, `next_attempt_at` set by backoff).
- `running -> dead_letter` on retry exhaustion.
- `running -> queued/dead_letter` on lease expiry depending on attempt limit.

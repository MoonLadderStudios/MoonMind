# Data Model: Worker Pause System

## 1. `system_worker_pause_state`

| Column | Type | Constraints / Default | Notes |
| --- | --- | --- | --- |
| `id` | integer | PK, default `1` | Singleton row keyed to 1. Migration seeds it immediately. |
| `paused` | boolean | not null, default `false` | Blocks claims when true. |
| `mode` | enum/text | `CHECK (mode IN ('drain','quiesce'))`, nullable when `paused=false` | Mirrors DOC-REQ Pause modes. |
| `reason` | text | nullable | Operator-supplied reason; persisted for audit + UI. |
| `requested_by_user_id` | uuid | FK `user.id`, nullable | OIDC user initiating the last action (when available). |
| `requested_at` | timestamptz | nullable | Timestamp when the current pause request was first accepted. |
| `updated_at` | timestamptz | not null, default `now()` on insert/update | Reflects the last state transition. |
| `version` | bigint | not null, default `1` | Monotonic counter incremented atomically every time any field changes so workers can diff versions. |

Implementation details:
- SQLAlchemy model lives alongside other queue models and inherits `Base`.
- Repository helper always `SELECT ... FOR UPDATE` the row before mutating so simultaneous POSTs serialize.
- Resume actions set `paused=false`, `mode=NULL`, keep the previous `reason` only if a resume reason is provided, and bump `version`.

## 2. `system_control_events`

| Column | Type | Constraints / Default | Notes |
| --- | --- | --- | --- |
| `id` | uuid | PK | Generated per audit row. |
| `control` | text | not null, default `'worker_pause'` | Future-proof for other controls. |
| `action` | text | not null | `'pause'` or `'resume'`. Enforced via validator. |
| `mode` | text | nullable | Captures drain/quiesce for pause entries. |
| `reason` | text | not null | Copied from operator reason (resume reason optional but recommended). |
| `actor_user_id` | uuid | FK `user.id`, nullable | Operator identity when the request came via authenticated API. |
| `created_at` | timestamptz | not null, default `now()` | Sorted descending when serving audit trails. |

Indexes:
- `(control, created_at DESC)` for quick retrieval of the latest N events.
- Optional partial index on `(action)` if we ever need dedicated pause/resume counts.

## 3. Derived Metrics (no table)

`AgentQueueRepository` exposes a `WorkerPauseMetrics` dataclass populated by single-pass COUNT queries:
- `queuedCount`: `COUNT(*)` where `status = 'queued'` and `next_attempt_at` is `NULL` or `<= now()`.
- `runningCount`: `COUNT(*)` where `status = 'running'`.
- `staleRunningCount`: `COUNT(*)` where `status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at < now())`.
- `isDrained`: computed boolean `runningCount == 0 && staleRunningCount == 0`.

Because these are read-only aggregations they carry zero write risk during a pause and satisfy DOC-REQ-006/010 by surfacing drain progress plus stale leases.

## 4. API / Worker schema mapping

The REST, MCP, and worker clients all reuse the same logical schemas:
- `QueueSystemMetadataModel`: `{ workersPaused: bool, mode: 'drain'|'quiesce'|null, reason: str|null, version: int, requestedAt: datetime|null, updatedAt: datetime }`.
- `WorkerPauseSnapshotResponse`: wraps the metadata, `metrics`, and `audit.latest` array of `SystemControlEventModel` entries.

`system` payload inclusion rules:
- Claim responses always return `system`, even when `job` is present.
- Heartbeat and other job fetches attach `system` to the top-level job JSON so running workers can detect quiesce transitions without schema churn elsewhere.

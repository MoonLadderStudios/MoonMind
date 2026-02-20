# Data Model: Worker Pause System

## 1. `system_worker_pause_state`

| Column | Type | Constraints | Notes |
| ------ | ---- | ----------- | ----- |
| `id` | integer | PK, default 1 | Singleton row. Create eagerly if missing. |
| `paused` | boolean | not null, default false | Blocks claims when true. |
| `mode` | text | check (`drain`,`quiesce`) or `NULL` when resumed | Mirrors doc-defined modes. |
| `reason` | text | nullable | Operator-supplied description for audits/UI banners. |
| `requested_by_user_id` | uuid | FK `user.id`, nullable | Actor that initiated the most recent pause/resume. |
| `requested_at` | timestamptz | nullable | First timestamp when pause was requested (for drain progress). |
| `updated_at` | timestamptz | not null, default now() | Touch on every toggle. |
| `version` | bigint | not null, default 1 | Monotonic counter incremented on each state change; workers log once per version. |

Implementation details:
- Use SQLAlchemy model on `moonmind/workflows/agent_queue/models.py` with `server_default=func.now()` for timestamps.
- Repository helper ensures the row exists and runs `SELECT ... FOR UPDATE` during toggle.

## 2. `system_control_events`

| Column | Type | Constraints | Notes |
| ------ | ---- | ----------- | ----- |
| `id` | uuid | PK | Generated per event. |
| `control` | text | not null | Fixed string `"worker_pause"` for now. |
| `action` | text | not null | `pause` or `resume`. |
| `mode` | text | nullable | `drain`/`quiesce` retained for pause events; `NULL` allowed on resume. |
| `reason` | text | not null | Mirrors operator-supplied text (resume reasons allowed). |
| `actor_user_id` | uuid | FK `user.id`, nullable | Operator identity when available (local auth disabled -> `NULL`). |
| `created_at` | timestamptz | not null, default now() | Enables chronological audit feeds. |

Events let ops audit toggles separately from the mutable singleton row.

## 3. Derived metrics view (in service layer)

No physical table is added for metrics. The queue service computes:
- `queued_count`: `COUNT(*)` of `agent_jobs` where `status='queued'` and `next_attempt_at` is ready.
- `running_count`: `COUNT(*)` of `agent_jobs` where `status='running'`.
- `stale_running_count`: subset of running jobs whose `lease_expires_at < NOW()` or `NULL`.
- `is_drained`: `running_count == 0 and stale_running_count == 0`.

Expose the metrics via `WorkerPauseMetrics` dataclass + API model so GET `/api/system/worker-pause` can show drain progress.

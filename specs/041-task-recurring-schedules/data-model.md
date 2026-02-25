# Data Model: Task Recurring Schedules System

## Entity: RecurringTaskDefinition

- **Description**: Persistent schedule definition describing what to run, when to run it, and policy behavior.
- **Primary key**: `id` (UUID)
- **Core fields**:
  - `name` (string, required, trimmed)
  - `description` (string, optional)
  - `enabled` (bool)
  - `schedule_type` (enum: `cron`)
  - `cron` (string, required, 5-field minute-level cron)
  - `timezone` (string, required, IANA TZ)
  - `next_run_at` (timestamptz, nullable)
  - `last_scheduled_for` (timestamptz, nullable)
  - `last_dispatch_status` (string, nullable)
  - `last_dispatch_error` (text, nullable)
  - `owner_user_id` (UUID, nullable for global schedules)
  - `scope_type` (enum: `personal`, `team`, `global`; API v1 uses `personal` + `global`)
  - `scope_ref` (string, nullable)
  - `target` (JSON object, required)
  - `policy` (JSON object, required)
  - `version` (bigint, optimistic update/version marker)
  - `created_at`, `updated_at` (timestamptz)
- **Indexes**:
  - `(enabled, next_run_at)` for due scans
  - `(owner_user_id, enabled)` for owner listing
- **Validation rules**:
  - `cron` must parse as five fields (no seconds support)
  - `timezone` must resolve through `zoneinfo`
  - `target.kind` must be one of `queue_task`, `queue_task_template`, `manifest_run`, `housekeeping`
  - Secret-like values are rejected in persisted target/policy payloads (FR-020)

## Entity: RecurringTaskRun

- **Description**: One schedule occurrence decision (scheduled or manual), including dispatch result and linkage to downstream queue artifacts.
- **Primary key**: `id` (UUID)
- **Core fields**:
  - `definition_id` (UUID FK -> `RecurringTaskDefinition.id`)
  - `scheduled_for` (timestamptz, required)
  - `trigger` (enum: `schedule`, `manual`)
  - `outcome` (enum: `pending_dispatch`, `enqueued`, `skipped`, `dispatch_error`)
  - `dispatch_attempts` (int)
  - `dispatch_after` (timestamptz, nullable)
  - `queue_job_id` (UUID, nullable)
  - `queue_job_type` (string, nullable)
  - `message` (text, nullable)
  - `created_at`, `updated_at` (timestamptz)
- **Constraints**:
  - Unique `(definition_id, scheduled_for)` prevents duplicate logical occurrences
- **Indexes**:
  - `(definition_id, created_at)` for history pages
  - `(outcome, dispatch_after)` for dispatch queues

## Entity: RecurringTarget (JSON union in `RecurringTaskDefinition.target`)

- **Description**: Target-specific dispatch payload.
- **Variants**:
  - `queue_task`
    - `job.type` must be `task`
    - `job.payload` required
  - `queue_task_template`
    - `template.slug` + `template.version` required
    - optional `template.inputs`, `jobDefaults`, `taskDefaults`
  - `manifest_run`
    - `name` required
    - `action` in `{run, plan}`
    - optional `options` object
  - `housekeeping`
    - `action` required
    - optional `args` object
- **Invariant**: Every dispatched payload receives recurrence metadata under `payload.system.recurrence`.

## Entity: RecurringPolicy (JSON object in `RecurringTaskDefinition.policy`)

- **Description**: Execution behavior controls for overlaps, catchup, and dispatch timing.
- **Fields**:
  - `overlap.mode`: `skip` or `allow`
  - `overlap.maxConcurrentRuns`: integer >= 1
  - `catchup.mode`: `none`, `last`, or `all`
  - `catchup.maxBackfill`: integer >= 1, capped by global scheduler max backfill
  - `misfireGraceSeconds`: integer >= 0
  - `jitterSeconds`: integer >= 0
- **Defaults**:
  - overlap `skip` + `maxConcurrentRuns=1`
  - catchup `last` + `maxBackfill=3`
  - `misfireGraceSeconds=900`
  - `jitterSeconds=0`

## Entity: RecurrenceMetadata

- **Description**: Provenance block attached to queued jobs.
- **Shape**:
  - `definitionId` (UUID string)
  - `runId` (UUID string)
  - `scheduledFor` (UTC ISO-8601 string)
- **Usage**:
  - Queue-job provenance in dashboard/detail views
  - Dispatch reconciliation to avoid duplicate enqueue after uncertain outcomes

## Entity: SchedulerConfig

- **Description**: Runtime scheduler loop controls from environment/settings.
- **Fields**:
  - `poll_interval_ms` (`MOONMIND_SCHEDULER_POLL_INTERVAL_MS`, min 250)
  - `batch_size` (`MOONMIND_SCHEDULER_BATCH_SIZE`, min 1)
  - `max_backfill` (`MOONMIND_SCHEDULER_MAX_BACKFILL`, min 1)
  - optional lock timeout (`MOONMIND_SCHEDULER_LOCK_TIMEOUT_SECONDS`)

## Relationships

- One `RecurringTaskDefinition` has many `RecurringTaskRun` records.
- One `RecurringTaskRun` may link to zero or one queue job (`queue_job_id`), depending on target type and dispatch outcome.
- `RecurringTarget` and `RecurringPolicy` are embedded JSON contracts scoped to a definition version.

## State Transitions

- **Definition lifecycle**:
  - `enabled=true` participates in due scans.
  - `enabled=false` keeps history but blocks new scheduled occurrences.
- **Run lifecycle**:
  - `pending_dispatch` -> `enqueued`
  - `pending_dispatch` -> `skipped`
  - `pending_dispatch` -> `dispatch_error`
  - `dispatch_error` -> `enqueued` (retry/reconciliation success)
  - `dispatch_error` -> `skipped` (policy/misfire decision)

## Invariants

- No duplicate run rows for the same definition occurrence.
- Scheduler advances `next_run_at` even when due occurrences are skipped, preventing repeated due-loop churn.
- Recurrence metadata must be present on all queue-backed dispatches.
- Persisted definitions must not contain raw secret literals.

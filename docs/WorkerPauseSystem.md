# Worker Pause System

Status: **Proposed**
Owners: **MoonMind Engineering**
Last Updated: **2026-02-19**

---

## 1. Problem Statement

MoonMind needs a **single, operator-driven “pause all workers” switch** so upgrades (image rebuilds, schema migrations, credential rotations, etc.) can be performed **without tasks entering weird intermediate states**, and with the **queue remaining intact** (jobs should stay queued; no surprise retries or dead-lettering).

Today, the queue’s claim path is lease-based and can requeue expired running jobs during claim normalization (the repository claim flow calls `_requeue_expired_jobs` before selecting queued work , and `_requeue_expired_jobs` can move expired running jobs back to `queued` or `dead_letter` ). During upgrades/restarts, that can create “why did this job retry / requeue?” surprises.

---

## 2. Goals

1. **Stop new work from starting** across all queue workers (Codex/Gemini/Claude/Manifest/Universal) by blocking claims.
2. Keep the queue **undisturbed**: queued jobs remain `queued`, and the claim endpoint does not mutate queue state while paused.
3. Provide a **clear, auditable operator control** (API + Dashboard) with reason + timestamps.
4. Support an upgrade-friendly workflow:

   * **Pause → Drain → Upgrade → Resume**
5. Optional “quiesce” mode: allow operators to request that **running jobs pause at safe checkpoints** (similar in spirit to existing pause-at-checkpoint behavior described for per-run controls ), while workers continue heartbeating.

---

## 3. Non-Goals (v1)

* Forcibly freezing/killing subprocesses mid-command (“hard stop the world”).
* Cross-worker, crash-proof **checkpoint/restore** of in-flight jobs after worker termination.
* Pausing **every subsystem** in MoonMind (e.g., independent Celery chains) with identical semantics. This design targets the **Task Queue** contract and claim lifecycle (queued → running → terminal ).

---

## 4. Concept Overview

### 4.1 Pause Modes

* **Drain (default / recommended for upgrades)**

  * Block new claims immediately.
  * Running jobs continue to completion.
  * Operator waits until running count hits zero → safe to restart workers.

* **Quiesce (optional)**

  * Block new claims.
  * Running jobs are asked to pause at safe checkpoints (stage boundaries / step boundaries), while still heartbeating to preserve leases.

### 4.2 Why “Drain” is the primary upgrade path

The Task Queue’s statuses and stage model are designed around `queued → running → terminal` with a wrapper stage plan (`prepare → execute → publish`) . The simplest way to avoid weird intermediate task state during upgrades is to ensure **no jobs are running** when containers restart.

---

## 5. Architecture

### 5.1 Components

1. **System Pause State (DB, singleton)**

   * Source of truth for whether claims are allowed.

2. **Queue Claim Endpoint Guard**

   * `POST /api/queue/jobs/claim` returns “paused” metadata and **does not call** repository claim logic while paused, preventing normalization side effects like lease-expiry requeues.

3. **Worker Runtime Behavior**

   * Workers treat “paused” claim responses as an idle loop (sleep/backoff).
   * In Quiesce mode, running workers learn about pause state via heartbeat responses and pause at safe checkpoints.

4. **Dashboard**

   * Global banner + a “Pause Workers / Resume Workers” control.
   * Drain progress indicator (running count).

---

## 6. Data Model

### 6.1 `system_worker_pause_state` (new)

Singleton row keyed by `id = 1`.

| Column                 | Type         | Notes                                    |
| ---------------------- | ------------ | ---------------------------------------- |
| `id`                   | int (pk)     | Always 1                                 |
| `paused`               | bool         | If true, new claims are blocked          |
| `mode`                 | enum         | `drain` | `quiesce`                      |
| `reason`               | text         | Operator-provided                        |
| `requested_by_user_id` | uuid fk user | Nullable (local disabled auth)           |
| `requested_at`         | timestamptz  | First time pause enabled                 |
| `updated_at`           | timestamptz  | Updated on every change                  |
| `version`              | bigint       | Monotonic increment for change detection |

### 6.2 `system_control_events` (new, append-only audit)

| Column          | Type         | Notes                  |
| --------------- | ------------ | ---------------------- |
| `id`            | uuid pk      |                        |
| `control`       | text         | `"worker_pause"`       |
| `action`        | text         | `"pause"` | `"resume"` |
| `mode`          | text         | `drain` | `quiesce`    |
| `reason`        | text         |                        |
| `actor_user_id` | uuid fk user | Nullable               |
| `created_at`    | timestamptz  |                        |

---

## 7. API Surface

### 7.1 System pause endpoints (operator-only)

* `GET /api/system/worker-pause`

  * Returns current pause state + computed drain metrics:

    * queued count
    * running count
    * `isDrained = (running == 0)`

* `POST /api/system/worker-pause`

  * Body:

    ```json
    {
      "action": "pause" | "resume",
      "mode": "drain" | "quiesce",
      "reason": "Upgrading images"
    }
    ```

### 7.2 Queue endpoints: response extensions (worker-visible)

**Extend** these existing endpoints (the Task Queue system already defines the base REST surface ):

* `POST /api/queue/jobs/claim`

  * Current response is `{ job: JobModel | null }`
  * Extend to:

    ```json
    {
      "job": null,
      "system": {
        "workersPaused": true,
        "mode": "drain",
        "reason": "Upgrading images",
        "version": 12,
        "updatedAt": "..."
      }
    }
    ```
  * When `workersPaused=true`, the handler must return early **before** invoking repository claim logic (which otherwise calls `_requeue_expired_jobs` ).

* `POST /api/queue/jobs/{jobId}/heartbeat`

  * Extend similarly so running workers can react (Quiesce mode):

    ```json
    {
      "job": { ... },
      "system": {
        "workersPaused": true,
        "mode": "quiesce",
        "reason": "...",
        "version": 12
      }
    }
    ```

### 7.3 MCP tools (optional but recommended)

Since MCP tools map to the same queue service methods , propagate the same `system` envelope for:

* `queue.claim`
* `queue.heartbeat`

---

## 8. Worker Behavior

### 8.1 Claim loop behavior (all worker runtimes)

When `claim` returns `job=null` with `system.workersPaused=true`:

* Worker does **not** treat it as an error.
* Worker sleeps with a pause-aware backoff:

  * default: `poll_interval_ms`
  * optional: `pause_poll_interval_ms` (e.g., 3–10s) to reduce API spam
* Worker logs pause status once per `system.version` (avoid log spam).

### 8.2 Running job behavior (Quiesce mode)

In Quiesce mode, the worker should:

* Continue heartbeating to preserve its lease.
* Pause **at safe checkpoints**, e.g.:

  * between wrapper stages (`prepare` → `execute` → `publish`)
  * between `task.steps[]` boundaries
  * between tool invocations / command executions

This aligns with the existing per-run “soft pause at checkpoints” idea (print “paused” and stop issuing new tool calls) , but is driven by system state rather than per-run control flags.

**Important**: Quiesce is meant for short maintenance where workers stay alive. For full worker restarts, Drain is the safe path.

---

## 9. Claim Guard Semantics (Queue Undisturbed)

### 9.1 Required behavior while paused

While `paused=true`:

* `POST /api/queue/jobs/claim` must:

  * **not** invoke repository `claim_job`
  * **not** trigger `_requeue_expired_jobs` normalization
  * return `{job:null, system:{workersPaused:true,...}}`

This prevents paused workers from inadvertently mutating job state (especially lease-expiry normalization which can requeue/dead-letter jobs ).

### 9.2 What happens to lease expiration?

* In **Drain**: running jobs keep heartbeating, so leases should not expire.
* If operators **stop workers anyway** while jobs are running, leases may expire and later be normalized into retries. That’s expected given the queue’s lease design —the system pause is not a time-freeze of execution.

---

## 10. Dashboard UX

### 10.1 Global banner / badge

* Show a top-level badge:

  * `Workers: Running` (green)
  * `Workers: Paused (Drain)` / `Workers: Paused (Quiesce)` (yellow/red)

### 10.2 Controls

* Button: **Pause Workers**

  * Mode selector: `Drain (recommended)` / `Quiesce`
  * Reason text box (required)
* Button: **Resume Workers**

### 10.3 Drain progress panel

* Show:

  * Running jobs count
  * Queued jobs count
  * “Safe to upgrade” indicator when running count is 0

---

## 11. Operational Playbook (Recommended)

1. **Pause Workers (Drain)**

   * Verify dashboard shows paused + drain mode.
2. **Wait for drain**

   * Running count reaches 0.
3. Perform upgrades:

   * rebuild/pull images
   * run migrations
   * restart services
4. **Resume Workers**

   * Workers begin claiming again.

---

## 12. Security & Permissions

* Only authenticated operators/admins can call `POST /api/system/worker-pause`.

* Workers learn pause state only via:

  * claim response
  * heartbeat response
    (no worker-token permission to toggle system state)

* Audit all pause/resume actions in `system_control_events`.

---

## 13. Testing Plan

### 13.1 Unit tests (API/service)

* **Pause guard test**: when paused, claim handler returns `job=null` and does **not** call repository claim logic (ensures no `_requeue_expired_jobs` side effects).
* Resume restores normal claim behavior.
* `GET /api/system/worker-pause` returns correct computed counts.

### 13.2 Worker tests

* When claim returns `system.workersPaused=true`, worker:

  * does not crash
  * does not mark job failed
  * sleeps and retries
* Quiesce mode:

  * heartbeat response triggers pause-at-checkpoint behavior.

### 13.3 UI tests (thin)

* Banner renders paused state.
* Buttons call correct endpoints and update state.

---

## 14. Rollout Plan

### Phase 1 (MVP)

* DB tables + system pause endpoints
* Claim endpoint guarded return with `system` envelope
* Dashboard banner + Pause/Resume buttons (Drain only)

### Phase 2

* Add Quiesce mode (heartbeat propagation + worker checkpoint pause)

### Phase 3 (Future)

* “Freeze across worker restarts” (requires durable checkpoint/resume semantics; likely ties into step checkpoints and resume controls)

---

## 15. Related

* `docs/TaskQueueSystem.md` (queue lifecycle + stage plan )
* `docs/LiveTaskHandoff.md` (existing pause-at-checkpoint operator model )
* `moonmind/workflows/agent_queue/repositories.py` (claim normalization + lease-expiry requeue behavior  )

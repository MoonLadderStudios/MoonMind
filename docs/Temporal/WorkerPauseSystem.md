# System Pause & Maintenance Mode

Status: **Implemented**
Owners: **MoonMind Engineering**
Last Updated: **2026-03-14**

---

## 1. Problem Statement

MoonMind needs a **single, operator-driven “pause all workflows” switch** so upgrades (image rebuilds, schema migrations, credential rotations, etc.) can be performed **without tasks entering weird intermediate states**, and with the **queue remaining intact** (workflows should stay queued or paused; no surprise retries or dead-lettering).

With the migration to Temporal, the legacy concept of REST API claim blocking (`POST /api/queue/jobs/claim`) is obsolete. We now rely on Temporal's robust execution guarantees and native signals to achieve maintenance states.

---

## 2. Goals

1. **Stop new work from starting** across all Managed Agents and Orchestrator processes.
2. Keep the queues **undisturbed**: queued Temporal workflows remain queued.
3. Provide a **clear, auditable operator control** (API + Mission Control Dashboard) with reason + timestamps.
4. Support an upgrade-friendly workflow:
   * **Pause → Drain → Upgrade → Resume**
5. Optional “quiesce” mode: allow operators to request that **running jobs pause at safe Activity boundaries** while retaining their state in Temporal history.

---

## 3. Pause Modes

### 3.1 Drain (default / recommended for upgrades)

* **Mechanism**: Scale down or gracefully shut down `temporal-worker-sandbox` and `mm-orchestrator` instances.
* A graceful shutdown in Temporal (`worker.shutdown()`) blocks new Activity claims immediately but lets currently executing Activities finish or hit their heartbeat timeout.
* Operator waits until the Temporal UI shows no active workers mapping to the Task Queues → safe to restart/upgrade underlying images.

### 3.2 Quiesce (System-Wide Suspend)

* **Mechanism**: A global flag in the `system_worker_pause_state` database table OR a broadcast Temporal Signal to all running Workflows.
* Workflows check this state between Activities. If paused, the Workflow blocks on a `workflow.wait_condition()` until resumed.
* Running Activities may be signaled to stop processing and return early with a checkpoint, allowing the Workflow to safely suspend.

---

## 4. Architecture

### 4.1 Components

1. **System Pause State (DB, singleton)**
   * Source of truth for whether the Mission Control UI accepts new workflow submissions.

2. **Mission Control API Guard**
   * `POST /api/workflows` returns “system paused” metadata and **does not** trigger new Temporal Workflows while paused.

3. **Temporal Worker Graceful Shutdown (Drain)**
   * Used by operators at the infrastructure level (e.g., `docker compose stop <worker>`) to gracefully drain inflight Activities without killing them instantly.

4. **Dashboard UX**
   * Global banner + a “Pause System / Resume System” control.
   * Drain progress indicator (running workflow count pulled from Temporal Visibility APIs).

---

## 5. API Surface

### 5.1 System pause endpoints (operator-only)

* `GET /api/system/worker-pause`
  * Returns current pause state (including `reason`) + computed drain metrics from Temporal:
    * queued count
    * running count
    * `isDrained = (activeRunning == 0)`

* `POST /api/system/worker-pause`
  * Body:
    ```json
    {
      "action": "pause" | "resume",
      "mode": "drain" | "quiesce",
      "reason": "Upgrading images"
    }
    ```

---

## 6. Worker Behavior

### 6.1 Running Activity behavior (Quiesce mode)

In Quiesce mode, the worker should:
* Continue heartbeating.
* Interceptors inject the pause state into the Activity context.
* If the Activity supports checkpoints (e.g., LLM loops), it pauses at a safe checkpoint boundary and yields back to the Workflow.
* The Workflow then enters a suspended state, waiting for the system to resume.

**Important**: Quiesce is meant for short maintenance where workflows need to suspend rapidly without losing long-running context. For infrastructure upgrades, Drain is the safe path.

---

## 7. Operational Playbook (Recommended)

1. **Pause System (Drain)**
   * Post to API or use Dashboard to pause new workflow ingestions.
   * Send graceful shutdown signals (SIGINT/SIGTERM) to Temporal worker containers.
2. **Wait for drain**
   * Temporal UI shows 0 active workers on the queues, and all inflight Activities have completed.
3. **Perform upgrades**:
   * rebuild/pull images
   * run migrations
   * restart services
4. **Resume System**
   * Workers reconnect to Temporal and begin pulling Tasks.
   * Dashboard allows new workflow submissions.

---

## 8. Security & Permissions

* Only authenticated operators/admins can call `GET/POST /api/system/worker-pause`.
* Audit all pause/resume actions in `system_control_events`.

---

## 9. Rollout Plan

### Phase 1 (Temporal Alignment)

* Connect the Mission Control Banner to Temporal visibility queries instead of the old queue tables.
* Update `POST /api/system/worker-pause` to block `/api/workflows` and broadcast signals to Workflows for Quiesce mode.

### Phase 2 (Advanced Suspend)

* Add deep Quiesce mode where LLM loops natively yield their state to Temporal history on receiving a pause signal.

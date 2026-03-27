# System Pause & Maintenance Mode

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkerPauseSystem.md`](../tmp/remaining-work/Temporal-WorkerPauseSystem.md)

Status: **Implemented**
Owners: **MoonMind Engineering**
Last Updated: **2026-03-14**

---

## 1. Problem Statement

MoonMind needs a **single, operator-driven “pause all workflows” switch** so upgrades (image rebuilds, schema migrations, credential rotations, etc.) can be performed **without tasks entering weird intermediate states**, and with the **queue remaining intact** (workflows should stay queued or paused; no surprise retries or dead-lettering).

With the migration to Temporal, REST-level claim blocking is obsolete. We now
rely on Temporal's execution guarantees and native signals to achieve
maintenance states.

---

## 2. Goals

1. **Stop new work from starting** across all Managed Agents and `agent_runtime` worker fleets.
2. Keep the queues **undisturbed**: queued Temporal workflows remain queued.
3. Provide a **clear, auditable operator control** (API + Mission Control Dashboard) with reason + timestamps.
4. Support an upgrade-friendly workflow:
   * **Pause → Drain → Upgrade → Resume**
5. Optional “workflow pause” mode: allow operators to request that **running jobs pause at safe Activity boundaries** while retaining state durably (Temporal history for workflow state + `ManagedRunStore` for detached managed runtimes).

---

## 3. Pause Modes

### 3.1 Fleet Drain (default / recommended for upgrades)

* **Mechanism**: Scale down or gracefully shut down `temporal-worker-sandbox` and `agent_runtime` worker fleets.
* This operates at the **fleet** level. The workflows themselves are not paused; they simply wait in the task queue for a worker to become available.
* A graceful shutdown in Temporal (`worker.shutdown()`) blocks new Activity claims immediately but lets currently executing Activities finish or hit their heartbeat timeout.
* Operator waits until the Temporal UI shows no active workers mapping to the Task Queues → safe to restart/upgrade underlying images.

### 3.2 Workflow Pause (System-Wide Suspend)

* **Mechanism**: A future workflow-level quiesce contract sent to running workflows, telling the orchestration logic to suspend at safe boundaries.
* This operates at the **workflow** level. Workers may still be online, but the workflows intentionally do not schedule new Activities.
* The workflow-level contract must align with the canonical execution control model: acknowledged execution pause/resume remains update-based, while any future system-wide quiesce broadcast must use an explicitly documented workflow-local async contract instead of ad hoc generic signal names.
* Workflows check this state between Activities and block on `workflow.wait_condition(...)` until resumed.
* Long-running agent runtimes are detached and their state is preserved via the `ManagedRunStore`, rather than requiring Temporal Activity heartbeats.

---

## 4. Architecture

### 4.1 Components

1. **System Pause State (DB, singleton)**
   * Source of truth for whether the Mission Control UI accepts new workflow submissions via the `POST /api/workflows` boundary.
   * **Note**: This does not govern the Temporal workers themselves; it only prevents new tasks from being injected into the system from the frontend.

2. **Mission Control API Guard**
   * `POST /api/workflows` returns “system paused” metadata and **does not** trigger new Temporal Workflows while the DB singleton is paused.

3. **Temporal Worker Graceful Shutdown (Drain)**
   * Used by operators at the infrastructure level (e.g., `docker compose stop <worker>`) to gracefully drain inflight Activities without killing them instantly.
   * This is the native Temporal way to halt new work assignments to a specific worker node.

4. **Dashboard UX**
   * Global banner + a “Pause System / Resume System” control.
   * Drain progress indicator (running workflow count pulled from Temporal Visibility APIs: `ExecutionStatus="Running"`).

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

In Workflow Pause mode, any future broad quiesce mechanism must use a dedicated, documented workflow-local async contract. The workflow implementation should:

* register the dedicated quiesce contract explicitly rather than assuming public `Pause` / `Resume` update names can be broadcast generically,
* Maintain an internal `self.is_paused` flag.
* Before transitioning to the next Activity/Agent Step, the workflow calls `await workflow.wait_condition(lambda: not self.is_paused)`.
* For long-running managed agent runtimes, `MoonMind.AgentRun` and adapters use `status(...)` polling against the `ManagedRunStore`. Suspending the workflow simply pauses the polling loop, leaving the detached runtime unaffected.

**Worker Identity & Identity Reconnection**:
Resumed workflows seamlessly handoff or continue on newly upgraded workers holding the same Task Queue, thanks to Temporal's execution guarantees.

**Important**: Workflow Pause is meant for short maintenance where workflows need to suspend rapidly without losing long-running context. For infrastructure upgrades, Drain is the safe path.

### 6.2 External Agents

For external agents (e.g., Jules), the pause mechanism behaves differently. External agent processes are not interrupted or canceled by a system pause. In steady state today, pause primarily blocks new workflow submissions and relies on worker drain to stop further orchestration. Any future workflow-level quiesce must document how external-agent waits and detached runtimes respond so operator expectations remain accurate.

---

## 7. Operational Playbook (Recommended)

1. **Pause System (Drain)**
   * Post to API or use Dashboard to pause new workflow ingestions.
   * Send graceful shutdown signals (SIGINT/SIGTERM) to Temporal worker containers. Note that Docker-Out-Of-Docker (DOOD) ephemeral containers might complete or get killed depending on Activity wall-clock timeouts.
2. **Wait for drain**
   * Temporal UI shows 0 active workers on the queues, and all inflight Activities have completed.
3. **Perform upgrades**:
   * rebuild/pull images
   * run migrations
   * restart services
4. **Resume System**
   * Workers reconnect to Temporal and begin pulling Tasks. The `agent_runtime` workers securely resume tracking detached runtimes via the `ManagedRunStore` (backed by the persistent `agent_workspaces`/`/work/agent_jobs` volume). Note that cross-node upgrades require the replacement workers to mount the same persistent/shared store path.
   * Dashboard allows new workflow submissions.

---

## 8. Security & Permissions

* Only authenticated operators/admins can call `GET/POST /api/system/worker-pause`.
* Audit all pause/resume actions in `system_control_events`.

---

## 9. Follow-on enhancements

**Steady-state today:** API-driven pause, worker drain, and Temporal-aligned operator messaging (see sections above). **Optional next steps** — banner wired purely to Temporal visibility (where not already), runbooks standardized on `worker.shutdown()` for drain, and a deeper workflow-level quiesce contract using `workflow.wait_condition()` plus an explicitly documented async broadcast path — are tracked in [`docs/tmp/remaining-work/Temporal-WorkerPauseSystem.md`](../tmp/remaining-work/Temporal-WorkerPauseSystem.md).

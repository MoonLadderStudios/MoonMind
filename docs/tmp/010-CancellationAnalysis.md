# Managed Agent Cancellation — Idiomatic Temporal Analysis

This document explains how **Temporal models cancellation** (official semantics), how that maps to **slow cancel** for `gemini_cli` and other managed runs in MoonMind, and which changes stay **inside idiomatic Temporal** (no parallel “shadow” cancel paths, no workflow-side process kills on remote workers).

**Code touchpoints:** [`agent_run.py`](../../moonmind/workflows/temporal/workflows/agent_run.py), [`run.py`](../../moonmind/workflows/temporal/workflows/run.py) (child workflow), [`supervisor.py`](../../moonmind/workflows/temporal/runtime/supervisor.py).

---

## Executive summary

Cancellation from Mission Control is **`WorkflowHandle.cancel()`** — a **graceful** stop: Temporal records `WorkflowExecutionCancelRequested`, schedules a workflow task, and workflow code may run cleanup. It is **not** immediate like **Terminate** (which skips workflow code entirely).

Slowness today comes mainly from:

1. **Default activity cancellation behavior** — When the workflow is awaiting `execute_activity(...)`, cancellation of the *workflow* must interact with cancellation of the *current activity*. The default **`ActivityCancellationType.WAIT_CANCELLATION_COMPLETED`** makes the workflow wait for the activity cancellation protocol to finish; long activities without responsive **heartbeats** cannot observe cancellation quickly.
2. **Long `start_to_close` timeouts** — Until the activity completes, fails, or responds to cancellation, the workflow may remain blocked.
3. **Extra hops after `CancelledError`** — Cleanup (e.g. dedicated `agent_runtime.cancel` activity, auth slot signals) adds queue and RPC latency.
4. **Product-level timing** — Supervisor graceful shutdown (`SIGTERM` wait before `SIGKILL`) is independent of Temporal but affects perceived latency.

**Highest-impact, idiomatic lever:** set **`cancellation_type=ActivityCancellationType.TRY_CANCEL`** on `execute_activity` calls where MoonMind should **enter workflow cancel handling without waiting** for the activity’s cancellation to fully complete (per SDK semantics). Combine with **heartbeats + heartbeat timeouts** inside long-running activities so the worker can deliver cancellation to application code promptly.

---

## Idiomatic Temporal cancellation (reference)

### Cancel vs terminate

| Mechanism | Workflow code runs cleanup? | Typical use |
|-----------|------------------------------|-------------|
| **Cancel** | Yes — workflow receives cancellation and can catch `CancelledError` / `asyncio.CancelledError` | Normal operator “stop” |
| **Terminate** | No — no workflow task; history shows `WorkflowExecutionTerminated` | Stuck workflow, broken code path |

Prefer **Cancel** unless the run is truly stuck and cannot make progress through cancellation ([Interrupt a Workflow Execution — Python](https://docs.temporal.io/develop/python/cancellation)).

### Child workflows

A **parent** cancel request is propagated to **child** workflows per your **parent close policy** and Temporal’s rules. That implies an extra server/workflow-task round trip compared to a single workflow. This is expected; do not “fix” it with out-of-band IPC—tune **activity** behavior and **cancellation types** instead.

### Activities: heartbeats are required for responsive cancellation

For **regular** activities, Temporal’s documentation is explicit: to cancel an activity from the workflow side, the activity should **send heartbeats** and set a **heartbeat timeout**. Without heartbeats, the activity may not process cancellation in a timely way ([same cancellation doc](https://docs.temporal.io/develop/python/cancellation)).

**Local activities** are a special case (same worker; cancellation without heartbeats is possible). They are **not** a substitute for killing a process started on another worker’s activity fleet—see *Non-goals* below.

### `ActivityCancellationType` (workflow await on `execute_activity`)

When a **workflow cancellation** arrives while the workflow is **blocked inside** `await workflow.execute_activity(...)`, the SDK uses an **activity cancellation type** to decide how the await resolves ([`ActivityCancellationType`](https://python.temporal.io/temporalio.workflow.ActivityCancellationType.html)):

| Type | Meaning (practical) |
|------|---------------------|
| **`WAIT_CANCELLATION_COMPLETED`** (default) | Workflow cancellation waits for the activity cancellation sequence to complete. Activities that rarely heartbeat may appear to “block” cancellation for a long time. |
| **`TRY_CANCEL`** | Request activity cancellation; the workflow’s await can complete with cancellation **without** waiting for the activity to finish all cleanup (exact semantics follow the SDK). Use when the workflow must run its own `CancelledError` handler (slot release, follow-up activities) even if the first activity is still winding down. |
| **`ABANDON`** | Workflow treats itself as cancelled **without** waiting for activity cancellation to complete—can strand work; use only with strong operational justification. |

MoonMind’s managed-agent path uses explicit **`TRY_CANCEL`** on long-running `agent_runtime.*` and integration poll call sites in `agent_run.py` and `run.py` (do not revert to default `WAIT_CANCELLATION_COMPLETED` on those paths without replay/workflow review).

### Explicit activity handle cancellation (advanced)

Inside workflow code, **`workflow.start_activity(...)`** returns a handle whose **`cancel()`** requests activity cancellation. That pattern is for **workflow-initiated** cancellation of a specific activity, distinct from **client-initiated workflow cancel**. Both are still fully within Temporal; they are not “bypassing” the platform.

---

## MoonMind: cancellation pipeline (observed)

The following is a **conceptual** sequence for a managed run using `MoonMind.Run` → child `MoonMind.AgentRun`. Exact ordering should be verified against current code.

```mermaid
sequenceDiagram
    participant UI as Mission Control
    participant API as API Service
    participant TDB as App DB projection
    participant TS as Temporal Server
    participant RW as MoonMind.Run
    participant AW as MoonMind.AgentRun child
    participant ACT as Activities on mm.activity.agent_runtime
    participant SUP as ManagedRunSupervisor
    participant PROC as CLI process

    UI->>API: POST /api/executions/{id}/cancel
    API->>TS: WorkflowHandle.cancel()
    API->>TDB: optimistic CANCELED optional
    API-->>UI: HTTP 202 Accepted
    Note over TS,AW: Cancel propagates parent → child per policy
    Note over AW: If inside execute_activity: activity cancel + type matters
    AW->>AW: CancelledError in workflow
    AW->>AW: e.g. signal AuthProfileManager release slot
    AW->>ACT: execute_activity(agent_runtime.cancel)
    ACT->>SUP: supervisor.cancel(run_id)
    SUP->>PROC: SIGTERM / SIGKILL
```

---

## Root causes of slowness (mapped to Temporal levers)

### 1. Workflow blocked inside a long activity (primary)

If `MoonMind.AgentRun` is awaiting **`agent_runtime.launch`**, **`integration.*.status`**, **`agent_runtime.publish_artifacts`**, etc., **workflow** cancellation cannot complete until the **activity cancellation contract** is satisfied—strongly influenced by **heartbeat** behavior, **timeouts**, and **`ActivityCancellationType`**.

**Idiomatic mitigations:** `TRY_CANCEL` where appropriate, **heartbeats** in long activities, **tighter timeouts** where safe, and **retry policies** that do not extend wall time unnecessarily.

### 2. Default `WAIT_CANCELLATION_COMPLETED` on `execute_activity`

With the default, a **workflow** that has been asked to cancel may still **wait** for the activity side to complete cancellation processing. For minute-scale activities, that dominates latency unless activities heartbeat frequently.

**Idiomatic mitigation:** `cancellation_type=ActivityCancellationType.TRY_CANCEL` on selected `execute_activity` calls (see **Recommendations** below).

### 3. Follow-up cancel activity + queue contention

After the workflow observes cancellation, MoonMind may schedule **`agent_runtime.cancel`**. That is still **idiomatic** (activity-based cleanup). Slowness here is **worker capacity** / **task queue backlog** (`mm.activity.agent_runtime`)—fix with **scaling**, **concurrency**, and **SLOs**, not by killing processes outside Temporal.

### 4. Child workflow propagation

Parent/child cancel ordering is inherent. Prefer **correct activity cancellation** and **timeouts** over extra control-plane shortcuts.

### 5. Ordering of cleanup steps in workflow code

If the workflow **signals** `AuthProfileManager` **before** running **`agent_runtime.cancel`**, slow paths on the manager add delay. **Within deterministic workflow rules**, consider **structuring awaits** so cleanup steps run in parallel only where safe, or reorder so process teardown is not unnecessarily gated on non-critical work—still **inside** the workflow.

### 6. Supervisor graceful shutdown window

`SIGTERM` then wait then `SIGKILL` is a **product** decision (CLI safety). It is not Temporal-specific; tune cautiously for user data integrity.

---

## Recommendations (idiomatic Temporal only)

Each phase lists tasks with checkboxes (`[x]` done, `[ ]` open). Last updated 2026-03.

### Phase P0 — Hot path and activity cooperation

- [x] **Use `TRY_CANCEL` on long `execute_activity` call sites** — Apply **`ActivityCancellationType.TRY_CANCEL`** in the **hot path** where workflow cancellation should **not** block on full activity cancellation completion (especially `agent_runtime.*` and integration polling). **Status:** `MoonMind.AgentRun` and `MoonMind.Run`. Validate changes with **workflow** and **replay** tests.

  Example pattern:

  ```python
  from temporalio.workflow import ActivityCancellationType

  await workflow.execute_activity(
      "agent_runtime.launch",
      ...,
      cancellation_type=ActivityCancellationType.TRY_CANCEL,
  )
  ```

- [x] **Heartbeats + heartbeat timeouts in long-running activities** — Activities that can run for minutes should call **`activity.heartbeat(...)`** below the **heartbeat timeout** and set a **heartbeat timeout** on `execute_activity` options ([cancellation doc](https://docs.temporal.io/develop/python/cancellation)).

- [ ] **Re-verify heartbeat coverage** when adding **new** long-running activities or queues.

### Phase P1 — Timeouts, retries, and capacity

- [ ] **Right-size timeouts and retries** — Tune **`start_to_close`** and **`schedule_to_close`** with heartbeat-aware bounds so “stuck” is detected without always waiting for the full timeout.

- [ ] **Operational: agent-runtime worker scaling** — If **`agent_runtime.cancel`** queues behind other work, increase worker throughput on **`mm.activity.agent_runtime`** or reduce exclusive long-running work occupying slots.

### Phase P2 — UX and product timing

- [x] **`workflow.query` for “cancellation in progress”** — **`MoonMind.Run.get_status`** exposes **`cancel_requested`** and **`canceling`** (latter true while cancel was requested and state is not yet fully canceled).

- [ ] **Supervisor `SIGTERM` / `SIGKILL` timing** — After Temporal-side cancellation is sound, adjust **graceful wait** constants in **`supervisor.py`** if product safety allows (orthogonal to Temporal; reduces tail latency).

### Phase P3 — Last-resort operator actions

- [x] **Terminate only as a last resort** — For runs that **cannot** honor cancellation (bug, poison pill), operators may use **Terminate** via tooling; document **when** it is appropriate; do not treat it as the normal Mission Control path.

---

## Non-goals (explicitly out of scope for "idiomatic Temporal")

The following were previously suggested in earlier drafts but **conflict** with keeping Temporal as the **single orchestration and lifecycle authority**:

| Anti-pattern | Why avoid |
|--------------|-----------|
| **"Bypass Temporal cancel"** via **direct IPC** to kill a process | Splits truth: process may exit while workflow still shows running; breaks audit and child/parent semantics. |
| **`execute_local_activity` from workflow to kill a remote agent process** | Local activities run on the **workflow worker**; managed agents run on **other** workers. This does not replace **`agent_runtime.cancel`** on the correct fleet. |
| **Duplicate cancel channels** (API kills process, workflow learns later) | Hard to reason about; duplicates failures and billing edge cases. |

**Preferred:** one **client** cancel on the **workflow** (`handle.cancel()`), **workflow** code handles `CancelledError`, **activities** cooperate via **heartbeats** and **`TRY_CANCEL`** where appropriate.

---

## Prioritization

Remaining work is anything still **`[ ]`** under **Recommendations** (P0 ongoing heartbeat review, P1, partial P2) and **Implementation Tasks** (Task 3).

---

## Auth Profile Slot Leak on Cancellation

### Observed Failure (2026-03-24)

Workflow `mm:5b122aaa` was cancelled while its child `MoonMind.AgentRun` held a `claude_minimax` auth profile slot (`max_parallel_runs: 1`). The slot was **not released**, blocking all subsequent Claude Code tasks until the lease was manually released via a `release_slot` signal to the `AuthProfileManager`.

### Root Cause Analysis

The `CancelledError` handler in `MoonMind.AgentRun` (line ~1005 in `agent_run.py`) wraps the slot-release signal in `asyncio.shield()`:

```python
except CancelledError:
    async def _release_slot():
        try:
            manager_handle = workflow.get_external_workflow_handle(manager_id)
            await manager_handle.signal("release_slot", {...})
        except Exception:
            self._get_logger().warning("Failed to release slot on cancellation...")
    tasks.append(asyncio.shield(_release_slot()))
    ...
    await asyncio.gather(*tasks, return_exceptions=True)
    raise
```

**Why the signal was not sent:** Temporal's Python SDK processes `CancelledError` within a workflow task. The `asyncio.shield()` is a Python-level primitive — it does not override Temporal's workflow-level cancellation semantics. Once the Temporal SDK has decided to cancel the workflow execution, `SignalExternalWorkflowExecution` commands may not be delivered if the workflow task completes as cancelled. The workflow history for `mm:5b122aaa:agent:node-1` confirms: no `SignalExternalWorkflowExecutionInitiated` event appears after the `WorkflowExecutionCancelRequested` event.

**Contributing factor:** The workflow was stuck on `integration.get_activity_route` (unregistered activity, now fixed) when the cancel arrived. Even with `TRY_CANCEL`, the cleanup handler's signal may still be swallowed depending on SDK workflow-task finalization ordering.

### Why the Safety Net Did Not Help Quickly (historical)

At the time of the incident, `evict_expired_leases()` ran every 60 seconds with `_MAX_LEASE_DURATION_SECONDS = 7200` (2 hours). **Current code** uses **`_MAX_LEASE_DURATION_SECONDS = 5400`** (1.5 hours) as the workflow-side default and **`auth_profile.verify_lease_holders`** to reclaim leases when the holder workflow is already terminal—so orphaned slots from cancelled workflows should clear much faster than waiting for max lease duration alone.

### Failure Modes Where Slot Cleanup Can Fail

| Failure Mode | Current Handling | Gap |
|---|---|---|
| `CancelledError` handler runs but `asyncio.shield` signal not delivered | Handler exists | Signal may not execute (Temporal SDK finalization) |
| Workflow terminates (not cancelled) | No cleanup code runs at all | No handler for `WorkflowExecutionTerminated` |
| Workflow stuck on unregistered/failing activity | `TRY_CANCEL` set, but cleanup depends on handler executing | Same shield issue |
| Worker crashes | No cleanup possible | Only lease eviction catches this |
| Workflow completes normally after 429 retry | `release_slot` signal sent | Working correctly |

---

## Implementation Tasks: Auth Profile Slot Leak Prevention

> **Design principle:** Do not rely on the child workflow's cleanup code as the sole mechanism for slot recovery. The `AuthProfileManager` (the slot owner) must independently detect and reclaim orphaned leases using idiomatic Temporal patterns.

### Phase — Auth profile slot leak mitigation

- [x] **Task 1: Active lease holder verification in `AuthProfileManager`** — Periodic check that lease-holding workflows are still running. **Implemented:** `_verify_lease_holders()` in [`auth_profile_manager.py`](../../moonmind/workflows/temporal/workflows/auth_profile_manager.py), invoked from the manager’s eviction loop, calling activity **`auth_profile.verify_lease_holders`** (in [`artifacts.py`](../../moonmind/workflows/temporal/artifacts.py) as `auth_profile_verify_lease_holders`; registered via [`activity_catalog.py`](../../moonmind/workflows/temporal/activity_catalog.py) / [`activity_runtime.py`](../../moonmind/workflows/temporal/activity_runtime.py)). Batch describe replaces the single-ID sketch below.

  **Original sketch (superseded by batch activity):**

  ```python
  # Sketch only — shipped as auth_profile.verify_lease_holders (batch)
  @activity.defn(name="auth_profile.verify_lease_holder")
  async def verify_lease_holder(workflow_id: str) -> dict:
      """Check if a workflow is still running. Returns {"running": bool, "status": str}."""
      ...
  ```

  **Reclaim policy:** If a lease holder is in a terminal state, release the lease and drain the queue; log for observability.

- [x] **Task 2: Reduce `_MAX_LEASE_DURATION_SECONDS` or make it profile-configurable** — Workflow default **`_MAX_LEASE_DURATION_SECONDS = 5400`** (1.5 hours) in `auth_profile_manager.py`; **`max_lease_duration_seconds`** on profile state and DB/API (`managed_agent_auth_profiles`, API router). Stored-row default from migration remains **7200** unless operators set a shorter value per profile (optional: align one global default).

- [ ] **Task 3: Verify `CancelledError` slot release reliability (workflow boundary)** — **What:** workflow-level test: managed `AgentRun` holds a slot → cancel → manager state has no lease. **Current state:** [`tests/integration/services/temporal/workflows/test_agent_run.py`](../../tests/integration/services/temporal/workflows/test_agent_run.py) **`test_cancellation_releases_auth_profile_slot`** is **`@pytest.mark.xfail`** when **`MoonMind.AgentRun`** runs without a **`MoonMind.Run`** parent. [`tests/unit/workflows/temporal/test_auth_profile_manager.py`](../../tests/unit/workflows/temporal/test_auth_profile_manager.py) has **`test_verify_lease_holders_exists`**. **Follow-up:** remove `xfail` using a full parent workflow or assert reclaim via `_verify_lease_holders` under time-skipping.

- [x] **Task 4: Parent-initiated `release_slot` fallback** — On **`MoonMind.Run`**, **`child_state_changed`** with terminal child states triggers **`_release_slot_defensive()`** → **`release_slot`** on the right **`AuthProfileManager`** ([`run.py`](../../moonmind/workflows/temporal/workflows/run.py)); gated by **`RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH`** for replay safety.

---

## References

- [Interrupt a Workflow Execution — Python SDK](https://docs.temporal.io/develop/python/cancellation)
- [Failure detection — Python SDK](https://docs.temporal.io/develop/python/failure-detection) (timeouts, heartbeats)
- [`temporalio.workflow.ActivityCancellationType`](https://python.temporal.io/temporalio.workflow.ActivityCancellationType.html)
- [TaskCancellation.md § 6 — Auth Profile Slot Cleanup](../Tasks/TaskCancellation.md) (canonical design)

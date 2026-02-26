# Orchestrator as a First-Class Task Runtime

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-02-25

## 1. Purpose

Align the MoonMind Orchestrator with the existing task system so that:

* Orchestrator work is surfaced as **Tasks** (not “runs”).
* Orchestrator tasks are created with a **similar steps + skills authoring experience** to queue tasks.
* Orchestrator tasks appear in the **same task list and task detail experience** as other tasks (with runtime category `orchestrator`), eliminating the dedicated orchestrator pages currently described in the dashboard route map L74-L88.
* The orchestrator can **continue executing** even if Postgres (and other “support services”) become unavailable mid-execution, which is intended to be a key differentiator from normal queue workers.

## 2. Current State (Relevant Snapshot)

### 2.1 Dashboard surface is split by “source”

MoonMind’s docs explicitly call out **separate orchestrator runs** alongside queue jobs L17-L24, with orchestrator runs “tracked and managed through `/orchestrator/*` APIs and dashboard pages” L86-L90. The UI route map currently includes `/tasks/orchestrator` and `/tasks/orchestrator/:runId` L84-L88.

### 2.2 Orchestrator submit does not use steps/skills like queue tasks

The UI contract currently treats orchestrator submit as a small separate shape: `instruction`, `targetService`, `priority`, optional `approvalToken` posting to `POST /orchestrator/runs` L28-L41.

### 2.3 Orchestrator is already a queue-consumed workload, but “run” is the canonical record

The orchestrator API creates a run record (`/orchestrator/runs`) and then enqueues an **agent queue job** of type `orchestrator_run` with payload `{ runId, steps, includeRollback, requiredCapabilities: ["orchestrator"] }` L39-L57, where `orchestrator_run` is a first-class supported queue job type L7-L17.

### 2.4 Orchestrator plan steps are currently constrained to a fixed enum and unique state rows

The orchestrator plan-step enum is fixed (`analyze/patch/build/restart/verify/rollback`) L73-L82, and step-state persistence enforces uniqueness per plan step + attempt (e.g. `uq_orchestrator_task_state_attempt` on `orchestrator_run_id, plan_step, attempt`) L65-L69. This makes “arbitrary N steps” impossible without a model change.

### 2.5 Orchestrator already has a “skill” concept (script-backed)

`OrchestratorCreateRunRequest` includes `skillId` and `skillArgs` L390-L402, and the API validates runnable skill scripts via `list_runnable_skill_names()` / `is_runnable_skill()` L17-L35. The runnable skill discovery comes from the orchestrator skill executor, which can list runnable skills from mirrors L43-L61.

## 3. Goals

1. **Unified naming**: “Orchestrator Runs” become **Orchestrator Tasks** everywhere user-facing, and API contracts begin migrating from `runId` → `taskId`.
2. **Unified authoring**: Orchestrator tasks can be authored with a **step list** and **skill selection** that feels like queue task authoring (steps editor + skill ids + args).
3. **Unified monitoring**: Remove dedicated orchestrator list/detail pages and render orchestrator tasks in the same list/detail UX as queue tasks.
4. **Degraded execution**: If Postgres becomes unreachable during an orchestrator task, the orchestrator should continue execution and still produce artifacts (and later reconcile state if/when DB returns).

## 4. Non-Goals (for this iteration)

* Replacing the Agent Queue DB backing entirely (queue workers still depend on it for claiming jobs).
* Reworking the entire dashboard to a new frontend framework (remain within the existing thin-dashboard architecture).
* Building a generalized “workflow engine” that unifies every workload type into a single DB table immediately (the current UI explicitly called that out as out-of-scope L41-L43; this design introduces a constrained unification focused on Orchestrator).

## 5. Proposed Design

### 5.1 Terminology and External Contracts

**User-facing**:

* Rename “Run” → “Task”
* Rename “Orchestrator run” → “Orchestrator task”
* Runtime category for these items is `orchestrator` (the dashboard already treats “orchestrator” as a runtime option label L53-L63).

**API compatibility**:

* Keep existing endpoints operational for now:

  * `POST /orchestrator/runs`, `GET /orchestrator/runs`, `GET /orchestrator/runs/{id}`, approvals, retry L40-L48
* Add aliases with new names (thin wrappers):

  * `POST /orchestrator/tasks` → same handler as `POST /orchestrator/runs`
  * `GET /orchestrator/tasks` → same as list runs
  * `GET /orchestrator/tasks/{task_id}` → same as get run
  * `POST /orchestrator/tasks/{task_id}/approvals` → same
  * `POST /orchestrator/tasks/{task_id}/retry` → same
* Response payloads:

  * For a transition period, return both `taskId` and `runId` fields (same UUID) in summaries/details.
  * Internally the DB tables will be aggressively migrated from `orchestrator_runs` to `orchestrator_tasks` to ensure terminology alignment across the stack.

### 5.2 Unified Dashboard UX (Remove Orchestrator Pages)

#### 5.2.1 Route consolidation

Current dashboard routes include `/tasks/orchestrator` and `/tasks/orchestrator/:runId` L84-L88 and the server allowlist explicitly includes `orchestrator` paths L26-L35.

**Change**:

* Remove `/tasks/orchestrator` list route.
* Remove `/tasks/orchestrator/:runId` detail route.
* Remove `/tasks/queue` list route.
* Replace with:

  * `/tasks/list` = unified list (all tasks, not only “active” or queue-specific)
  * `/tasks/:taskId` = unified detail

**Compatibility redirects**:

* `/tasks/orchestrator` → `/tasks/list?filterRuntime=orchestrator` (client-side redirect)
* `/tasks/orchestrator/:runId` → `/tasks/:runId?source=orchestrator` (client-side redirect)
* `/tasks/queue` → `/tasks/list?source=queue` (client-side redirect)
* `/tasks/queue/:jobId` → `/tasks/:jobId?source=queue` (optional; can keep existing for a while)

#### 5.2.2 Unified list data model (client-side first)

The dashboard already supports a “fan-out” pattern and consolidated pages L60-L68. We’ll reuse that approach:

* Fetch queue jobs:

  * `GET /api/queue/jobs?limit=200`
* Fetch orchestrator tasks:

  * `GET /orchestrator/tasks?limit=200` (alias to runs)

Then render a **single table** with rows normalized to a shared DTO:

```ts
type UnifiedTaskRow = {
  id: string;            // jobId or taskId
  source: "queue" | "orchestrator";
  runtime: "codex" | "gemini" | "claude" | "orchestrator" | string;
  status: "queued" | "running" | "awaiting_action" | "succeeded" | "failed" | "cancelled";
  title: string;         // instructions/instruction (trimmed)
  skillId?: string;      // queue: task.skill.id; orchestrator: skillId if present
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  outcome?: string;      // best-effort derived from status / finishOutcome*
}
```

**Status normalization** remains consistent with the dashboard contract L40-L44, but orchestration items will now be labeled “task” and “taskId” in UI.

#### 5.2.3 Unified detail page

`/tasks/:taskId` will:

1. Determine `source`:

   * If `?source=` is provided, use it.
   * Otherwise, attempt queue fetch first (`GET /api/queue/jobs/{id}`), fall back to orchestrator fetch (`GET /orchestrator/tasks/{id}`).
2. Render a shared shell:

   * Header: title, runtime pill, status pill, timestamps
   * Tabs:

     * Overview
     * Timeline (events/step states)
     * Artifacts
     * Controls (contextual)

**Source-specific tab behavior**:

* Queue tasks: use existing queue event stream and artifacts endpoints L17-L38.
* Orchestrator tasks: show action plan step states and orchestrator artifacts (existing) L6-L40.

### 5.3 Unified Authoring: Orchestrator Tasks Use “Steps + Skill” Like Queue Tasks

This is the core alignment change.

#### 5.3.1 Submit form behavior

The dashboard already has a runtime selector and a shared submit form scaffold, with an Orchestrator runtime option L53-L70.

**Change the orchestrator authoring mode**:

Instead of hiding the step editor when runtime is `orchestrator` (as the current UI contract does L28-L41), we make the orchestrator mode show:

* Step editor (same component/data structure as queue tasks)
* Skill selection per step
* Skill args JSON per step
* Orchestrator-specific fields:

  * `targetService` (required)
  * `priority` (normal/high)
  * `approvalToken` (optional)

We **do not hide** queue-specific fields. Orchestrator tasks should be able to do anything normal worker tasks can do if desired:

* publish mode, PR fields, max attempts (though orchestrator tasks are maxAttempts=1 today via dispatch L75-L81, they will be expanded)
* repo/new branch fields

#### 5.3.2 Orchestrator skill catalog surfaced to the dashboard

Today the dashboard skill endpoint is used for queue submit suggestions L11-L15. Orchestrator has its own runnable skill listing via `list_runnable_skill_names()` L43-L61.

**Change**:

* Extend `GET /api/tasks/skills` to return a grouped payload:

```json
{
  "items": {
    "worker": ["auto", "fix-proposal", "..."],
    "orchestrator": ["update-moonmind", "restart-stack", "..."]
  }
}
```

This keeps a single “skills discovery” concept but lets the UI switch lists based on runtime.

#### 5.3.3 Backend contract: new optional `steps` for orchestrator create

Today `OrchestratorCreateRunRequest` only supports a single `skillId/skillArgs` pair L390-L402.

**Extend it** (backwards-compatible):

* Add optional `steps: []` where each step is:

```json
{
  "id": "step-1",
  "title": "Update orchestrator",
  "instructions": "Pull latest and restart",
  "skill": { "id": "update-moonmind", "args": { "restartOrchestrator": true } }
}
```

**Interpretation rule**:

* If `steps` is absent/empty:

  * Preserve current behavior: generate the standard action plan from instruction + service profile (analyze/patch/build/restart/verify/rollback).
* If `steps` is provided:

  * Generate a *skill-driven action plan* where each step maps to one orchestrator skill execution (script-backed) **in order**.

> Why this requires data model work: current orchestrator step state is keyed by a fixed enum and enforces uniqueness per plan_step+attempt L65-L69, so arbitrary step sequences can’t be represented without change.

### 5.4 Orchestrator Step Tracking v2 (to support “N steps”)

To truly match queue-like “add steps” behavior, we need step state tracking that is not limited to the fixed `OrchestratorPlanStep` enum L73-L82.

#### 5.4.1 New tables (recommended)

Add new persistence specifically for orchestrator task steps:

* `orchestrator_tasks`

  * `id` (uuid pk)
  * `instruction` (text)
  * `target_service` (text)
  * `priority` (enum)
  * `status` (enum: pending/running/awaiting_approval/succeeded/failed/rolled_back)
  * `approval_*` fields (same meaning as current)
  * `created_at`, `started_at`, `completed_at`
  * `metrics_snapshot` (jsonb)
  * `artifact_root` (text)

* `orchestrator_task_steps`

  * `id` (uuid pk) or `(task_id, step_id)` composite
  * `task_id` (fk)
  * `step_id` (text, stable)
  * `index` (int)
  * `title` (text)
  * `instructions` (text)
  * `skill_id` (text)
  * `skill_args` (jsonb)
  * `status` (queued/running/succeeded/failed/skipped)
  * `attempt` (int)
  * `message` (text)
  * `started_at`, `finished_at`
  * `artifact_refs` (jsonb)

This mirrors the “steps list” mental model the user wants, without fighting the legacy orchestrator enum constraints.

#### 5.4.2 Legacy compatibility and Migration

* Given the decision to migrate aggressively, existing `orchestrator_runs` will be migrated directly into `orchestrator_tasks` via database migration scripts.
* `orchestrator_runs` tables will be dropped rather than kept around indefinitely.
* The unified dashboard reads exclusively from the new models.

### 5.5 Execution Model

#### 5.5.1 Normal mode (DB available)

* `POST /orchestrator/tasks` (alias) creates an orchestrator task record and step records.
* Dispatch:

  * Continue using Agent Queue for execution (same pattern as today), but rename the queue job type from `orchestrator_run` to `orchestrator_task` (keep accepting `orchestrator_run` for backward compatibility).
  * Queue payload contains `taskId` (and optionally minimal step info for better UI resilience).

#### 5.5.2 Orchestrator queue worker updates

The existing queue worker is dedicated to `orchestrator_run` jobs L7-L16 and parses payload `{runId, steps}` L51-L71.

**Update**:

* Accept both job types:

  * `orchestrator_run` (legacy) → execute legacy plan steps
  * `orchestrator_task` (new) → execute `orchestrator_task_steps` in order
* Emit queue events as it already does (“Starting orchestrator step”, etc.) for operator visibility.

### 5.6 Degraded Mode: Continue Working Without Postgres

Important nuance: as long as the Agent Queue is DB-backed, **claiming new tasks from the queue requires Postgres**. The requirement here is interpreted as:

> If Postgres becomes unavailable during execution, the orchestrator should not crash-loop; it should continue executing steps and producing artifacts, then reconcile state when DB returns.

#### 5.6.1 Introduce a “State Sink” abstraction in the orchestrator executor

Add an internal interface used by orchestrator execution code:

* `OrchestratorStateSink`

  * `record_task_status(taskId, status, timestamps, message?)`
  * `record_step_status(taskId, stepId, status, timestamps, message?, artifactRefs?, metadata?)`
  * `record_artifact(taskId, path, type, checksum, sizeBytes)`
  * `flush()`

Implementations:

1. `DbStateSink` (writes via SQLAlchemy repositories)
2. `ArtifactStateSink` (writes JSONL snapshots under the task’s artifact directory)

When DB is down:

* The executor catches DB exceptions and falls back to `ArtifactStateSink`.
* Artifacts remain the durable truth; DB becomes eventually consistent.

#### 5.6.2 Queue-worker lease/heartbeat behavior when DB is down

If Postgres drops, the queue API will fail heartbeats; leases may expire, causing reclaims later.

Mitigation options:

* **Best-effort**: keep executing locally; if queue API calls fail, log locally and continue. On reconnect, attempt to `fail_job` or `complete_job` once the API is reachable again.
* **Operator safeguard**: encourage enabling “Worker Pause” before planned DB maintenance (MoonMind already has a worker pause system, but that’s orthogonal).

### 5.7 Security and Policy Alignment

* Preserve orchestrator approval gates (existing policy helpers validate tokens and expiry) .
* Keep orchestrator skill execution restrictions (no arbitrary commands; skill executor rejects `skill_args.command`) L67-L70.
* Update dashboard auth consistency: Task UI docs note `/orchestrator/*` does not yet enforce the same auth pattern as queue routes L76-L79. As part of consolidation, align orchestrator routes to require the same `get_current_user()` dependency approach as other dashboard-facing APIs.

## 6. Migration Plan (Phased)

### Phase 1 — UI consolidation + rename (low risk)

1. Update dashboard:

   * Remove `/tasks/orchestrator` and `/tasks/orchestrator/:runId` from the client route map.
   * Add unified `/tasks/list` list (all tasks) + `/tasks/:id` detail.
2. Update server allowlist:

   * Remove `orchestrator` paths from `api_service/api/routers/task_dashboard.py` allowlist L26-L35 and only allow unified routes (`/tasks/list`, etc).
3. Rename UI labels:

   * Replace “Orchestrator Runs” with “Orchestrator Tasks”
   * Replace “runId” visible labels with “taskId” (but still display the UUID)
4. Add orchestrator endpoints aliases (`/orchestrator/tasks*`) while keeping `/orchestrator/runs*`.

### Phase 2 — Steps + skills authoring for orchestrator tasks (medium risk)

1. Extend orchestrator create request to accept `steps[]` (new), in addition to existing `skillId/skillArgs`.
2. Extend `/api/tasks/skills` to return both worker and orchestrator skill lists.
3. Update submit form so runtime=orchestrator shows:

   * step editor + per-step skill selection
   * targetService/priority/approvalToken fields

### Phase 3 — Step-state tracking v2 & Aggressive DB Migration (higher churn, unlocks “true step list”)

1. Add new orchestrator task + step tables (`orchestrator_tasks`, `orchestrator_task_steps`).
2. Migrate existing data from `orchestrator_runs` to `orchestrator_tasks` and drop the old `orchestrator_runs` tables.
3. Implement new orchestrator executor that runs `orchestrator_task_steps` and records step state.
4. Update orchestrator queue worker to process the `orchestrator_task` job type.

### Phase 4 — Degraded mode + reconciliation (resilience)

1. Add `StateSink` abstraction + artifact-based fallback.
2. Add “reconcile from artifacts” routine:

   * on startup (or periodic), if DB is reachable, import missing status/step records from artifact snapshots.

## 7. Testing Strategy

* Dashboard JS tests:

  * Unified list merges queue + orchestrator correctly.
  * `/tasks/:id` can render both sources.
  * Orchestrator runtime submit uses step editor and hits `/orchestrator/tasks`.
* API contract tests:

  * Existing orchestrator contract tests continue to pass via `/orchestrator/runs` (legacy).
  * New alias endpoints `/orchestrator/tasks` mirror the same behavior.
* Orchestrator executor tests:

  * DB sink writes step/task statuses.
  * Artifact sink writes snapshots when DB writes fail.
* Queue worker tests:

  * Accepts both `orchestrator_run` and `orchestrator_task` job types.

## 8. Resolved Questions

1. **Do we want orchestrator tasks to ever support “publish PR” semantics like worker tasks?**
   * **Decision:** Yes. Orchestrator tasks should be able to do anything normal worker tasks can do if desired. The UI and backend will not artificially hide queue-only fields for orchestrator tasks.
2. **Should orchestrator tasks appear interleaved with queue tasks on `/tasks/queue`, or do we redefine `/tasks` as the canonical “All Tasks” list and deprecate `/tasks/queue` entirely?**
   * **Decision:** We will use `/tasks/list` as the canonical idiomatic route for the unified list, since we are using a database and it is not a strict queue structure. `/tasks/queue` will be deprecated and redirect to `/tasks/list`.
3. **How aggressively do we want to migrate DB naming (`orchestrator_runs` → `orchestrator_tasks`), vs keeping DB names stable and only changing external contracts?**
   * **Decision:** Aggressively. We will run database migrations to move data from `orchestrator_runs` to `orchestrator_tasks` and drop the legacy tables, rather than maintaining dual models.

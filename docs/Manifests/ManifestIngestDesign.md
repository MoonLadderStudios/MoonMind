# Manifest Ingest Design

**Status:** Draft (2026-03-05)
**Scope:** How MoonMind ingests a “manifest” artifact and reliably turns it into one or more **Temporal Workflow Executions**, with strong observability, editability, and scalability.

---

## 1. Executive summary

A **manifest ingest** is implemented as a dedicated **Temporal Workflow Type** (e.g., `MoonMind.ManifestIngest`). A single ingest is a **Workflow Execution** that:

1. Reads a manifest from the **Artifact System** (by reference, not by embedding the file in workflow inputs).
2. Parses + validates it.
3. Compiles it into an executable “plan” (a DAG / dependency graph / run list).
4. Starts one or more **Child Workflow Executions** (e.g., `MoonMind.Run`) to execute the plan.
5. Tracks progress and produces stable, queryable metadata via **Search Attributes** + optional Memo.
6. Supports user edits via **Workflow Updates** (not custom DB mutation).

This design uses Temporal concepts directly:

* Workflow Type / Workflow Execution ([Temporal Docs][1])
* Child Workflow ([Temporal Docs][1])
* Task Queue (FIFO + routing) ([Temporal Docs][1])
* Update / Signal / Query message passing ([Temporal Docs][1])
* Search Attributes + Memo ([Temporal Docs][1])
* System limits that drive architecture decisions (payload/event-history/concurrency/update limits) ([Temporal Docs][2])

---

## 2. Problem statement

MoonMind needs a “manifest ingest” capability that is:

* **Durable**: won’t lose progress across crashes/redeploys.
* **Scalable**: can ingest manifests that describe many executions without hitting Temporal limits.
* **Observable**: can list “everything started by this manifest” and show accurate totals/progress.
* **Editable**: can accept changes while running (add/remove/modify future work).
* **Consistent**: deterministic orchestration with side-effects isolated to Activities.

Temporal is the primary workflow manager and scheduler, so manifest ingest should be expressed natively as Temporal Workflows/Activities.

---

## 3. Non-goals

* Backward compatibility with old Celery/“Agent Queue” concepts.
* Maintaining legacy naming like “worker task / orchestrator task / manifest job” inside the new system.
* Implementing a custom queue semantics layer on top of Temporal Task Queues (Temporal Task Queues are FIFO polling queues used for dispatch/routing). ([Temporal Docs][1])

---

## 4. Temporal-first terminology

These are the only “root” execution abstractions we use:

* **Workflow Type**: a name mapping to a Workflow Definition. ([Temporal Docs][1])
* **Workflow Execution**: the durable unit of execution. ([Temporal Docs][1])
* **Activity Type / Activity Execution**: side-effecting work invoked by workflows. ([Temporal Docs][1])
* **Task Queue**: FIFO queue polled by workers; used for routing tasks. ([Temporal Docs][1])
* **Update / Signal / Query**: workflow message passing mechanisms. ([Temporal Docs][3])
* **Search Attribute**: indexed metadata for listing/filtering workflow executions. ([Temporal Docs][1])
* **Memo**: non-indexed metadata returned by describe/list. ([Temporal Docs][1])

We still need one domain abstraction Temporal doesn’t provide:

* **Manifest**: a user-controlled artifact that declares what should run, with optional dependencies and parameters.

---

## 5. Key decisions

### 5.1 Manifest ingest is its own Workflow Type

**Decision:** Yes, manifest ingest is a first-class **Workflow Type**: `MoonMind.ManifestIngest`.

Rationale:

* It must be durable and reactive (supports edits, pauses, external triggers).
* It is orchestration-heavy and should not be “just an Activity”.
* It benefits from Temporal visibility, retention, and debugging.

(“Workflow Type” / “Workflow Execution” are Temporal-native concepts; we don’t invent a parallel taxonomy.) ([Temporal Docs][1])

---

### 5.2 Each executable unit in a manifest becomes a Child Workflow Execution

**Decision:** Each “node” in the compiled plan is executed as a **Child Workflow Execution** of type `MoonMind.Run` (or other small set of execution workflow types, if needed).

Rationale:

* Child workflows are a native scaling primitive and give strong lineage from ingest → runs. ([Temporal Docs][1])
* It keeps the ingest workflow mostly orchestration logic and avoids huge event histories from doing everything as Activities.
* It supports heterogeneous execution strategies (LLM vs non-LLM) inside `MoonMind.Run` via Activities (see §8).

---

### 5.3 Manifests and plans are passed by reference (ArtifactRef), not embedded payloads

Temporal Cloud has strict message/payload and event history transaction limits (e.g., max payload per request, gRPC message size, and event history transaction size). ([Temporal Docs][2])
Therefore:

* Workflow inputs/outputs contain only **ArtifactRef** and small metadata.
* Full manifest bytes, compiled plan, and large outputs live in the Artifact Store.

---

### 5.4 Edits use Updates (not DB writes), and optional Signals for fire-and-forget

Temporal’s guidance:

* Signals = async write requests, no response.
* Updates = synchronous, tracked write requests with response and validation. ([Temporal Docs][3])
  **Decision:**
* Use **Updates** for anything “editable” that needs validation/ack (replace manifest, change concurrency, pause/resume, cancel pending nodes).
* Use **Signals** only for low-value fire-and-forget nudges (e.g., “refresh status soon”), if needed. ([Temporal Docs][1])

---

## 6. Interfaces

### 6.1 Workflow Type: `MoonMind.ManifestIngest`

#### Input: `ManifestIngestInput`

Minimal (references only):

* `manifest_ref: ArtifactRef`
* `requested_by: PrincipalRef` (user/service identity)
* `execution_policy: ExecutionPolicy`

  * `max_concurrency: int` (default 50–200, hard cap < 500 recommended; see limits) ([Temporal Docs][2])
  * `failure_mode: enum { FAIL_FAST, BEST_EFFORT }`
  * `default_task_queues: { activity_cpu, activity_llm, ... }` (optional; see §8)
* `tags: map<string,string>` (small; for Search Attributes / Memo)

#### Output: `ManifestIngestResult`

* `plan_ref: ArtifactRef` (compiled plan)
* `summary_ref: ArtifactRef` (final summary: counts, statuses, pointers to run executions)
* `manifest_digest: string` (sha256)
* `run_index_ref: ArtifactRef` (optional: a canonical “index” file containing run workflow IDs + node IDs)

---

### 6.2 Child Workflow Type: `MoonMind.Run`

Not fully specified here; the key contract for ingest is:

* Input includes:

  * `manifest_ingest_workflow_id: string`
  * `node_id: string`
  * `node_input_ref: ArtifactRef` (if large)
  * `runtime_hints` (LLM vs non-LLM preference; see §8)

* Output includes:

  * `result_ref: ArtifactRef`
  * small structured result summary (status, timestamps, etc.)

---

### 6.3 Update handlers on `MoonMind.ManifestIngest`

From Temporal glossary: **Update** is “a request to and a response from Workflow Execution.” ([Temporal Docs][1])
Also, Temporal SDKs commonly support validators to reject invalid updates early (Python SDK: update validators can throw to reject before events are written). ([GitHub][4])

Proposed Updates:

1. `UpdateManifest(new_manifest_ref: ArtifactRef, mode: enum { REPLACE_FUTURE, APPEND }) -> UpdateManifestResult`

   * `REPLACE_FUTURE`: keep already-started nodes; recompute remaining graph.
   * `APPEND`: add new nodes (must not modify existing node IDs).

2. `SetConcurrency(max_concurrency: int) -> ok`

3. `Pause() -> ok`

4. `Resume() -> ok`

5. `CancelNodes(node_ids: list<string>) -> CancelResult`

   * Cancels nodes not yet started; optionally requests cancel for running child workflows.

6. `RetryNodes(node_ids: list<string>) -> RetryResult`

   * Only for nodes in terminal failed states; restarts via new child workflow run IDs or by a retry mechanism in `MoonMind.Run`.

---

### 6.4 Query handlers

Queries are the primary “read current state” mechanism; they don’t add to Event History and can be used for status polling. ([Temporal Docs][3])

Proposed Queries:

* `GetStatus() -> ManifestIngestStatus`
* `ListReadyNodes(cursor, limit) -> Page<NodeSummary>`
* `ListRunningNodes(cursor, limit) -> Page<NodeSummary>`
* `ListCompletedNodes(cursor, limit) -> Page<NodeSummary>`

(For large node lists, these query results should be backed by Artifact Store indexes rather than returning massive payloads.)

---

## 7. Manifest lifecycle

### 7.1 States

Manifest ingest Workflow Execution states (domain-level):

* `RECEIVED` (workflow started, manifest_ref recorded)
* `VALIDATED`
* `COMPILED` (plan_ref stored)
* `RUNNING`
* `PAUSED`
* `COMPLETED`
* `FAILED`
* `CANCELED`

These should be mirrored into Search Attributes for UI filtering (see §10). ([Temporal Docs][1])

---

### 7.2 Pipeline (high level)

```
Client/API
  -> StartWorkflowExecution: MoonMind.ManifestIngest(manifest_ref)
       -> Activity: Read manifest bytes (Artifact System)
       -> Activity: Parse + validate
       -> Activity: Compile to plan DAG
       -> Activity: Persist plan (Artifact System)
       -> Start child workflows: MoonMind.Run(node_i) with concurrency limit
       -> Await children, update progress
       -> Activity: Write summary + run index artifacts
       -> Complete
```

---

## 8. Where execution happens and how we support LLM vs non‑LLM runtimes

### 8.1 Deterministic workflow code; runtime selection in Activities

Workflow code must remain deterministic and should avoid external I/O. Activities are where side effects happen (network calls, LLM calls, filesystem, etc.). (This is a core Temporal programming model principle; see general Temporal docs and SDK guidance.) ([Temporal Docs][3])

**Decision:** Choose LLM vs non‑LLM at the **Activity level** via Task Queue routing.

Reason:

* Task Queues are a routing mechanism: workers poll task queues for tasks. ([Temporal Docs][1])
* `MoonMind.Run` can schedule:

  * “LLM Activities” on `mm.activity.llm`
  * “CPU/IO Activities” on `mm.activity.default`
  * “Sandbox Activities” on `mm.activity.sandbox`

The manifest (or compiled plan) can contain **runtime hints**, but the actual selection mechanism is “which Activity Type and Task Queue does the workflow schedule”.

This avoids forcing a single runtime choice at the Workflow level and supports mixed workloads in one run.

---

### 8.2 Task Queue naming (proposed)

* Workflow task queue:

  * `mm.workflow` (hosts `MoonMind.ManifestIngest`, `MoonMind.Run`)
* Activity task queues:

  * `mm.activity.default` (general CPU/IO)
  * `mm.activity.llm` (LLM calls, GPU-backed if needed)
  * `mm.activity.integrations` (external APIs)
  * `mm.activity.artifacts` (artifact read/write)

Reminder: Task Queue is FIFO and polled by workers; it’s not a business-level “priority queue” abstraction. ([Temporal Docs][1])

---

## 9. Plan compilation model

### 9.1 Compiled plan format

**Plan** is a compiled, normalized representation of a manifest, stored as an artifact:

* `plan.version`
* `nodes: { node_id -> NodeSpec }`
* `edges: dependencies`
* `defaults: concurrency, failure_mode, timeouts`
* `node_groups` (optional)
* `artifact_refs` for any large static inputs

The plan is an internal artifact that the ingest workflow and the run workflows can both consume.

### 9.2 Node IDs must be stable

To support edits and idempotency:

* Node IDs must be stable across re-ingests where semantics didn’t change.
* Node IDs should be content-addressed or explicitly defined in the manifest.

---

## 10. Execution algorithm inside `MoonMind.ManifestIngest`

### 10.1 Orchestration state

The workflow maintains:

* `pending: set[node_id]`
* `ready: set[node_id]` (deps satisfied)
* `running: map[node_id -> child_workflow_id]`
* `completed: map[node_id -> status]`
* `failed: map[node_id -> failure_info]`
* `paused: bool`

For large manifests, this state must be checkpointed in artifacts periodically to enable Continue-As-New without losing track (see §11). ([Temporal Docs][1])

### 10.2 Concurrency control

Temporal Cloud imposes per-workflow limits on **incomplete** Activities/Signals/Child Workflows (and recommends keeping concurrent operations to ~500 or fewer for performance). ([Temporal Docs][2])

**Policy:**

* Default `max_concurrency = 100` (tunable).
* Hard cap `max_concurrency <= 500` unless explicitly overridden with clear risks. ([Temporal Docs][2])
* Never allow the workflow to have > 1,500 running children to stay away from 2,000 incomplete-child-workflow command failures. ([Temporal Docs][2])

Implementation:

* A simple semaphore in workflow code controls how many child workflows are started at a time.
* When a child completes, a slot is freed, new ready nodes are started.

### 10.3 Failure handling

Two modes:

* **FAIL_FAST**: on first node failure, stop scheduling new nodes, request cancel on running children, finalize as FAILED.
* **BEST_EFFORT**: continue scheduling independent nodes, finalize as COMPLETED_WITH_ERRORS (domain) but workflow can still “complete successfully” from Temporal’s perspective if desired.

Retries:

* Activities follow Temporal Retry Policy mechanics (server-managed). ([Temporal Docs][1])
* Child workflows may implement internal retry or be restarted by ingest (policy-driven).

Idempotency:

* Activities must be designed to be idempotent where they can be retried (Temporal explicitly recommends idempotent operations). ([Temporal Docs][1])

---

## 11. Scaling and limits (why the design looks like this)

Temporal Cloud limits directly constrain manifest ingest:

### 11.1 Payload / message size

* Per-message gRPC limit and payload limits are bounded; large inputs should not be embedded in workflow history. ([Temporal Docs][2])
  **Therefore:** manifests/plans/results must be ArtifactRefs, not embedded JSON blobs.

### 11.2 Event History limits

A Workflow Execution Event History is limited (event count and size), and this applies to Temporal Cloud and other deployments. ([Temporal Docs][2])

Temporal glossary: **Continue-As-New** passes relevant state to a new Workflow Execution with a fresh Event History. ([Temporal Docs][1])

**Strategy:**

* The ingest workflow uses **Continue-As-New** when:

  * the number of scheduled nodes/events suggests the run is approaching history limits, or
  * after every N node completions (e.g., every 500–2,000) for very large manifests.
* Before continuing-as-new, persist orchestration state (running children IDs, completed set, remaining graph) to an artifact, then pass only a reference + minimal counters to the next run.

### 11.3 Per-workflow execution concurrency limits

Temporal Cloud: if a workflow has ~2,000 incomplete Activities/Signals/Child Workflows/cancel requests, additional commands of that type fail; recommended to limit concurrent ops to ~500. ([Temporal Docs][2])
**Therefore:** concurrency is bounded and enforced regardless of manifest size.

### 11.4 Update and signal limits

Temporal Cloud limits include:

* Max in-flight Updates, total Updates in history, and signal limits per Workflow Execution. ([Temporal Docs][2])
  **Therefore:**
* Use Updates sparingly and coalesce “chatty” UI operations.
* Prefer: “UpdateManifest once” rather than “update per node”.

---

## 12. Cancellation and parent/child semantics

When `MoonMind.ManifestIngest` starts children, it must specify what happens if the parent closes.

Temporal glossary: **Parent Close Policy** determines what happens to a Child Workflow Execution if its parent closes. ([Temporal Docs][1])

**Default choice:**

* `ParentClosePolicy = REQUEST_CANCEL` for `MoonMind.Run` children:

  * If user cancels the ingest, children receive cancellation requests.
  * If ingest completes normally, children should already be done; if not, treat as an invariant violation.

(If you need “fire-and-forget runs”, use `ABANDON`, but that is usually the wrong default for a manifest-driven orchestrator.)

---

## 13. Observability and indexing

### 13.1 Search Attributes (indexed)

Temporal glossary: Search Attributes are indexed names used in list filters for Workflow Executions. ([Temporal Docs][1])

**Manifest ingest workflow Search Attributes:**

* `mm.ManifestId` = WorkflowId (or external manifest ID)
* `mm.ManifestDigest` = sha256
* `mm.Status` = RECEIVED/VALIDATED/COMPILED/RUNNING/PAUSED/COMPLETED/FAILED
* `mm.RequestedBy` = user/service
* `mm.Source` = upload/git/integration
* `mm.TotalNodes` = int
* `mm.CompletedNodes` = int
* `mm.FailedNodes` = int

**Run workflow Search Attributes:**

* `mm.ManifestId` = parent manifest ingest workflow id
* `mm.NodeId` = node id
* `mm.Status` = RUNNING/COMPLETED/FAILED/etc.
* `mm.Runtime` = llm/default/sandbox (optional)

This enables:

* List all runs started by a manifest via `mm.ManifestId = ...`
* Paginate accurately using Temporal Visibility APIs (and the returned page tokens).

### 13.2 Memo (non-indexed)

Temporal glossary: Memo is non-indexed user metadata returned on list/describe. ([Temporal Docs][1])
Use Memo for:

* small human-readable summary
* “latest plan_ref”
* UI display hints (not used for filtering)

### 13.3 A canonical “Run Index” artifact

To avoid relying purely on visibility reads (which have rate limits), write an index artifact:

* `run_index.jsonl` or `run_index.parquet`
* Contains `{node_id, run_workflow_id, run_id, started_at, ...}`

This index is:

* produced incrementally during execution
* used by UI for pagination/counts without merging multiple sources

---

## 14. Security and authorization

Manifest ingest must enforce:

1. **Caller is authorized to start ingest** (API layer).
2. **Caller is authorized to read the manifest artifact** and any referenced artifacts.
3. **Run workflows inherit authorization context** (principal, tenant, org) as immutable workflow input metadata + Search Attributes.

Artifact access control is enforced by the Artifact System (per your Artifact System Design), with signed URLs or brokered reads. (Manifest ingest never embeds secrets in workflow history.)

---

## 15. Activity design (minimal set)

Activities called by `MoonMind.ManifestIngest`:

1. `Artifacts.Read(ref) -> bytes/stream`
2. `Manifest.Parse(bytes) -> ManifestAst`
3. `Manifest.Validate(ast) -> ValidationResult`
4. `Manifest.Compile(ast) -> Plan`
5. `Artifacts.Write(plan) -> plan_ref`
6. `Artifacts.Write(summary/index) -> summary_ref/index_ref`

Notes:

* Keep Activities idempotent where possible. ([Temporal Docs][1])
* Apply Retry Policies appropriate to each external dependency. ([Temporal Docs][1])

Optional (future):

* Use **Asynchronous Activity Completion** when integrating external systems that complete later via callback/webhook. ([Temporal Docs][1])

---

## 16. How this supports “total counts” and correct pagination in the UI

The UI should **not** infer totals by merging unrelated lists. Instead:

* The ingest workflow computes `TotalNodes` directly from the compiled plan and stores it as a Search Attribute (`mm.TotalNodes`). ([Temporal Docs][1])
* Each `MoonMind.Run` execution is tagged with `mm.ManifestId` so the UI can list runs for a manifest via a single indexed filter query. ([Temporal Docs][1])
* For fast pagination, the UI can:

  1. Use Temporal Visibility pagination for “live truth”, respecting visibility read rate limits. ([Temporal Docs][2])
  2. Or read the “run index” artifact for a stable snapshot view (especially if Temporal visibility reads are costly).

This eliminates the class of bugs where “page totals” are computed from one source while “next page” cursor is driven by another source.

---

## 17. Open questions

1. **Manifest schema**: How expressive should dependencies be (simple DAG vs conditionals vs dynamic fan-out)?
2. **Node execution mapping**: Is every node always a `MoonMind.Run` child workflow, or do we allow “inline” nodes as Activities for very small units?
3. **Delta semantics on UpdateManifest**: Do we allow changing existing node definitions, or only append new nodes?
4. **Failure semantics**: Should overall ingest Workflow Execution fail when any node fails, or should it complete successfully with errors and rely on summary artifacts?
5. **Sharding strategy**: At what size do we require Continue-As-New vs hierarchical shard ingests?

# Manifest Ingest Design & Implementation

**Implementation tracking:** [`docs/tmp/remaining-work/Rag-ManifestIngestDesign.md`](../tmp/remaining-work/Rag-ManifestIngestDesign.md)

**Status:** Draft (2026-03-20)
**Scope:** How MoonMind ingests a “manifest” artifact and reliably turns it into one or more **Temporal Workflow Executions**, with strong observability, editability, and scalability. Includes architecture, design decisions, and implementation-level detail.
**See also:** [LlamaIndexManifestSystem.md](LlamaIndexManifestSystem.md) (v0 manifest schema & operator guide), [WorkflowRag.md](WorkflowRag.md) (how agents retrieve data from Qdrant at runtime)

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

This document describes the **Temporal-managed manifest path** specifically. During migration, MoonMind may still also have queue-backed manifest flows elsewhere in the product. Those should not be relabeled as Temporal-backed until the execution substrate has actually moved.

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

* Preserving old Celery/“Agent Queue” naming or semantics inside the Temporal-native implementation.
* Maintaining legacy naming like “worker task / system task / manifest job” inside the new system.
* Implementing a custom queue semantics layer on top of Temporal Task Queues (Temporal Task Queues are FIFO polling queues used for dispatch/routing). ([Temporal Docs][1])
* Claiming that every current manifest submission path in MoonMind already runs through `MoonMind.ManifestIngest` or that queue-backed manifest flows disappear immediately on adoption of this design.

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

Migration note:

* Temporal-managed manifest executions should appear as `source=temporal`, `entry=manifest` in compatibility surfaces.
* Queue-backed manifest jobs remain `source=queue` until they are actually migrated.

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

`GetStatus()` should distinguish:

* canonical workflow lifecycle state (`initializing`, `executing`, `finalizing`, `succeeded`, `failed`, `canceled`)
* finer-grained manifest ingest phase for UI/debugging, if needed

---

## 7. Manifest lifecycle

### 7.1 States

Manifest ingest should use the **shared MoonMind workflow state model** already defined by the Temporal lifecycle and visibility docs.

Canonical `mm_state` values:

* `initializing`
* `executing`
* `finalizing`
* `succeeded`
* `failed`
* `canceled`

Interpretation for manifest ingest:

* `initializing`: manifest ref accepted; read/parse/validate/compile in progress
* `executing`: child execution orchestration in progress
* `finalizing`: summary/index aggregation in progress
* `succeeded` / `failed` / `canceled`: terminal states

If the product needs more granular progress labels such as “validated” or “compiled”, expose those as bounded phase metadata in Memo, query results, or summary artifacts rather than redefining the canonical workflow state taxonomy.

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

  * LLM/model-facing Activities on `mm.activity.llm`
  * repo/command/sandbox Activities on `mm.activity.sandbox`
  * integration Activities on `mm.activity.integrations`
  * artifact IO Activities on `mm.activity.artifacts`

The manifest (or compiled plan) can contain **runtime hints**, but the actual selection mechanism is “which Activity Type and Task Queue does the workflow schedule”.

This avoids forcing a single runtime choice at the Workflow level and supports mixed workloads in one run.

---

### 8.2 Task Queue naming (proposed)

* Workflow task queue:

  * `mm.workflow` (hosts `MoonMind.ManifestIngest`, `MoonMind.Run`)
* Activity task queues:

  * `mm.activity.artifacts` (artifact read/write)
  * `mm.activity.llm` (LLM calls, GPU-backed if needed)
  * `mm.activity.sandbox` (repo/command/sandbox operations)
  * `mm.activity.integrations` (external APIs)

Provider- or domain-specific sub-queues are deferred, not default. Add them only when stronger isolation or separate scaling is required.

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
* **BEST_EFFORT**: continue scheduling independent nodes, write an explicit summary artifact describing partial failure, and choose terminal close semantics deliberately rather than inventing an undocumented pseudo-state.

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

(If you need “fire-and-forget runs”, use `ABANDON`, but that is usually the wrong default for a manifest-driven system.)

---

## 13. Observability and indexing

### 13.1 Search Attributes (indexed)

Temporal glossary: Search Attributes are indexed names used in list filters for Workflow Executions. ([Temporal Docs][1])

For v1, this design should align with the shared Visibility registry rather than inventing a second naming system.

Required shared fields for `MoonMind.ManifestIngest`:

* `mm_owner_type`
* `mm_owner_id`
* `mm_state`
* `mm_updated_at`
* `mm_entry = "manifest"`

Optional bounded fields already allowed by the shared registry may include:

* `mm_repo`
* `mm_integration`

Rules:

* do **not** introduce ad hoc Search Attribute names such as `mm.ManifestId` or `mm.Status`
* do **not** store large manifests, prompts, or unbounded user text in Search Attributes
* if manifest-specific indexed lineage becomes necessary, standardize a bounded field such as `mm_manifest_ingest_id` by updating the shared Visibility contract first

### 13.2 Memo (non-indexed)

Temporal glossary: Memo is non-indexed user metadata returned on list/describe. ([Temporal Docs][1])
Use Memo for:

* small human-readable summary
* `manifest_ref` / `plan_ref` when safe
* bounded ingest-phase metadata for detail views
* UI display hints (not used for filtering)
* small progress counters when the product needs them but they are not yet part of the indexed registry

Memo should stay small and display-oriented. Do not place secrets, full manifest bodies, large prompts, or unbounded user text in Memo.

### 13.3 A canonical “Run Index” artifact

To avoid relying purely on visibility reads (which have rate limits), write an index artifact:

* `run_index.jsonl` or `run_index.parquet`
* Contains `{node_id, run_workflow_id, run_id, started_at, ...}`

This index is:

* produced incrementally during execution
* used by UI/detail views for stable per-manifest lineage without merging multiple sources
* a better near-term source for “runs started by this manifest” than inventing undocumented Search Attribute names

---

## 14. Security and authorization

Manifest ingest must enforce:

1. **Caller is authorized to start ingest** (API layer).
2. **Caller is authorized to read the manifest artifact** and any referenced artifacts.
3. **Run workflows inherit authorization context** (principal, tenant, org) as immutable workflow input metadata + Search Attributes.

Artifact access control is enforced by the Artifact System (per your Artifact System Design), with signed URLs or brokered reads.

Additional guardrails:

* never place raw credentials, signed URLs, prompts with secrets, or full manifest contents into workflow history
* never place secrets or high-cardinality payloads into Search Attributes or Memo
* keep authorization lineage bounded and immutable so visibility/filtering can rely on it safely

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

* The ingest workflow computes total nodes directly from the compiled plan and writes them into summary artifacts and/or bounded detail metadata.
* The manifest ingest execution itself is listed through the shared Temporal Visibility model (`mm_entry=manifest` plus the standard execution fields).
* Child-run listings for a manifest should come from the canonical `run_index_ref` artifact until a shared manifest-lineage Search Attribute is explicitly standardized.
* For fast pagination, the UI can:

  1. Use Temporal Visibility pagination for “live truth”, respecting visibility read rate limits. ([Temporal Docs][2])
  2. Or read the “run index” artifact for a stable per-manifest snapshot view (especially if Temporal visibility reads are costly or lineage fields are not yet standardized).

This eliminates the class of bugs where “page totals” are computed from one source while “next page” cursor is driven by another source, or where the UI invents its own cross-source notion of manifest lineage.

---

## 17. Implementation details

### 17.1 Module layout

| Module | Responsibility |
|---|---|
| `moonmind/workflows/temporal/manifest_ingest.py` | `MoonMind.ManifestIngest` workflow, projection helpers, plan compilation, summary builders, all 6 Updates |
| `moonmind/workflows/temporal/workflows/manifest_ingest.py` | Lightweight workflow variant for compile-and-summarize flows |
| `moonmind/workflows/agent_queue/manifest_contract.py` | Manifest YAML validation, normalization, capability derivation, secret leak detection, secret ref collection |
| `moonmind/schemas/manifest_ingest_models.py` | Pydantic models: `CompiledManifestPlanModel`, `ManifestPlanNodeModel`, `ManifestNodeModel`, `ManifestStatusSnapshotModel`, `ManifestIngestSummaryModel`, `ManifestRunIndexModel`, `ManifestExecutionPolicyModel`, `RequestedByModel` |
| `moonmind/schemas/manifest_models.py` | Legacy manifest schema models |
| `moonmind/manifest/*` | Legacy loader, interpolation, runner, sync service |

### 17.2 Workflow input example

```json
{
  "manifestArtifactRef": "art_01JABC...",
  "action": "run",
  "requestedBy": { "type": "user", "id": "user-123" },
  "executionPolicy": {
    "failurePolicy": "fail_fast",
    "maxConcurrency": 50
  },
  "planArtifactRef": null,
  "manifestNodes": []
}
```

Key fields:
- `manifestArtifactRef` (required): Artifact reference to the manifest YAML stored in MinIO. The workflow reads manifest content via the `manifest_read` Activity — raw YAML is never inlined in workflow history.
- `action`: `"run"` or `"plan"` (default `"run"`).
- `requestedBy`: Immutable owner identity, validated against the workflow's `mm_owner_id` Search Attribute.
- `executionPolicy`: Controls concurrency and failure behavior.
- `planArtifactRef` + `manifestNodes`: Optional pre-compiled plan for resumption or re-execution.

### 17.3 Pipeline stages

The workflow executes the following stages:

1. **Initialize**: Validate `manifestArtifactRef`, resolve `requestedBy` against workflow owner metadata, normalize execution policy.
2. **Compile**: If no pre-compiled plan is provided:
   - `manifest_read` Activity — reads manifest YAML from the artifact store.
   - `manifest_compile` Activity — validates YAML via `normalize_manifest_job_payload`, derives required capabilities, computes manifest hash, produces a `CompiledManifestPlanModel` with stable node IDs and dependency edges.
3. **Materialize nodes**: Convert compiled plan nodes to runtime `ManifestNodeModel` entries with initial state `ready`.
4. **Execute (fan-out)**: For each ready node (respecting dependency ordering and concurrency limits):
   - Spawn a child `MoonMind.Run` workflow with the node's parameters, linked to the parent via `manifestIngestWorkflowId` and `nodeId`.
   - Track child state transitions (`running` → `succeeded`/`failed`).
   - Apply failure policy: `fail_fast` cancels remaining nodes on first failure; `continue` proceeds.
5. **Finalize**: Execute `manifest_write_summary` Activity to produce summary and run-index artifacts.

### 17.4 Idempotency and stable node IDs

Node IDs are derived deterministically from manifest content:

```python
node_id = f"node-{sha256(json.dumps(data_source, sort_keys=True))[:12]}"
```

This ensures that repeated runs against the same manifest produce the same node graph, enabling:
- Stable child workflow IDs (`{workflow_id}:{run_id}:{node_id}`)
- Idempotent artifact linkage
- Meaningful diff detection between manifest versions

### 17.5 Manifest hash and version tracking

The manifest contract computes a content-addressable hash:

```
manifestHash = sha256:{hex_digest_of_yaml_content}
```

Combined with `manifestVersion` (currently `v0`), this provides:
- Change detection for incremental sync decisions
- Audit trail for which manifest version produced which run
- Safe retry semantics (same hash = same behavior)

### 17.6 Artifact-backed state

Manifest runs produce three key artifacts:

| Artifact | Description |
|---|---|
| Summary (`summaryArtifactRef`) | `ManifestIngestSummaryModel` — workflow state, phase, counts, failed node IDs |
| Run Index (`runIndexArtifactRef`) | `ManifestRunIndexModel` — per-node state, child workflow/run IDs, result artifact refs |
| Checkpoint (`checkpointArtifactRef`) | Per-(manifest, dataSource) state for incremental sync resumption |

These are referenced via memo fields and stored in MinIO via the artifact Activities.

### 17.7 UpdateManifest modes

- **`APPEND`**: Add new nodes from the updated manifest without modifying existing nodes. Fails if any new node IDs collide with existing ones.
- **`REPLACE_FUTURE`**: Replace all `pending`/`ready` nodes with the new manifest's plan. Running, succeeded, and failed nodes are preserved.

---

## 18. Security model

### 18.1 No raw secrets in Temporal History

Temporal inputs and workflow history MUST NOT contain raw keys/tokens.
Allowed reference patterns:
* `${ENV_VAR}` references (resolved by worker runtime env)
* `profile://provider#field` references (resolved by the Activity fetching the integration secret at runtime)
* `vault://mount/path#field` references (resolved via Vault at runtime)

The manifest contract (`manifest_contract.py`) enforces this at validation time via `detect_manifest_secret_leaks`, which scans all manifest values for:
- Known sensitive field names (`api_key`, `token`, `password`, etc.)
- Suspect value prefixes (`sk-`, `ghp_`, `AKIA`, etc.)
- JWT patterns and base64-encoded secrets

### 18.2 Secret reference collection

`collect_manifest_secret_refs` extracts and deduplicates all `profile://` and `vault://` references from the manifest, producing a `manifestSecretRefs` map that workers use to resolve credentials at Activity execution time.

---

## 19. Delivery scope

**Delivered baseline:** `MoonMind.ManifestIngest` workflow and tests, `/api/manifests` registry, manifest contract validation and normalization, compiled plan model and node materialization, Temporal Updates for interactive control, and projection/snapshot plumbing for API queries.

**Remaining work:** data-fetch and embedding pipeline activities, incremental checkpoint semantics, Qdrant integration, and Mission Control list/detail/launch and node-level controls. Phased sequencing and verification are tracked in [`docs/tmp/remaining-work/Rag-ManifestIngestDesign.md`](../tmp/remaining-work/Rag-ManifestIngestDesign.md).

---

## 20. Open questions

1. **Manifest schema**: How expressive should dependencies be (simple DAG vs conditionals vs dynamic fan-out)?
2. **Node execution mapping (future extension)**: Do we ever introduce inline activity-only nodes later, or keep the v1 rule that manifest nodes execute as child workflows?
3. ~~**Delta semantics on UpdateManifest**: Do we allow changing existing node definitions, or only append new nodes?~~ **Resolved:** Both `APPEND` and `REPLACE_FUTURE` modes are supported (see §17.7).
4. **Failure semantics**: In `BEST_EFFORT` mode, should the Temporal execution close as `succeeded` with errors recorded in summary artifacts, or close as `failed` once any node failure occurs?
5. **Visibility lineage**: When do we standardize a bounded manifest-lineage Search Attribute (for example `mm_manifest_ingest_id`) instead of relying on the run index artifact for child-run lookup?
6. **Sharding strategy**: At what size do we require Continue-As-New vs hierarchical shard ingests?

# MoonMind Temporal-First Architecture

## 0) Intent

Redesign MoonMind to use **Temporal** as the **primary workflow manager and scheduling system**.

MoonMind will align to Temporal’s core abstractions:

* **Workflow Execution** is the primary “thing” the product creates, lists, edits, cancels, and observes.
* **Activity** is the only place side effects happen (LLM calls, filesystem work, GitHub/Jules calls, running tests, etc.).
* **Task Queue** exists only as Temporal plumbing for worker routing; it is **not** a product-level “queue” and we do not promise queue-like ordering semantics to users.

MoonMind keeps only domain concepts that Temporal doesn’t cover:

* **Skill** (a capability contract)
* **Plan** (a structured set/graph of skill invocations)
* **Artifact** (durable storage for large inputs/outputs)

Notably absent: “task”, “job”, “work item”, and any “spec” naming in runtime execution.

## 1) Goals and non-goals

### Goals

1. **Single canonical unit in the UI**: every row is a **Workflow Execution**.
2. **Correct pagination + totals** via Temporal **Visibility** APIs (no merged pagers).
3. **Edits** implemented via Temporal **Update** (request/response) and/or **Signal**.
4. **Scheduling / polling / retries** handled by Temporal (Timers, Retry Policies, Schedules).
5. **Manifest ingestion** is first-class and cleanly modeled in Temporal terms.
6. **LLM and non-LLM execution** supported without creating competing abstractions.
7. **No Celery** and no competing workflow engines.

### Non-goals

* Preserving old APIs, old database schemas, or old queue behavior.
* Pretending Task Queues are user-visible queues with stable ordering guarantees.

## 2) Vocabulary (what MoonMind will say)

### Temporal-native terms (use these in code/docs/UI where feasible)

* **Workflow Execution**: a specific running/completed instance of a workflow.
* **Workflow Type**: the name that maps to a workflow definition.
* **Activity / Activity Type**: side-effecting work + its name-to-definition mapping.
* **Task Queue**: the routing point workers poll from.
* **Worker**: a process that executes workflow and/or activity tasks.
* **Signal**: async message to a workflow execution.
* **Update**: request/response interaction that can mutate workflow state.
* **Timer**: deterministic waiting inside workflows.
* **Schedule**: server-side mechanism to start workflows on a timetable.
* **Visibility / Search Attributes / Memo**: listing, filtering, and display metadata.

### MoonMind domain terms (kept because Temporal doesn’t provide them)

* **Skill**: a named capability with inputs/outputs and an executor implementation.
* **Plan**: structured set/graph of skill invocations.
* **Artifact**: stored inputs/outputs referenced by ID/URI (object store, DB, etc.).

## 3) Top-level architecture

### Components

1. **Temporal Service**

   * Cluster (self-hosted or cloud)
   * Visibility store enabled as needed for list queries and counts

2. **MoonMind API (FastAPI)**

   * Auth and user/session management
   * Starts workflow executions
   * Sends Updates / Signals
   * Lists workflow executions via Visibility
   * Issues presigned upload/download for artifacts

3. **Artifact Store**

   * Object store (S3/GCS/MinIO) recommended
   * Stores large payloads: manifests, plans, patches, logs, outputs, attachments
   * Prevents bloating workflow histories

4. **Skill Registry**

   * Source of truth for what skills exist, schemas, and executor bindings
   * Can be code-defined + config (versioned)

5. **Temporal Workers**

   * **Workflow Worker(s)** (orchestration only; deterministic code)
   * **Activity Worker fleets** segmented by capability, isolation, and secrets:

     * LLM activities
     * Sandbox/command execution activities
     * Integrations (GitHub, Jules, etc.)
     * Artifact I/O activities (if not local)

### Dataflow (high level)

* User triggers “run” → API starts a **Workflow Execution** (Workflow Type).
* Workflow orchestrates steps and calls **Activities**.
* Activities read/write **Artifacts** and interact with integrations.
* Dashboard lists **Workflow Executions** directly from Temporal Visibility.

## 4) Temporal modeling: Workflow Types (root-level differentiation)

MoonMind will avoid introducing a new “kind” abstraction. Root-level differentiation is done with **Workflow Type**.

### Proposed Workflow Types

#### A) `MoonMind.Run`

General-purpose execution:

* can involve planning (via skills)
* can involve executing skills directly
* can include external integrations and long-lived waiting

This is the default “start a run” entry point.

#### B) `MoonMind.ManifestIngest`

Manifest ingestion and orchestration:

* turns a manifest artifact into a Plan (or set of Plans)
* executes that Plan by orchestrating Activities and/or spawning child workflow executions

This exists as a separate Workflow Type because manifest ingestion typically implies:

* multi-run fan-out
* dependency graphs
* partial failure policy choices
* aggregation of results

If you later decide manifests are “just another input format,” `MoonMind.ManifestIngest` can become a thin workflow that immediately starts one or more `MoonMind.Run` executions and exits.

## 5) Execution model: Activities, not “runtimes baked into workflows”

### Key rule

A workflow orchestration can mix LLM and non-LLM work in one execution. **Selection happens at the Activity level**, not by creating separate workflow types per runtime.

### How this works

* The workflow interprets a **Plan** (or directly a single skill invocation).
* Each step resolves to:

  * an **Activity Type** (what to execute)
  * and a **Task Queue** (which worker fleet can execute it)

This allows a single `MoonMind.Run` execution to do:

* non-LLM steps (git operations, running tests, file transforms),
* LLM steps (planning, codegen),
* integration steps (Jules, GitHub),
  in a single coherent, durable execution.

## 6) Task Queues: required plumbing, not product semantics

Temporal requires Task Queues. MoonMind uses them **only** for routing.

### Naming conventions (recommended)

* Workflow task queue:

  * `mm.workflow`

* Activity task queues (routing by capability):

  * `mm.activity.llm.codex`
  * `mm.activity.llm.gemini`
  * `mm.activity.llm.claude`
  * `mm.activity.sandbox`
  * `mm.activity.integrations.github`
  * `mm.activity.integrations.jules`
  * `mm.activity.artifacts`

Optional: add priority lanes by suffix if needed:

* `...:high`, `...:normal`, `...:low`

**Important:** Priority lanes are a routing/throughput mechanism, not an ordering promise.

## 7) Inputs, outputs, and “no spec in runtime naming”

MoonMind will not use “spec” terminology in execution code. Inputs and outputs are:

* **Artifact references** (large payloads)
* **Skill and Plan data structures** (small structured JSON)

### Workflow inputs (examples)

#### `MoonMind.Run` input

* `title` (string, optional)
* `requested_skill` (string | null)
* `plan_artifact_id` (optional) — if the user provides a plan
* `input_artifact_id` (optional) — user text/instructions/blob
* `initial_parameters` (small JSON for toggles)

#### `MoonMind.ManifestIngest` input

* `manifest_artifact_id` (required)
* `execution_parameters` (small JSON: concurrency caps, failure policy)

### Where payloads live

* Anything large (instructions, manifests, diffs, logs, generated files) lives in **Artifact Store**
* Temporal history carries only references + small state

## 8) Edits: Temporal Updates as the primary mechanism

MoonMind’s “edit” feature is modeled as a Temporal **Update** to a Workflow Execution.

### Update semantics (recommended)

#### Update: `UpdateInputs`

Payload:

* `input_artifact_id` (optional)
* `plan_artifact_id` (optional)
* `parameters_patch` (optional)

Behavior in workflow:

* validate the update (schema, permissions, state)
* decide when to apply:

  * immediately if safe
  * at the next “stable point” (between steps)
  * or trigger **Continue-As-New** to restart orchestration with new inputs cleanly

Return value:

* `accepted: bool`
* `effective_at: "now" | "next_step" | "continue_as_new"`
* `message`

### Cancellation

Use Temporal cancellation on the workflow execution, optionally with a Signal for graceful shutdown.

## 9) Manifest ingestion (dedicated section)

### Question: “Does this need its own root type?”

In Temporal terms, the deciding factor is whether manifest ingestion requires materially different orchestration behavior. In practice, manifests usually imply:

* graph orchestration
* fan-out/fan-in
* result aggregation
* policy controls (continue on error, fail fast, etc.)

That maps cleanly to a dedicated **Workflow Type**, so the proposal is **yes**: `MoonMind.ManifestIngest`.

### Where it executes

* Orchestration: workflow worker (`mm.workflow`)
* Parsing/validation/compilation: Activities (e.g., `mm.activity.artifacts` or `mm.activity.sandbox` depending on implementation)
* Step execution: Activities routed to the correct worker fleet (LLM vs non-LLM vs integrations)

### Proposed `MoonMind.ManifestIngest` structure

1. **Activity** `artifact.read(manifest_artifact_id)`
2. **Activity** `manifest.parse(bytes)`
3. **Activity** `manifest.validate(parsed)`
4. **Activity** `manifest.compile_to_plan(parsed)` → returns a Plan (graph)

Then either:

**Option A: Execute plan inside the same workflow**

* schedule activities for nodes as dependencies resolve
* enforce concurrency limits
* aggregate results into an output artifact

**Option B (recommended for visibility): Spawn child workflow executions**

* for each top-level node or each declared “run” in the manifest:

  * start child `MoonMind.Run` workflow execution with inputs/plan references
* wait for completion
* aggregate results

Option B gives you:

* separate Workflow Executions per run (clean dashboard visibility)
* isolation of failures and retries

### Failure policies

Manifest ingestion should make failure policy explicit in workflow logic:

* `fail_fast`
* `continue_and_report`
* `best_effort`

These are parameters to the workflow (small JSON), not a new abstraction.

## 10) Skills and Plans: how they map to Activities

### Skill registry responsibilities

Each skill defines:

* `name`
* input schema
* output schema
* executor mapping (which Activity Type(s) implement it)
* default retry policy and timeout
* required capability class (LLM, sandbox, integration)

### Plan format (high level)

A Plan is a sequence or graph of “skill invocation” nodes:

* `skill_name`
* `inputs` (small JSON and/or artifact refs)
* `executor_hint` (optional)
* dependencies (for graphs)

### Execution in `MoonMind.Run`

1. Acquire a Plan:

   * If `plan_artifact_id` provided → load it
   * Else optionally call `plan.generate` as an Activity (planning is “just a skill”)
2. For each node:

   * Resolve to Activity Type + Task Queue
   * Execute Activity with retry/timeout policy
3. Persist outputs as artifacts and record summary metadata

This makes “planning” a capability, not a baked-in “spec system.”

## 11) External integrations and monitoring (Jules)

Temporal is used for long-lived monitoring without an external scheduler.

### Preferred integration patterns

#### A) Callback-first (asynchronous completion)

* Activity starts external work and returns an external job id.
* External system calls your webhook.
* API signals the workflow execution with the completion payload.
* Workflow continues.

#### B) Timer-based polling loop (fallback / if callbacks are unavailable)

* Workflow sets a timer.
* On timer fire, schedules `jules.get_status` activity.
* Uses backoff and stop conditions.
* Continues when complete.

Both patterns live entirely inside Temporal workflow logic (Timers + Activities + Signals/Updates).

## 12) Dashboard: list/paginate/count workflow executions from Visibility

### Source of truth

The dashboard lists **Workflow Executions** directly from Temporal Visibility.

### Pagination

* Use Temporal’s native page token returned by list operations.
* No cross-source merge.
* Page size is an API parameter (default 50).

### Totals

* Use Temporal’s count capability for the same filter set (or compute via Visibility mechanisms appropriate to your cluster configuration).
* If exact totals are expensive, provide:

  * exact totals for narrow filters
  * “unknown / estimated” for broad queries (configurable)
    But the architectural intent is “Temporal provides the truth.”

### Display fields

Use:

* **Search Attributes** for filtering
* **Memo** for lightweight display data (title, short summary, runtime label)
* **Artifacts** for large details (full instructions, logs, outputs)

Recommended Search Attributes:

* `OwnerId`
* `WorkflowType` (already known)
* `Status` (domain status you maintain inside workflow via attributes)
* `RuntimeLabel` (if you want to filter by “codex/gemini/etc.”)
* `UpdatedAt`

## 13) Security and isolation model

Segment worker fleets by risk and secrets:

* **LLM workers**: network egress to model providers, limited filesystem, limited repo access unless needed.
* **Sandbox workers**: run commands/tests; strongest isolation (containers, seccomp, limited egress).
* **Integration workers**: GitHub/Jules tokens, webhooks; restricted to integration endpoints.
* **Artifact workers**: access to object store, minimal other privileges.

Routing is via Task Queues, not by inventing new domain “kinds.”

## 14) Reliability: retries, idempotency, and history control

### Retries

* Use Temporal Activity retry policies as the default reliability mechanism.
* Keep workflow code simple and deterministic; push failure-prone work into activities.

### Idempotency

* Activity implementations must be idempotent where possible:

  * artifact writes use content addressing or “put-if-absent”
  * external job starts store external job id and reuse on retry

### History size control

* Keep payloads out of history (use artifacts).
* Use **Continue-As-New** for:

  * long polling loops
  * very large manifests
  * very long-running runs with many steps

## 15) Deployment blueprint

### Minimal set of services

* Temporal cluster
* MoonMind API
* Artifact store (S3/GCS/MinIO)
* Worker deployments:

  * workflow workers
  * activity workers (llm/sandbox/integrations/artifacts)

### Scaling

* scale activity workers by task queue backlog and throughput needs
* keep workflow workers modest (they orchestrate; they shouldn’t do heavy work)

## 16) Concrete API surface (example)

All naming is product-level; underlying behavior is Temporal-native.

* `POST /executions`

  * starts a Workflow Execution (Workflow Type = `MoonMind.Run` or `MoonMind.ManifestIngest`)
* `POST /executions/{workflow_id}/update`

  * Temporal Update (`UpdateInputs`)
* `POST /executions/{workflow_id}/signal`

  * for async events (approval, webhook completion, etc.)
* `POST /executions/{workflow_id}/cancel`

  * cancel workflow execution
* `GET /executions`

  * list workflow executions (Visibility) with page token + filters
* `GET /executions/{workflow_id}`

  * describe execution + memo + links to artifacts

## 17) What gets deleted by design (Celery-free, queue-free product semantics)

* No Celery workers, beat, or result backends.
* No custom “Agent Queue” leasing/heartbeat mechanisms.
* No product-level “queue” semantics.
* No “task/job/work item” nouns in runtime code.
* No “spec” variables or spec-kit coupling in execution paths.

## 18) Open decisions (Temporal-native choices you should lock early)

1. **Visibility backend level**

   * what filtering and count guarantees you want
2. **Update semantics**

   * apply immediately vs apply at stable points vs always Continue-As-New
3. **Manifest execution strategy**

   * in-workflow orchestration vs spawning child executions (recommended)
4. **Priority routing**

   * do you need lane-based task queues now, or later?

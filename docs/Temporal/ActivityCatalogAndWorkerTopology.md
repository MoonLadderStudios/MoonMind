# Activity Catalog and Worker Topology

Status: **Draft**
Last updated: **2026-03-04**
Scope: Defines **Activity Types**, **worker fleets**, **Task Queue routing**, and the operational rules for executing MoonMind’s Skills and integrations on Temporal.

---

## 1) Purpose

MoonMind uses Temporal’s abstractions directly:

* **Workflow Executions** orchestrate.
* **Activities** perform all side-effecting work (LLM calls, sandbox execution, artifact I/O, integrations).
* **Task Queues** are treated strictly as **routing plumbing** for worker fleets. They are **not** a product-level “queue,” and MoonMind makes no user-facing promises about FIFO ordering.

This document standardizes:

* the **Activity catalog** (names, responsibilities, IO patterns)
* the **worker topology** (fleets, isolation boundaries, secrets)
* routing rules (which Activity runs where)
* reliability (timeouts, retries, heartbeats, idempotency)
* observability expectations

---

## 2) Goals and non-goals

### Goals

1. Provide a **stable Activity Type taxonomy** that supports:

   * Skill execution (LLM and non-LLM)
   * Artifact read/write
   * External integrations (e.g., Jules, GitHub)
   * Planning-as-skill (Plan generation is an Activity)
2. Define worker fleets with **clear security and resource boundaries**.
3. Ensure Activities are:

   * retryable and safe (idempotent where possible)
   * cancelable and observable (heartbeats/logging)
4. Keep Task Queue usage **minimal**, using them for routing only.

### Non-goals

* Designing Workflow Types or lifecycle semantics (covered elsewhere).
* Modeling a product-level “queue order” or “priority queue.” (If priority lanes exist, they are throughput controls, not ordering guarantees.)
* Embedding “spec” concepts into runtime naming or execution code.

---

## 3) Principles

1. **Determinism boundary**
   Workflow code must be deterministic. Any non-determinism (LLMs, network calls, filesystem, clocks, random, external state) must be placed in Activities.

2. **Stable public surface**
   Activity Type names are long-lived contracts. Prefer adding new Activity Types over changing semantics in-place.

3. **Routing by capability, not by domain nouns**
   Workers are split by what they can safely do (secrets, sandboxing, egress), not by legacy concepts.

4. **Payload discipline**
   Large inputs/outputs live in the **Artifact Store**. Workflow and Activity payloads should pass **references**, not blobs.

5. **Idempotency by design**
   Activities must tolerate retries. Either be naturally idempotent or implement idempotency keys.

---

## 4) Task Queues (routing only)

Temporal requires Task Queues for Workers to poll. MoonMind uses Task Queues as *internal routing labels*.

### 4.1 Minimal Task Queue set

**Workflow task queue**

* `mm.workflow`

**Activity task queues**

* `mm.activity.artifacts`
* `mm.activity.llm` *(optionally subdivided by provider)*
* `mm.activity.sandbox`
* `mm.activity.integrations`

Optional subdivisions (only if needed for operational clarity / isolation):

* `mm.activity.llm.codex`
* `mm.activity.llm.gemini`
* `mm.activity.llm.claude`
* `mm.activity.integrations.jules`
* `mm.activity.integrations.github`

> Rule of thumb: Start minimal. Subdivide only when you need isolation, scaling, or different secrets/egress.

---

## 5) Activity catalog

### 5.1 Naming conventions

Activity Type names use dotted namespaces:

* `artifact.*` for artifact store operations
* `plan.*` for plan creation/validation (planning is a Skill that returns a Plan)
* `skill.<skill_name>.execute` for executing a specific skill
* `sandbox.*` for OS/process execution and repo operations
* `integration.<provider>.*` for external systems
* `system.*` for housekeeping / reconciliation (rare; prefer Schedules + workflows)

**Examples**

* `artifact.read`
* `artifact.write`
* `plan.generate`
* `skill.edit_file.execute`
* `sandbox.run_command`
* `integration.jules.start`
* `integration.jules.status`
* `integration.jules.fetch_result`

### 5.2 Shared request/response envelope (recommended)

Every Activity should accept a common envelope to standardize idempotency and tracing:

**Request common fields**

* `workflow_id`
* `run_id`
* `activity_id` (or a logical step id)
* `attempt` (Temporal attempt number)
* `correlation_id` (MoonMind-provided; stable across workflow updates)
* `idempotency_key` (required for side-effecting calls)
* `input_refs[]` (artifact references)
* `parameters` (small JSON)

**Response common fields**

* `output_refs[]` (artifact references)
* `summary` (small JSON)
* `metrics` (optional small JSON, e.g., token counts)
* `diagnostics_ref` (artifact reference for logs if large)

> The exact schema is language-specific, but the shape should be consistent.

---

## 6) Activity families (canonical set)

### 6.1 Artifact activities (`artifact.*`)

**Purpose:** Read/write immutable or versioned blobs used by workflows and skills.

Core Activities:

* `artifact.read(ref) -> bytes|stream_ref`
* `artifact.write(content|stream_ref, metadata) -> ref`
* `artifact.copy(ref, target_namespace) -> ref`
* `artifact.delete(ref)` *(rare; prefer retention policies)*

Worker queue: `mm.activity.artifacts`

Key constraints:

* Must be **idempotent** for retries (e.g., content-addressing or put-if-absent).
* Must support large payloads via streaming or pre-signed URLs (avoid workflow payload bloat).

---

### 6.2 Plan activities (`plan.*`)

**Purpose:** Generate and validate Plans. “Planning” is a capability, not a system layer.

Core Activities:

* `plan.generate(inputs_ref, parameters) -> plan_ref`
* `plan.validate(plan_ref) -> validation_report_ref`

Worker queue: typically `mm.activity.llm` for LLM planners, but non-LLM planners may run on `mm.activity.sandbox` or `mm.activity.integrations` depending on implementation.

Key constraints:

* Treat plan generation as nondeterministic: always an Activity.
* Output should be an artifact reference (`plan_ref`) unless extremely small.

---

### 6.3 Skill execution activities (`skill.<name>.execute`)

**Purpose:** Execute a specific skill; this is the core unit of “doing work” in MoonMind.

Two execution patterns:

**Pattern A (recommended): one Activity Type per skill**

* `skill.<skill_name>.execute(request) -> response`

Pros:

* Clear ownership and isolation
* Easy routing and metrics per skill
* Stable contracts per capability

Cons:

* Large catalog

**Pattern B: generic executor**

* `skill.execute({skill_name, inputs...})`

Pros:

* Smaller catalog

Cons:

* Less explicit observability, harder isolation boundaries

**Recommendation:** Use Pattern A for skills with distinct capabilities/secrets/sandbox needs; use a generic fallback only for small internal skills.

Worker queue: depends on skill capability mapping (see routing rules below).

Key constraints:

* Must accept `idempotency_key` and return references for large output.
* Must be cancel-aware (check cancellation frequently for long work).

---

### 6.4 Sandbox activities (`sandbox.*`)

**Purpose:** Execute untrusted or resource-heavy operations (commands, tests, repo actions).

Core Activities:

* `sandbox.checkout_repo(repo_ref) -> workspace_ref`
* `sandbox.run_command(workspace_ref, cmd, env) -> result_ref`
* `sandbox.apply_patch(workspace_ref, patch_ref) -> workspace_ref`
* `sandbox.run_tests(workspace_ref, parameters) -> report_ref`

Worker queue: `mm.activity.sandbox`

Isolation requirements:

* Strong container isolation (seccomp/apparmor), limited FS, controlled egress
* Explicit resource limits (CPU/mem/time)
* No access to unrelated secrets by default

Reliability:

* Use **heartbeats** for long-running commands with progress metadata.
* Activities must handle retries carefully to avoid duplicating side effects:

  * workspace naming must be idempotent or keyed by idempotency key

---

### 6.5 Integration activities (`integration.<provider>.*`)

**Purpose:** External API calls, long-lived external work, event bridging.

Example: Jules

* `integration.jules.start(inputs_ref, parameters) -> {external_id, tracking_ref}`
* `integration.jules.status(external_id) -> status`
* `integration.jules.fetch_result(external_id) -> output_refs[]`

Worker queue: `mm.activity.integrations` (or `mm.activity.integrations.jules`)

Patterns supported:

* **Callback-first:** external event triggers API → Signal/Update workflow
* **Polling fallback:** workflow uses Timers + `integration.*.status` Activities

Security:

* Integration workers hold provider secrets; sandbox workers do not.
* Egress allowlists per provider.

---

## 7) Routing rules (how workflows choose task queues)

Workflows select Activity routing via **Activity Options**.

### 7.1 Capability mapping

Skill Registry (or equivalent config) must map each Skill to:

* Activity Type (`skill.<name>.execute`)
* Required capability class:

  * `llm`
  * `sandbox`
  * `integration:<provider>`
  * `artifacts`
* Default Task Queue for that capability class
* Timeouts/retries defaults

### 7.2 LLM vs non-LLM selection

Selection is made **per Activity invocation**, not per Workflow Type.

Examples:

* `plan.generate` may route to `mm.activity.llm.codex` or `mm.activity.llm.gemini` depending on parameters
* `sandbox.run_tests` always routes to `mm.activity.sandbox`
* `integration.jules.start` routes to `mm.activity.integrations.jules`

### 7.3 Optional “priority lanes” (throughput control only)

If needed, add suffix queues:

* `mm.activity.sandbox.high|normal|low`
* `mm.activity.llm.codex.high|normal|low`

Workflows route based on a small policy function (not a “queue order” promise):

* high-lane gets more worker capacity, not guaranteed strict priority ordering.

---

## 8) Worker topology

### 8.1 Worker roles

1. **Workflow Workers**

   * Poll: `mm.workflow`
   * Execute: Workflow code only (no heavy lifting)

2. **Activity Workers**

   * Poll: one or more activity task queues
   * Execute: side effects (LLM, sandbox, integrations, artifacts)

### 8.2 Fleet segmentation (recommended)

**Fleet: Workflow**

* Queues: `mm.workflow`
* Privileges: minimal (Temporal credentials only)
* Scaling: modest CPU; scale by workflow task backlog

**Fleet: Artifacts**

* Queues: `mm.activity.artifacts`
* Privileges: access to object store; no repo tokens; limited egress
* Scaling: IO-bound; autoscale on throughput/latency

**Fleet: LLM**

* Queues: `mm.activity.llm` and/or provider-specific subqueues
* Privileges: LLM API keys; no sandbox execution
* Scaling: rate-limited by provider quotas; autoscale with QPS control

**Fleet: Sandbox**

* Queues: `mm.activity.sandbox`
* Privileges: minimal secrets; ability to run commands in isolated containers
* Scaling: CPU/mem heavy; strict concurrency caps; queue depth monitoring

**Fleet: Integrations**

* Queues: `mm.activity.integrations` (and optionally per provider)
* Privileges: provider tokens; webhook verification secrets
* Scaling: depends on provider; protect with rate limiting and circuit breakers

### 8.3 Multi-language support (optional)

Temporal supports workers in multiple languages. You may:

* keep workflows in one language for consistency
* implement some activity fleets in the best-suited language (e.g., node for certain integrations)

This is an implementation choice; the Activity Type contract remains stable.

---

## 9) Reliability contracts

### 9.1 Timeouts (defaults by activity family)

You should standardize timeouts by family:

* `artifact.*`: short start-to-close (e.g., 30s–2m), retries ok
* `plan.generate` (LLM): moderate (e.g., 2–10m), retries with backoff
* `skill.*.execute`: depends on skill; default moderate with overrides
* `sandbox.*`: longer (e.g., 10–60m), **heartbeat required**
* `integration.*`: short per API call; long-running external work should be modeled as:

  * start activity + polling activities + timers, or
  * async completion

### 9.2 Retry policies

* Use exponential backoff with max interval caps for integrations.
* For sandbox commands, retries should be carefully bounded (avoid “rerun destructive command” surprises).
* For LLM calls, retries must account for idempotency (see below) and cost.

### 9.3 Heartbeats

Required for:

* `sandbox.run_command`
* long-running integration polling loops (if modeled as a long activity—which is generally *not* recommended)

Heartbeats should include:

* progress phase
* last log offset / artifact ref
* estimated remaining time if possible

### 9.4 Idempotency

Rules:

* All side-effecting activities must accept `idempotency_key`.
* Artifact writes should be content-addressed or use “put-if-absent.”
* External starts (e.g., `integration.jules.start`) must store and reuse `external_id` for the same key.

---

## 10) Security model

1. **Least privilege per fleet**

   * Sandbox fleet never holds provider API keys.
   * Integration fleet never runs arbitrary shell commands.
2. **Network controls**

   * LLM fleet can reach model endpoints; sandbox fleet has restricted egress.
3. **Secret distribution**

   * Use a secret manager; short-lived tokens where possible.
4. **Data handling**

   * Large content stored as artifacts; workflows pass references.
   * Redaction policy for logs and workflow memos.

---

## 11) Observability requirements

### 11.1 Logging

Every activity log line must include:

* `workflow_id`, `run_id`
* `activity_type`, `activity_id`, `attempt`
* `correlation_id`
* `idempotency_key` (or a hash of it)

Large logs must be written as artifacts and referenced by ID.

### 11.2 Metrics

Per fleet:

* task queue lag / backlog
* activity execution latency distributions
* retry counts and failure reasons
* sandbox resource usage (CPU/mem), command duration
* LLM token usage/cost metrics (where available)

### 11.3 Tracing

If using OpenTelemetry:

* propagate correlation IDs through activities
* annotate spans with workflow/run identifiers

---

## 12) Testing strategy

1. **Activity contract tests**

   * validate schemas
   * idempotency behavior under retry
2. **Worker fleet integration tests**

   * can the right fleet execute the right activity type?
3. **Load tests**

   * sandbox concurrency and isolation
   * LLM rate limiting correctness
4. **Failure injection**

   * external provider timeouts
   * artifact store outages
   * worker restarts mid-activity (heartbeat behavior)

---

## 13) Implementation sequence (for this subsystem)

1. Implement baseline worker fleets:

   * workflow worker (`mm.workflow`)
   * artifacts worker (`mm.activity.artifacts`)
2. Define the first handful of Activities:

   * `artifact.read`, `artifact.write`
   * `skill.<one_safe_skill>.execute` (non-LLM, non-sandbox)
3. Add LLM fleet and a single planning activity:

   * `plan.generate` routed to `mm.activity.llm`
4. Add sandbox fleet:

   * `sandbox.run_command` with heartbeats and strict isolation
5. Add integrations fleet:

   * `integration.jules.start/status/fetch_result` (or callback-first contract)

---

## 14) Open questions

* Do we need provider-specific LLM task queues (`mm.activity.llm.codex` etc.) immediately, or can we start with one `mm.activity.llm` and route internally?
* Do we want “priority lanes” at all, or should we rely solely on concurrency limits + rate limiting?
* What is the minimal set of Search Attributes/Memo fields Activities should upsert (if any), vs leaving all visibility updates to workflows?

---

## Appendix A: Suggested initial Activity Type list (MVP)

**Artifacts**

* `artifact.read`
* `artifact.write`

**Planning**

* `plan.generate`

**Skills**

* `skill.<name>.execute` (start with 1–3 core skills)

**Sandbox**

* `sandbox.run_command` *(phase 4)*

**Integrations**

* `integration.jules.start`
* `integration.jules.status`
* `integration.jules.fetch_result` *(phase 5)*

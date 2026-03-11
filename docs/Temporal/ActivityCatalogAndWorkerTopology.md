# Activity Catalog and Worker Topology

Status: **Draft**
Last updated: **2026-03-05**
Scope: Defines **Activity Types**, **worker fleets**, **Task Queue routing**, and the operational rules for executing MoonMind's Skills and integrations on Temporal.

---

## 1) Purpose

MoonMind uses Temporal's abstractions directly:

* **Workflow Executions** orchestrate.
* **Activities** perform all side-effecting work (LLM calls, sandbox execution, artifact I/O, integrations).
* **Task Queues** are treated strictly as **routing plumbing** for worker fleets. They are **not** a product-level "queue," and MoonMind makes no user-facing promises about FIFO ordering.

This document standardizes:

* the **Activity catalog** (names, responsibilities, IO patterns)
* the **worker topology** (fleets, isolation boundaries, secrets)
* routing rules (which Activity runs where)
* reliability (timeouts, retries, heartbeats, idempotency)
* observability expectations

This doc covers the **Temporal-managed worker model**. Current queue workers and orchestrator workers may continue to exist during migration, but they should converge toward these activity boundaries rather than invent parallel long-term runtime abstractions.

---

## 2) Goals and non-goals

### Goals

1. Provide a **stable Activity Type taxonomy** that supports:

   * Skill execution (LLM and non-LLM)
   * Artifact lifecycle (create, upload, link, read, pin, sweep)
   * External integrations (e.g., Jules, GitHub)
   * Planning-as-skill (Plan generation is an Activity)
2. Define worker fleets with **clear security and resource boundaries**.
3. Ensure Activities are:

   * retryable and safe (idempotent where possible)
   * cancelable and observable (heartbeats/logging)
4. Keep Task Queue usage **minimal**, using them for routing only.

### Non-goals

* Designing Workflow Types or lifecycle semantics (covered in `WorkflowTypeCatalogAndLifecycle.md`).
* Modeling a product-level "queue order" or "priority queue." (If priority lanes exist, they are throughput controls, not ordering guarantees.)
* Embedding "spec" concepts into runtime naming or execution code.

---

## 3) Principles

1. **Determinism boundary**
   Workflow code must be deterministic. Any non-determinism (LLMs, network calls, filesystem, clocks, random, external state) must be placed in Activities.

2. **Stable public surface**
   Activity Type names are long-lived contracts. Prefer adding new Activity Types over changing semantics in-place. Implementations behind those names are designed for replaceability (Constitution V — Bittersweet Lesson).

3. **Routing by capability, not by domain nouns**
   Workers are split by what they can safely do (secrets, sandboxing, egress), not by legacy concepts.

4. **Payload discipline**
   Large inputs/outputs live in the **Artifact Store**. Workflow and Activity payloads should pass **references** (`ArtifactRef`), not blobs.

5. **Idempotency by design**
   Activities must tolerate retries. Either be naturally idempotent or implement idempotency keys (Constitution VIII — Self-Healing).

6. **Continue-As-New awareness**
   Workflows may Continue-As-New after N skill invocations or long wait thresholds (see `WorkflowTypeCatalogAndLifecycle.md`). Activities must not assume a stable `run_id` across a workflow's full lifetime. The `correlation_id` is the stable identifier that survives Continue-As-New. Artifact links use both `workflow_id` and `run_id` via `ExecutionRef`.

7. **Search Attribute ownership**
   Activities do **not** upsert Search Attributes or Memo fields directly. They return results to the calling workflow, which owns all visibility updates (`mm_state`, `mm_owner_id`, `mm_updated_at`, etc.). This preserves the determinism boundary.

### 3.1 Constitution alignment

This subsystem's design maps to the following constitutional principles:

| Constitution Principle | Expression in this subsystem |
|---|---|
| **I — One-Click Deployment** | All worker fleets are provisionable as Docker Compose services with only documented prerequisites and minimal secrets. |
| **II — Avoid Vendor Lock-In** | Artifact storage uses the `TemporalArtifactStore` adapter interface with MinIO/S3-compatible backends by default and explicit override paths for alternates. Integration activities sit behind provider adapter interfaces. |
| **III — Own Your Data** | Large execution inputs/outputs remain portable, inspectable artifacts under MoonMind-managed storage rather than provider-specific opaque payloads. |
| **IV — Skills Are First-Class** | Skill activities must declare inputs, outputs, external dependencies, and failure modes via `ToolDefinition` and `SkillPolicies`. |
| **V — Bittersweet Lesson** | Activity Type names are the stable contracts; implementations are replaceable. Design for deletion. |
| **VI — Powerful Runtime Configurability** | Queue routing, backend selection, retention policy, and worker capability bindings are configuration-driven and must remain observable in run metadata/logging. |
| **VII — Modular Architecture** | Fleet segmentation enforces clear module boundaries. Core orchestration depends on stable Activity Type interfaces, not vendor specifics. |
| **VIII — Self-Healing** | All side effects are retry-safe. Idempotency keys, heartbeats, and explicit terminal states ensure resumability. |
| **IX — Facilitate Continuous Improvement** | Activities emit structured summaries, diagnostics references, and retry/failure signals so operators can see what happened and feed recurring issues into follow-up improvements. |
| **X — Spec-Driven Development** | New activity families and routing changes must stay traceable to spec/contracts/tests so the catalog does not drift from implementation reality. |

---

## 4) Task Queues (routing only)

Temporal requires Task Queues for Workers to poll. MoonMind uses Task Queues as *internal routing labels*.

### 4.1 Minimal Task Queue set

**Workflow task queue**

* `mm.workflow`

**Activity task queues**

* `mm.activity.artifacts`
* `mm.activity.llm`
* `mm.activity.sandbox`
* `mm.activity.integrations`

> **Decided:** Start with a single `mm.activity.llm` queue. Provider-specific subqueues (`mm.activity.llm.codex`, etc.) are deferred until operational isolation or independent scaling demands them. Internal routing by provider is handled within the LLM activity worker, not via separate task queues.

> Rule of thumb: Subdivide only when you need isolation, scaling, or different secrets/egress.

---

## 5) Activity catalog

### 5.1 Naming conventions

Activity Type names use dotted namespaces:

* `artifact.*` for artifact store operations
* `plan.*` for plan creation/validation (planning is a Skill that returns a Plan)
* `mm.tool.execute` for the default registry-dispatched skill executor
* `sandbox.*` for OS/process execution and repo operations
* `integration.<provider>.*` for external systems
* `system.*` for housekeeping / reconciliation (rare; prefer Schedules + workflows)

Curated exceptions may bind a skill directly to an explicit Activity Type when the boundary needs stronger isolation, specialized credentials, or clearer routing. This follows the hybrid dispatcher model in `docs/Skills/SkillAndPlanContracts.md`.

**Examples**

* `artifact.create`
* `artifact.write_complete`
* `artifact.read`
* `artifact.link`
* `plan.generate`
* `plan.validate`
* `mm.tool.execute`
* `sandbox.run_command`
* `integration.jules.start`
* `integration.jules.status`
* `integration.jules.fetch_result`

### 5.2 Shared request/response envelope

Activity contracts should stay small and business-focused. Use explicit request fields for business inputs, artifact references, and idempotency; derive Temporal execution metadata from activity context/logging rather than duplicating it into every payload.

**Common business request fields**

* `correlation_id` (MoonMind-provided; stable across Continue-As-New boundaries)
* `idempotency_key` (required for side-effecting calls)
* `input_refs[]` (artifact references as `ArtifactRef`)
* `parameters` (small JSON)

**Response common fields**

* `output_refs[]` (artifact references as `ArtifactRef`)
* `summary` (small JSON)
* `metrics` (optional small JSON, e.g., token counts)
* `diagnostics_ref` (artifact reference for logs if large)

**Context-derived metadata**

* `workflow_id`
* `run_id`
* `activity_id`
* `attempt`

These values should come from the Temporal runtime/activity context for logging, metrics, and tracing. Do not require them as duplicated business payload fields unless a specific external contract truly needs them.

**Canonical contract references:**

* `ArtifactRef` — see `moonmind/workflows/temporal/artifacts.py` (`artifact_ref_v`, `artifact_id`, `sha256`, `size_bytes`, `content_type`, `encryption`)
* `ExecutionRef` — see `moonmind/workflows/temporal/artifacts.py` (`namespace`, `workflow_id`, `run_id`, `link_type`, `label`, `created_by_activity_type`, `created_by_worker`)
* `StageExecutionDecision` / `StageExecutionOutcome` — see `moonmind/workflows/skills/contracts.py`
* `PlanDefinition`, `ToolDefinition`, `SkillPolicies` — see `moonmind/workflows/skills/skill_plan_contracts.py`

---

## 6) Activity families (canonical set)

### 6.1 Artifact activities (`artifact.*`)

**Purpose:** Manage the full artifact lifecycle — create, upload, link to executions, read, list, preview, pin, and sweep expired artifacts.

The artifact system uses a two-phase upload model: `create` registers metadata and returns an upload descriptor for API/client flows, then `write_complete` finalizes the upload with integrity verification. Activity-side worker flows may also stream bytes directly through the artifact service facade. Artifacts are identified by `art_<ULID>` IDs and referenced via `ArtifactRef` values. Storage is backed by the `TemporalArtifactStore` adapter interface with **MinIO/S3-compatible storage as the default local/dev and Docker Compose backend**; `local_fs` remains an explicit fallback only when selected by configuration.

Core Activities:

| Activity | Description |
|---|---|
| `artifact.create(execution_ref, metadata) -> ArtifactRef + upload_descriptor` | Register artifact metadata, derive retention class from link type, return upload details for API/client flows or a writable handle for worker-side flows |
| `artifact.write_complete(artifact_id, upload_proof_or_payload_metadata) -> ArtifactRef` | Finalize uploaded or streamed content, verify integrity, transition status to COMPLETE |
| `artifact.read(artifact_ref) -> bytes or readable stream handle` | Activity-side read of artifact content using internal credentials; presigned download URLs are an API-layer concern, not workflow state |
| `artifact.list_for_execution(namespace, workflow_id, run_id, link_type?, latest_only?) -> ArtifactRef[]` | List artifacts linked to one execution, with deterministic latest-output selection when requested |
| `artifact.compute_preview(artifact_ref, policy?) -> ArtifactRef` | Produce or retrieve a redacted preview artifact for restricted UI-safe reads |
| `artifact.link(artifact_id, execution_ref) -> link_id` | Bind an existing artifact to a workflow execution via `ExecutionRef` and link type |
| `artifact.pin(artifact_id, reason) -> pin_id` | Override retention to prevent lifecycle deletion |
| `artifact.unpin(artifact_id)` | Remove pin; artifact resumes normal retention lifecycle |
| `artifact.lifecycle_sweep() -> LifecycleSweepSummary` | Expire artifacts past retention, soft-delete, then hard-delete blobs older than cutoff (scheduled via Temporal Schedule, not per-request) |

**Retention classes** (derived from link type):

* `ephemeral` — 7 days (debug traces, intermediate outputs)
* `standard` — 30 days (normal workflow outputs)
* `long` — 180 days (plans, important results)
* `pinned` — no automatic deletion

**Link types** (connect artifacts to executions):

* `input.instructions`, `input.manifest`, `input.plan`
* `output.primary`, `output.patch`, `output.logs`, `output.summary`
* `debug.trace`

**Redaction levels:**

* `none` — full access
* `preview_only` — a truncated (16 KB) redacted preview artifact is auto-generated
* `restricted` — only owner or service principals get raw access

Worker queue: `mm.activity.artifacts`

Key constraints:

* Must be **idempotent** for retries (content-addressing via sha256; `write_complete` is safe to retry).
* Large payloads use presigned URLs or multipart upload on API/client paths and streamed reads/writes on worker paths. Never pass blob content or presigned URLs through workflow history.
* Artifacts flow through the `TemporalArtifactStore` adapter interface — vendor-neutral by design (Constitution II).
* Default local one-click behavior keeps MinIO private on the internal Docker network; `AUTH_PROVIDER=disabled` is an API auth-mode choice, not a public-bucket/storage exposure model.

---

### 6.2 Plan activities (`plan.*`)

**Purpose:** Generate and validate Plans. "Planning" is a capability, not a system layer.

Core Activities:

* `plan.generate(inputs_ref, parameters) -> plan_ref`
* `plan.validate(plan_ref, registry_snapshot_ref) -> validated_plan_ref | SkillFailure`

The output of `plan.generate` is a `PlanDefinition` artifact (see `moonmind/workflows/skills/skill_plan_contracts.py`): a DAG of `Step` nodes connected by `PlanEdge` dependencies. Each node references a `ToolDefinition` with declared inputs, outputs, and `SkillPolicies` (retries, timeouts, failure modes).

Worker queue: typically `mm.activity.llm` for LLM planners, but non-LLM planners may run on `mm.activity.sandbox` or `mm.activity.integrations` depending on implementation.

Key constraints:

* Treat plan generation as nondeterministic: always an Activity.
* Deep plan validation should complete before execution begins; workflows may do lightweight structural checks, but `plan.validate` is the authoritative pre-execution gate.
* Output must be stored as an artifact reference (`plan_ref`), not inlined in workflow history.

---

### 6.3 Tool execution activities (`mm.tool.execute` + curated explicit types)

**Purpose:** Execute a specific skill; this is the core unit of "doing work" in MoonMind.

Skills must declare inputs, outputs, external dependencies, and failure modes (Constitution IV). The Skill Registry maps each skill to its Activity Type, capability class, and default policies.

MoonMind uses a **hybrid activity binding model**:

**Default path: registry-dispatched executor**

* `mm.tool.execute(invocation_payload) -> SkillResult`

Pros:

* Low-ceremony skill addition
* Keeps the catalog small
* Makes skill registration the source of truth for routing/binding

Cons:

* Per-skill observability relies on registry metadata and emitted result fields rather than unique activity names

**Curated explicit activity types**

Examples:

* `artifact.*`
* `sandbox.*`
* `integration.jules.*`
* other explicit types declared by the Skill Registry when stronger isolation, least-privilege, or specialized worker code is required

**Recommendation:** Default to `mm.tool.execute`; bind a skill directly to an explicit Activity Type only when the registry declares a concrete operational reason.

Worker queue: depends on skill capability mapping (see routing rules below).

Key constraints:

* Must accept `idempotency_key` and return references for large output.
* Must be cancel-aware (check cancellation frequently for long work).
* Must declare failure modes via `SkillPolicies` (`FAIL_FAST` or `CONTINUE`).
* The workflow/interpreter does not guess bindings. Activity Type selection comes from the pinned registry snapshot.

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

**Purpose:** External API calls, long-lived external work, event bridging. Each provider's integration sits behind an adapter interface so that alternative providers can be substituted without changing Activity Type contracts (Constitution II).

Example: Jules

* `integration.jules.start(inputs_ref, parameters) -> {external_id, tracking_ref}`
* `integration.jules.status(external_id) -> status`
* `integration.jules.fetch_result(external_id) -> output_refs[]`

Worker queue: `mm.activity.integrations` (or `mm.activity.integrations.jules`)

Patterns supported:

* **Callback-first (preferred):** external event triggers API → Signal/Update workflow
* **Polling fallback:** workflow uses Timers + `integration.*.status` Activities

Security:

* Integration workers hold provider secrets; sandbox workers do not.
* Egress allowlists per provider.

---

## 7) Routing rules (how workflows choose task queues)

Workflows select Activity routing via **Activity Options**.

### 7.1 Capability mapping

Skill Registry (or equivalent config) must map each Skill to:

* Activity Type (`mm.tool.execute` by default, or a curated explicit type)
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

* `plan.generate` routes to `mm.activity.llm`; provider selection (Codex, Gemini, Claude) is handled within the activity worker based on parameters
* `sandbox.run_tests` always routes to `mm.activity.sandbox`
* `integration.jules.start` routes to `mm.activity.integrations`

### 7.3 Priority lanes — deferred

**Decided:** Priority lanes are deferred for v1. Throughput control relies on concurrency limits and rate limiting per fleet. If priority lanes are introduced later, they will use suffix queues (e.g., `mm.activity.sandbox.high|normal|low`) with more worker capacity on higher lanes — never a strict ordering guarantee.

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

All fleets are provisioned as Docker Compose services (Constitution I — One-Click Deployment).

**Fleet: Workflow**

* Queues: `mm.workflow`
* Privileges: minimal (Temporal credentials only)
* Scaling: modest CPU; scale by workflow task backlog

**Fleet: Artifacts**

* Queues: `mm.activity.artifacts`
* Privileges: access to MinIO/S3-compatible object storage; no repo tokens; limited egress
* Scaling: IO-bound; autoscale on throughput/latency

**Fleet: LLM**

* Queues: `mm.activity.llm`
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

### 8.3 Multi-language support — deferred

Multi-language workers are a valid Temporal capability but are deferred. The current codebase is Python-only. If a future activity fleet is best implemented in another language, the Activity Type contract remains stable — only the worker implementation changes. This is an implementation choice to be revisited if a concrete need arises.

---

## 9) Reliability contracts

### 9.1 Timeouts (defaults by activity family)

You should standardize timeouts by family:

* `artifact.*`: short start-to-close (e.g., 30s–2m), retries ok
* `plan.generate` (LLM): moderate (e.g., 2–10m), retries with backoff
* `skill.*.execute`: depends on skill; default moderate with overrides via `SkillPolicies`
* `sandbox.*`: longer (e.g., 10–60m), **heartbeat required**
* `integration.*`: short per API call; long-running external work should be modeled as:

  * start activity + polling activities + timers, or
  * async completion

### 9.2 Retry policies

* Use exponential backoff with max interval caps for integrations.
* For sandbox commands, retries should be carefully bounded (avoid "rerun destructive command" surprises).
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
* Artifact writes use sha256 content verification and two-phase upload (`create` + `write_complete`) which is naturally retry-safe.
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
   * Secrets never appear in artifact content, workflow history, or logs (Constitution operational constraint).
4. **Data handling**

   * Large content stored as artifacts; workflows pass references.
   * Redaction policy for logs and workflow memos.
   * RESTRICTED artifacts are access-controlled: only owner or service principals get raw content; a redacted preview (16 KB, token/password/secret patterns scrubbed) is auto-generated for UI display.
5. **Default local/dev posture**

   * `AUTH_PROVIDER=disabled` may allow user-facing artifact metadata/presign APIs without end-user auth in the one-click profile.
   * This does **not** make artifact storage public; MinIO/object storage remains on the internal network with service credentials.

---

## 11) Observability requirements

### 11.1 Logging

Every activity log line must include:

* `workflow_id`, `run_id`
* `activity_type`, `activity_id`, `attempt`
* `correlation_id`
* `idempotency_key` (or a hash of it)

Large logs must be written as artifacts (link type `output.logs` or `debug.trace`) and referenced by ID.

Activity completion should also emit a structured summary that lets operators answer "what happened?" without reading raw worker internals.

### 11.2 Metrics

Per fleet:

* task queue lag / backlog
* activity execution latency distributions
* retry counts and failure reasons
* sandbox resource usage (CPU/mem), command duration
* LLM token usage/cost metrics (where available)
* repeated retry/time-out patterns that should feed operator review or follow-up improvement work

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
5. **Traceability gate**

   * activity catalog changes stay aligned with specs/contracts/runtime tests
   * new explicit activity types document migration/compatibility impact before rollout

---

## 13) Implementation sequence (for this subsystem)

1. Implement baseline worker fleets:

   * workflow worker (`mm.workflow`)
   * artifacts worker (`mm.activity.artifacts`)
2. Define the first artifact activities:

   * `artifact.create`, `artifact.write_complete`, `artifact.read`, `artifact.list_for_execution`, `artifact.compute_preview`, `artifact.link` *(artifact service is implemented; activity wrappers are next)*
3. Add LLM fleet and planning activities:

   * `plan.generate` routed to `mm.activity.llm`
   * `plan.validate` as the authoritative deep-validation gate before execution
4. Add default skill dispatch:

   * `mm.tool.execute` backed by the pinned Skill Registry snapshot
5. Add sandbox fleet:

   * `sandbox.run_command` with heartbeats and strict isolation
6. Add integrations fleet:

   * `integration.jules.start/status/fetch_result` (callback-first preferred)
7. Add lifecycle management:

   * `artifact.lifecycle_sweep` scheduled via Temporal Schedule
   * `artifact.pin` / `artifact.unpin` for retention overrides

---

## 14) Decided questions

These were previously open questions; decisions are now recorded:

1. **Provider-specific LLM task queues:** Start with a single `mm.activity.llm` queue. Provider selection is handled internally within the LLM activity worker based on request parameters. Subdivide into per-provider queues only when operational isolation or independent scaling demands it.

2. **Priority lanes:** Deferred for v1. Rely on concurrency limits and rate limiting per fleet. If introduced later, priority lanes will allocate more worker capacity to higher lanes without guaranteeing strict ordering.

3. **Search Attributes from Activities:** Activities do **not** upsert Search Attributes or Memo fields. Workflows own all visibility updates. Activities return results to the calling workflow, which updates `mm_state`, `mm_owner_id`, `mm_updated_at`, etc. This preserves the determinism boundary and keeps visibility logic in one place.

---

## Appendix A: Suggested initial Activity Type list (MVP)

**Artifacts** *(service implemented; activity wrappers next)*

* `artifact.create`
* `artifact.write_complete`
* `artifact.read`
* `artifact.list_for_execution`
* `artifact.compute_preview`
* `artifact.link`

**Planning**

* `plan.generate`
* `plan.validate`

**Skills**

* `mm.tool.execute`

**Sandbox** *(phase 4)*

* `sandbox.run_command`

**Integrations** *(phase 5)*

* `integration.jules.start`
* `integration.jules.status`
* `integration.jules.fetch_result`

**Lifecycle** *(phase 6)*

* `artifact.lifecycle_sweep`
* `artifact.pin`
* `artifact.unpin`

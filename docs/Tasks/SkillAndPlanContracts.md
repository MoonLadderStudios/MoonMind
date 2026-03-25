# Tool and Plan Contracts (Skills as a Tool Subtype)

**Implementation tracking:** [`docs/tmp/remaining-work/Tasks-SkillAndPlanContracts.md`](../tmp/remaining-work/Tasks-SkillAndPlanContracts.md)

MoonMind system design (Temporal-first)

Status: **Implemented** (contracts active, runtime live)
Last updated: 2026-03-20

---

## 1) Purpose

Define what **execution** means in MoonMind using **Temporal’s** core model:

* A **Workflow Execution** orchestrates.
* **Activities** perform side effects (LLM calls, filesystem, network, integrations).
* MoonMind adds only what Temporal does not provide:

  * **Tool** (capability contract)
  * **Plan** (structured sequence/graph of tool invocations)
  * **Artifact** (large inputs/outputs stored outside workflow history)

This document establishes:

* Tool interface: schemas, validation, error model
* Plan format: DAG-first, concurrency, dependency semantics
* Plan production: planning is “just a tool”; plans are artifacts
* Determinism boundaries: orchestration in workflow, execution in activities
* Progress & intermediate outputs conventions
* Deliverables: tool registry spec, plan schema/examples/validation, execution semantics (plan -> activity invocations)

### 1.1 Terminology policy (Temporal era)

Canonical terminology for execution payloads is:

* **task** (top-level user request)
* **step** (plan node)
* **tool** (executable capability — Temporal contract object)

There are two tool subtypes:

* `tool.type = "skill"` — dispatched as a Temporal Activity (`mm.tool.execute` / `mm.skill.execute`). Uses a `ToolDefinition` from the registry snapshot.
* `tool.type = "agent_runtime"` — dispatched as a child `MoonMind.AgentRun` workflow. Uses an `AgentExecutionRequest`.

> **Note:** The term **"Skill"** in `.agents/skills/` directories and `SKILL.md` files
> refers to **agent instruction bundles** — markdown files that AI agents read for
> guidance. These are a separate concept from the Temporal contract `ToolDefinition`
> objects described in this document.

Canonical Python class names:

| Contract | Canonical class | Legacy alias |
|---|---|---|
| Tool definition | `ToolDefinition` | `SkillDefinition` |
| Tool result | `ToolResult` | `SkillResult` |
| Tool failure | `ToolFailure` | `SkillFailure` |
| Plan node | `Step` | `SkillInvocation` |
| Policies | `ToolPolicies` | `SkillPolicies` |

Compatibility rule:

* Legacy `Skill*` aliases are re-exported for backward compatibility
* New code should import canonical `Tool*` names
* Legacy `skill` payload fields are accepted during migration

---

## 2) Design principles

1. **Workflow code orchestrates only.**
   No nondeterministic behavior in workflow code. All external I/O and LLM calls are Activities.

2. **Everything executable is a Tool invocation.**
   “Planning” is a Tool that outputs a Plan.

3. **Plans are data, not code.**
   Plans are validated, stored as artifacts, and interpreted deterministically.

4. **DAG-first plan model.**
   Linear plans are DAGs with a simple chain of dependencies.

5. **Payload discipline.**
   Workflow history stays small: large content always lives in Artifacts.

6. **Observable progress.**
   Execution progress is structured and retrievable without parsing logs.

---

## 3) Artifact reference contract

Large inputs/outputs (plans, manifests, patches, logs, model transcripts) are stored outside Temporal history. Workflows and activities pass **artifact references**.

### 3.1 ArtifactRef (canonical)

```json
{
  "artifact_ref": "art:sha256:BASE16…",
  "content_type": "application/json",
  "bytes": 12345,
  "created_at": "2026-03-05T00:00:00Z",
  "metadata": {
    "name": "plan.json",
    "producer": "skill:plan.generate",
    "labels": ["plan"]
  }
}
```

### 3.2 Rules

* `artifact_ref` is opaque to most code.
* Artifacts are **immutable** once written.
* Inputs/outputs larger than “small JSON” must be artifacts.
* Artifacts may be encrypted; access is controlled by the Artifact System doc.

---

## 4) Tool contract

### 4.1 Definition

A **Tool** is a named capability defined by:

* input schema
* output schema
* execution binding (how it is executed as an Activity or child workflow)
* default policies (timeouts, retries)
* capability requirements (what worker fleet can run it)

A Tool is not a workflow. Workflows interpret Plans and invoke Tools as Activities (or child workflows for `agent_runtime`).

Two tool subtypes are currently in use:

| `tool.type` | Dispatch mechanism | Contract |
|---|---|---|
| `skill` | Temporal Activity (`mm.tool.execute`) | `ToolDefinition` from registry snapshot |
| `agent_runtime` | Child `MoonMind.AgentRun` workflow | `AgentExecutionRequest` |

---

### 4.2 ToolDefinition schema (registry entry)

Tools are declared in a registry (YAML or JSON). Example:

```yaml
name: "repo.apply_patch"
version: "2.1.0"
type: "skill"
description: "Apply a patch artifact to a repo ref and optionally format."
inputs:
  schema:
    type: object
    required: [repo_ref, patch_artifact]
    properties:
      repo_ref: { type: string }
      patch_artifact: { type: string }      # ArtifactRef.artifact_ref
      format: { type: boolean, default: true }
outputs:
  schema:
    type: object
    required: [files_changed]
    properties:
      files_changed: { type: integer }
      commit_sha: { type: string }
      diff_artifact: { type: string }       # artifact_ref (optional)
executor:
  # See §11 decision: hybrid model
  activity_type: "mm.tool.execute"
  selector:
    mode: "by_capability"
requirements:
  capabilities:
    - "sandbox"
policies:
  timeouts:
    start_to_close_seconds: 300
    schedule_to_close_seconds: 1800
  retries:
    max_attempts: 3
    backoff: "exponential"
    non_retryable_error_codes:
      - "INVALID_INPUT"
      - "PERMISSION_DENIED"
security:
  allowed_roles: ["user", "admin"]
```

#### Required fields

* `name`, `version`
* `inputs.schema`, `outputs.schema` (JSON Schema)
* `executor.activity_type`
* `policies.timeouts`, `policies.retries`
* `requirements.capabilities`

---

### 4.3 ToolInvocation schema

A Plan node references a Tool with pinned version and inputs.

```json
{
  "id": "n1",
  "tool": { "type": "skill", "name": "repo.apply_patch", "version": "2.1.0" },
  "inputs": {
    "repo_ref": "git:org/repo#branch",
    "patch_artifact": "art:sha256:…",
    "format": true
  },
  "options": {
    "timeouts_override": { "start_to_close_seconds": 120 },
    "retries_override": { "max_attempts": 2 }
  }
}
```

Legacy compatibility form (accepted during migration):

```json
{
  "id": "n1",
  "skill": { "name": "repo.apply_patch", "version": "2.1.0" },
  "inputs": {
    "repo_ref": "git:org/repo#branch",
    "patch_artifact": "art:sha256:…"
  }
}
```

#### Rules

* `id` unique within Plan.
* Tool must exist in the pinned registry snapshot (see §8).
* Inputs must validate against the tool input schema.
* Overrides are optional and must be within safety limits.

---

### 4.4 ToolResult schema

Tool execution returns a structured result:

```json
{
  "status": "SUCCEEDED",
  "outputs": {
    "files_changed": 4,
    "commit_sha": "abc123"
  },
  "output_artifacts": [
    { "artifact_ref": "art:sha256:…", "content_type": "application/json", "bytes": 2048 }
  ],
  "progress": {
    "message": "Patch applied and formatted",
    "percent": 100
  }
}
```

#### Rules

* `outputs` is small JSON only.
* Any large data is written to artifacts and referenced in `output_artifacts`.

---

### 4.5 Error model (ToolFailure)

All failures normalize to:

```json
{
  "error_code": "RATE_LIMITED",
  "message": "Upstream provider rate limit",
  "retryable": true,
  "details": { "provider": "Jules", "retry_after_seconds": 30 },
  "cause": {
    "error_code": "HTTP_429",
    "message": "Too Many Requests"
  }
}
```

#### Standard error codes (v1)

* `INVALID_INPUT` (non-retryable)
* `PERMISSION_DENIED` (non-retryable)
* `NOT_FOUND` (non-retryable)
* `CONFLICT` (non-retryable unless inputs change)
* `RATE_LIMITED` (retryable)
* `TRANSIENT` (retryable)
* `TIMEOUT` (policy-driven)
* `EXTERNAL_FAILED` (usually non-retryable)
* `CANCELLED`
* `INTERNAL` (retryable up to max attempts)

#### Retry semantics

* Activity retry policy is derived from ToolDefinition defaults.
* `non_retryable_error_codes` stop retries immediately.
* `retryable` is informative; the actual retry decision is policy-driven.

---

## 5) Skill registry spec

### 5.1 Declaration format

* Repository-hosted registry files (YAML/JSON).
* Each entry is validated at build time and worker startup.

### 5.2 Discovery model (v1: static)

**Static registry snapshot** is the v1 requirement:

* Skills are bundled with worker deployments and API deployment.
* Registry has an immutable build identifier/digest (see §8).
* Deploying a new skill means deploying workers that can execute it.

### 5.3 Worker capability model

Workers declare capability sets (e.g., `llm`, `sandbox`, `integration:jules`). ToolDefinitions declare requirements. The runtime selects a task queue based on capabilities (details in Worker Topology doc).

---

## 6) Plan contract

### 6.1 Definition

A **Plan** is a DAG of tool invocations (Steps) with explicit dependencies and policy.

### 6.2 Plan schema (DAG-first)

```json
{
  "plan_version": "1.0",
  "metadata": {
    "title": "Fix failing tests",
    "created_at": "2026-03-05T00:00:00Z",
    "registry_snapshot": {
      "digest": "reg:sha256:…",
      "artifact_ref": "art:sha256:…"
    }
  },
  "policy": {
    "failure_mode": "FAIL_FAST",
    "max_concurrency": 8
  },
  "nodes": [
    {
      "id": "n1",
      "skill": { "name": "repo.run_tests", "version": "1.2.0" },
      "inputs": { "repo_ref": "git:org/repo#branch" }
    },
    {
      "id": "n2",
      "skill": { "name": "plan.generate", "version": "1.0.0" },
      "inputs": { "context_artifact": "art:sha256:…" }
    }
  ],
  "edges": [
    { "from": "n1", "to": "n2" }
  ]
}
```

### 6.3 Dependency semantics

* `from → to` means:

  * `to` may start only after `from` succeeds (v1).
  * `to.inputs` may reference `from` outputs via references (see below).

### 6.4 Data references between nodes

Inputs can reference outputs of prior nodes:

```json
{
  "ref": { "node": "n1", "json_pointer": "/outputs/test_report_artifact" }
}
```

Rules:

* references must resolve to a valid node and output path.
* resolving a reference is deterministic given recorded activity results.

### 6.5 Concurrency

* A node is **ready** when all dependencies have succeeded.
* Up to `policy.max_concurrency` ready nodes may run concurrently.

### 6.6 Failure policy (v1)

* `FAIL_FAST`: first failure ends execution; outstanding work is cancelled.
* `CONTINUE`: independent branches continue; failures are reported in final summary.

> v1 intentionally does **not** include conditional edges; see §11.

---

## 7) Plan production

### 7.1 “Planning is a skill”

Planning is expressed as one or more skills (e.g., `plan.generate`) executed as Activities. A planner may be LLM-driven or not, but it is always an Activity.

### 7.2 Plans are artifacts

* The planner skill writes the Plan as an artifact and returns `plan_artifact`.
* Workflows pass only the reference.

---

## 8) Determinism boundaries

### 8.1 Workflow code responsibilities (deterministic)

* load plan (via activity)
* validate structure / graph properties (or accept validated plan)
* schedule activities based on plan readiness
* track node states and aggregate outcomes
* emit structured progress (see §10)

### 8.2 Activity responsibilities (nondeterministic allowed)

* execute a skill invocation (LLM calls, shell, git, integrations)
* read/write artifacts
* transform data

---

## 9) Execution semantics (Plan → Activity invocations)

### 9.1 The Plan Executor (workflow algorithm)

1. Read the plan artifact reference.
2. Validate plan:

   * structural checks in workflow (cheap)
   * deep schema checks in a validation Activity (authoritative) — see §11
3. Compute ready set (nodes with satisfied deps).
4. Schedule activity for each ready node up to concurrency cap.
5. When a node completes:

   * store its result reference
   * update state
   * unlock dependents whose deps are all succeeded
6. Apply failure policy:

   * fail fast or continue, per plan policy.
7. Produce final summary artifact.

### 9.2 Mapping a node to an Activity invocation

**Inputs**

* `Step` (plan node)
* pinned `registry_snapshot`

**Resolution**

* Resolve `ToolDefinition` from snapshot.
* Derive Activity Type and routing target (task queue) from ToolDefinition + worker capabilities.

**Invocation payload**

* `tool.name`, `tool.version`
* `inputs` (with references resolved to concrete values or artifact refs)
* an execution context (execution identifiers, correlation IDs)
* optional overrides (timeouts/retries within allowed bounds)

**Result**

* `ToolResult` recorded by Temporal as the activity result (small)
* large outputs written as artifacts and referenced

---

## 10) Progress and intermediate outputs

### 10.1 Progress model (v1)

Progress is represented as a small structured object:

```json
{
  "total_nodes": 12,
  "pending": 4,
  "running": 3,
  "succeeded": 4,
  "failed": 1,
  "last_event": "Completed repo.run_tests",
  "updated_at": "2026-03-05T00:10:00Z"
}
```

### 10.2 How progress is exposed (v1)

* Workflow maintains progress state internally.
* Workflow exposes a **Query** that returns the progress object.
* Additionally, the workflow periodically writes a `progress.json` artifact for durable retrieval (optional but recommended).

> Search Attributes/Memo updates for dashboard display are specified in the Workflow Lifecycle doc; this doc only defines the progress contract.

### 10.3 Intermediate outputs

* Each node completion produces a `ToolResult`.
* If `ToolResult` is small, store inline in interpreter state.
* If large, store as artifact and keep only `artifact_ref`.
* Nodes may reference previous outputs via `ref` pointers (resolved deterministically).

---

## 11) Validation rules (authoritative)

### 11.1 Tool registry validation

* unique `(name, version)`
* valid JSON Schemas
* valid policy bounds (timeouts, retries)
* executor binding defined
* capabilities listed

### 11.2 Plan validation (v1 rules)

Structural checks:

* `plan_version` supported
* node IDs unique
* edges reference existing nodes
* acyclic graph required
* referenced skills exist in pinned registry snapshot
* node inputs validate against skill input schema
* data references point to valid nodes + output pointers

---

## 12) Open questions — resolved with recommended solutions

This section **locks decisions** for implementation.

### Q1) Do we pin Plans to a registry snapshot or resolve “latest” skills at runtime?

**Decision (recommended): Pin to a registry snapshot.**

**Why**

* Reproducibility: the same plan re-runs the same skill contracts.
* Debuggability: you can answer “what code/schema was used?”
* Safety: avoids surprise changes from concurrent deployments.

**How**

* Every plan includes `metadata.registry_snapshot` with:

  * `digest` (immutable identifier)
  * `artifact_ref` to the snapshot content (the registry file(s) used)

**Validation rule**

* Interpreter must resolve skill definitions from the plan’s snapshot, not from “latest.”

---

### Q2) Do we allow conditional edges in v1?

**Decision (recommended): No conditional edges in v1.**

**Why**

* Conditional execution explodes semantics (skip vs fail vs partial).
* Greatly complicates validation, progress reporting, and user expectations.

**Forward-compatible extension**
Reserve fields without enabling them:

* `edges[].condition` (optional, ignored/invalid in v1)
* introduce explicit **condition nodes** later (`skill:decision.evaluate`) that gate downstream nodes by producing a boolean output and generating a new plan segment (or triggering Continue-As-New).

**v1 rule**

* Dependencies are strict: a node runs only if all deps succeeded.

---

### Q3) How much validation happens in workflow code vs in an Activity?

**Decision (recommended): Split validation.**

* **Workflow does lightweight structural checks** (acyclic, IDs, edges reference nodes).
* **Activity performs deep validation** (JSON Schema validation against pinned registry snapshot, reference resolution checks).

**Why**

* Keeps workflow deterministic and small.
* Produces consistent validation errors (same error model as other skills).
* Allows updating validators without touching workflow determinism concerns (still must version safely, but it’s easier).

**Activity**

* `plan.validate(plan_artifact_ref, registry_snapshot_ref)` → returns either:

  * `validated_plan_ref` (could be the same ref) or
  * a `SkillFailure` error.

**v1 rule**

* Execution begins only after `plan.validate` succeeds.

---

### Q4) Should `mm.skill.execute` be the only Activity Type, or should there be per-skill activity types?

**Decision (recommended): Hybrid model (dispatcher + curated activity types).**

**Why**

* A single dispatcher Activity Type (`mm.skill.execute`) is flexible and keeps catalogs small.
* But some boundaries benefit from explicit types for routing/isolation/least-privilege:

  * `artifact.read/write`
  * `integration.jules.*`
  * `integration.github.*`
  * `sandbox.exec` (high-risk)

**Implementation**

* Default: Skills bind to `mm.skill.execute`.
* Exception: Skills may bind directly to a curated Activity Type if they require special worker isolation or credentials.

**Rule**

* The Skill registry must declare the activity type; the interpreter does not guess.

---

## 13) Deliverables (this doc’s outputs)

### A) Tool registry spec

* ToolDefinition schema (required fields, validation)
* Static snapshot mechanism (digest + artifact ref)
* Capability requirements and policy defaults
* Activity type binding rules (hybrid model)

### B) Plan schema + examples + validation rules

* DAG-first plan schema (nodes/edges/policy/metadata)
* Reference format (`ref.node` + `json_pointer`)
* Examples:

  * linear chain
  * parallel branches
  * continue-on-failure

### C) Execution semantics

* Plan Executor algorithm (deterministic orchestration)
* Node → activity invocation mapping (registry snapshot resolution)
* Concurrency rules and failure modes
* Progress and intermediate output contracts

---

## 14) Engineering backlog

Minimum components: tool registry format + loader + validator; registry snapshot digest artifact; `plan.validate`; Plan Executor in `MoonMind.Run`; `mm.tool.execute` / skill dispatch activity; progress query and optional progress artifact. Status is tracked in [`docs/tmp/remaining-work/Tasks-SkillAndPlanContracts.md`](../tmp/remaining-work/Tasks-SkillAndPlanContracts.md).

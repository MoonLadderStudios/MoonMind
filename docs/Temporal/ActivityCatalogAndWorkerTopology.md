# Activity Catalog and Worker Topology

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-ActivityCatalogAndWorkerTopology.md`](../tmp/remaining-work/Temporal-ActivityCatalogAndWorkerTopology.md)

Status: **Implemented in core runtime** (catalog live; some target-state families still pending)
Last updated: **2026-03-30**
Scope: Defines MoonMind’s canonical **Activity Types**, **worker fleets**, **Task Queue routing**, and the operational rules for executing artifacts, planning, skills, integrations, managed runtime supervision, and related Temporal-side support work.

## Related docs

- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](./ManagedAndExternalAgentExecutionModel.md)
- [`docs/Tools/SkillSystem.md`](../Tools/SkillSystem.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](./WorkflowArtifactSystemDesign.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ErrorTaxonomy.md`](./ErrorTaxonomy.md)

---

## 1. Purpose

MoonMind uses Temporal’s abstractions directly:

- **Workflow Executions** orchestrate.
- **Activities** perform all side-effecting work.
- **Task Queues** are internal routing labels for worker fleets. They are **not** a product-level queue abstraction, and MoonMind makes no FIFO guarantees to users.

This document standardizes:

- activity families and naming
- which worker fleet owns which activity family
- the routing and timeout/retry model for those activities
- the contract boundary between workflow code and activity implementations
- the current implemented catalog and the target-state additions that are still pending

This document covers the **Temporal-managed worker model** only.

---

## 2. Goals and non-goals

## 2.1 Goals

1. Provide a stable activity taxonomy for:
   - artifact lifecycle
   - planning
   - executable tool execution
   - external integrations
   - managed runtime supervision
   - provider-profile coordination support
   - proposal/review support
   - future agent skill resolution and materialization

2. Define worker fleets with clear:
   - capability boundaries
   - security boundaries
   - scaling expectations

3. Ensure activity contracts are:
   - retry-safe
   - observable
   - cancel-aware where needed
   - payload-disciplined

4. Keep Task Queue usage minimal and operational.

5. Make canonical contract boundaries explicit so workflow code does not perform provider-specific coercion.

## 2.2 Non-goals

This document does **not** define:

- workflow lifecycle semantics
- product-level queue ordering or priority semantics
- source-precedence rules for agent skill resolution
- provider-specific business logic beyond what is needed to define activity boundaries

---

## 3. Core principles

### 3.1 Determinism boundary

Workflow code must remain deterministic. Any nondeterminism belongs in Activities, including:

- network calls
- filesystem access
- subprocesses
- clocks and random values
- external provider inspection
- mutable runtime state reads

### 3.2 Stable public surface

Activity Type names are long-lived contracts. Prefer adding new activity types over changing semantics in place.

### 3.3 Routing by capability

Workers are split by what they can safely do:

- artifact I/O
- LLM access
- sandbox execution
- provider integration access
- managed runtime execution

They are not split by legacy product nouns.

### 3.4 Payload discipline

Large inputs and outputs belong in the artifact system. Activity payloads should contain refs and compact metadata, not blobs.

### 3.5 Canonical return-shape rule

For true agent-runtime execution, activities must return canonical runtime contracts directly.

That means:

- `integration.<provider>.start` returns `AgentRunHandle`
- `integration.<provider>.status` returns `AgentRunStatus`
- `integration.<provider>.fetch_result` returns `AgentRunResult`
- `integration.<provider>.cancel` returns `AgentRunStatus`
- `agent_runtime.status` returns `AgentRunStatus`
- `agent_runtime.fetch_result` returns `AgentRunResult`

Workflow code should not reconstruct canonical contracts from provider-shaped payloads.

### 3.6 Search Attribute ownership

Activities do **not** upsert Search Attributes or workflow memo state directly. They return results to workflows, and workflows own visibility updates.

### 3.7 Continue-As-New awareness

Activities must not assume a stable Temporal `run_id` across the entire logical lifetime of a workflow. `correlation_id` is the durable business identifier across Continue-As-New boundaries.

---

## 4. Task queues (routing only)

Temporal requires Task Queues so Workers can poll. MoonMind uses them strictly as internal routing plumbing.

## 4.1 Current queue set

### Workflow task queue

- `mm.workflow`

### Activity task queues

- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.integrations`
- `mm.activity.agent_runtime`

## 4.2 Queue policy

MoonMind starts with a minimal queue set.

Examples of deliberate non-decisions:

- no provider-specific LLM subqueues by default
- no priority lanes by default
- no queue-per-tool explosion
- no queue-per-provider unless isolation or scaling truly demands it

Rule of thumb: subdivide only when you need different secrets, different egress, different scaling behavior, or materially different isolation.

---

## 5. Worker fleets

The activity catalog maps activity types onto the following fleets.

| Fleet | Queue(s) | Primary capabilities | Primary privileges |
|---|---|---|---|
| `workflow` | `mm.workflow` | workflow execution, limited helper activities | Temporal only |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle, provider-profile support, OAuth session support | artifact storage, DB-backed support services |
| `llm` | `mm.activity.llm` | planning, validation, review, generic LLM work | model/provider credentials |
| `sandbox` | `mm.activity.sandbox` | repo and command execution | isolated process execution |
| `integrations` | `mm.activity.integrations` | external provider APIs and repo operations | provider tokens, egress to provider APIs |
| `agent_runtime` | `mm.activity.agent_runtime` | managed runtime launch, supervision, status, result fetch, cancellation | isolated runtime execution, auth volume mounts |

## 5.1 Workflow fleet exception rule

The workflow fleet is primarily for workflow code. It may also host **small helper activities** when needed to preserve deterministic workflow behavior without creating unnecessary routing complexity.

Current example:

- `integration.resolve_adapter_metadata`

This is a narrow exception, not a second general-purpose activity plane.

---

## 6. Naming conventions

Activity Type names use dotted namespaces.

### 6.1 Canonical namespaces

- `artifact.*` — artifact lifecycle
- `plan.*` — planning and plan validation
- `mm.skill.execute` — default registry-dispatched executable tool path
- `sandbox.*` — shell, repo, and workspace actions
- `integration.<provider>.*` — provider-specific external integrations
- `repo.*` — provider-backed repo operations exposed as general-purpose activities
- `provider_profile.*` — provider-profile coordination support
- `oauth_session.*` — OAuth session lifecycle support
- `agent_runtime.*` — managed runtime launch/supervision/result/cancel operations
- `proposal.*` — task proposal generation/submission
- `step.review` — review gate execution

### 6.2 Target-state namespace not yet fully implemented

- `agent_skill.*` — future skill resolution/materialization family

This family is still part of the target-state architecture but is not yet a core live catalog family in the current implementation.

---

## 7. Contract model

Activity contracts should be small, business-focused, and explicit.

## 7.1 General request shape guidelines

Common business fields include:

- `correlation_id`
- `idempotency_key`
- `input_refs[]`
- `parameters`
- compact selector/config fields

Do not duplicate Temporal execution metadata into every payload unless an external contract truly requires it.

## 7.2 Canonical agent contract references

For true agent-runtime activities, the canonical schema source of truth is:

- `AgentExecutionRequest`
- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`

as defined in `moonmind/schemas/agent_runtime_models.py`.

## 7.3 Where provider-specific data belongs

Provider-specific details belong in canonical `metadata` fields, not alternate top-level response shapes.

Examples of acceptable metadata:

- provider URLs
- normalized provider status labels
- callback support flags
- PR URLs
- merge outcomes
- tracking refs

Examples of unacceptable workflow-facing top-level variants:

- ad hoc `{external_id, tracking_ref}` instead of `AgentRunHandle`
- raw provider status blobs instead of `AgentRunStatus`
- custom provider result payloads instead of `AgentRunResult`

---

## 8. Current implemented activity catalog

This section describes the current implemented families and their role.

## 8.1 Artifact activities (`artifact.*`)

Purpose: artifact lifecycle management.

Current implemented activities include:

- `artifact.create`
- `artifact.write_complete`
- `artifact.read`
- `artifact.list_for_execution`
- `artifact.compute_preview`
- `artifact.link`
- `artifact.pin`
- `artifact.unpin`
- `artifact.lifecycle_sweep`

Worker queue: `mm.activity.artifacts`

Key rules:

- large content stays in artifact storage
- writes must be retry-safe
- artifact references are the durable interface used by workflows and other activities

## 8.2 Plan activities (`plan.*`)

Purpose: plan generation and validation.

Current implemented activities:

- `plan.generate`
- `plan.validate`

Worker queue: typically `mm.activity.llm`

Key rules:

- planning is always nondeterministic, therefore always an activity
- plan outputs are stored as artifacts, not placed directly into workflow history

## 8.3 Tool execution (`mm.skill.execute`)

Purpose: execute a registry-defined executable tool through the default dispatcher path.

Current implemented activity:

- `mm.skill.execute`

Routing is determined by registry metadata and capability class.

Key rules:

- tool execution remains separate from true agent-runtime execution
- the pinned registry snapshot is the source of truth for routing and policies

## 8.4 Sandbox activities (`sandbox.*`)

Purpose: isolated repo and process execution.

Current implemented activities:

- `sandbox.checkout_repo`
- `sandbox.apply_patch`
- `sandbox.run_command`
- `sandbox.run_tests`

Worker queue: `mm.activity.sandbox`

Key rules:

- strong isolation
- explicit concurrency limits
- heartbeat support for long-running operations
- careful retry handling to avoid duplicate side effects

## 8.5 Provider-profile support activities (`provider_profile.*`)

Purpose: support the managed-runtime provider-profile lifecycle.

Current implemented activities:

- `provider_profile.list`
- `provider_profile.ensure_manager`
- `provider_profile.reset_manager`
- `provider_profile.verify_lease_holders`
- `provider_profile.sync_slot_leases`

Worker queue: `mm.activity.artifacts`

These are support activities used by workflow and manager orchestration. They are not part of the end-user skill or agent contract surface.

## 8.6 OAuth session activities (`oauth_session.*`)

Purpose: OAuth session preparation, update, verification, and cleanup.

Current implemented activities:

- `oauth_session.ensure_volume`
- `oauth_session.start_auth_runner`
- `oauth_session.stop_auth_runner`
- `oauth_session.update_terminal_session`
- `oauth_session.update_status`
- `oauth_session.verify_volume`
- `oauth_session.verify_cli_fingerprint`
- `oauth_session.register_profile`
- `oauth_session.mark_failed`
- `oauth_session.cleanup_stale`

Worker queue: `mm.activity.artifacts`

## 8.7 Integration activities (`integration.<provider>.*`)

Purpose: external provider interaction and delegated agent execution.

### Current implemented provider families

- `integration.jules.*`
- `integration.codex_cloud.*`
- `integration.openclaw.execute`

### Jules and Codex Cloud contract pattern

Current canonical pattern:

- `integration.jules.start(...) -> AgentRunHandle`
- `integration.jules.status(...) -> AgentRunStatus`
- `integration.jules.fetch_result(...) -> AgentRunResult`
- `integration.jules.cancel(...) -> AgentRunStatus`

and likewise for `integration.codex_cloud.*`

### OpenClaw streaming-gateway contract pattern

`integration.openclaw.execute(...)` is a special-case single-call external execution path for providers using the streaming-gateway execution style.

Contract:

- `integration.openclaw.execute(...) -> AgentRunResult`

Worker queue: `mm.activity.integrations`

### Integration helper activity

Current helper activity:

- `integration.resolve_adapter_metadata`

Contract:

- validate adapter registration
- return adapter metadata such as execution style
- keep env inspection and dynamic provider registration reads out of deterministic workflow code

This activity is intentionally small and is not part of the true agent-run status/result contract family.

## 8.8 Repo activities (`repo.*`)

Purpose: provider-backed repository operations used by workflow or runtime flows.

Current implemented activities:

- `repo.create_pr`
- `repo.merge_pr`

Worker queue: `mm.activity.integrations`

## 8.9 Managed runtime activities (`agent_runtime.*`)

Purpose: managed runtime launch, supervision support, result collection, artifact publication, and cancellation.

Current implemented activities:

- `agent_runtime.launch`
- `agent_runtime.launch_session`
- `agent_runtime.publish_artifacts`
- `agent_runtime.session_status`
- `agent_runtime.send_turn`
- `agent_runtime.steer_turn`
- `agent_runtime.interrupt_turn`
- `agent_runtime.clear_session`
- `agent_runtime.terminate_session`
- `agent_runtime.fetch_session_summary`
- `agent_runtime.publish_session_artifacts`
- `agent_runtime.reconcile_managed_sessions`
- `agent_runtime.status`
- `agent_runtime.fetch_result`
- `agent_runtime.cancel`

Worker queue: `mm.activity.agent_runtime`

### Contract expectations

- `agent_runtime.status(...) -> AgentRunStatus`
- `agent_runtime.fetch_result(...) -> AgentRunResult`
- `agent_runtime.cancel(...) -> AgentRunStatus`
- `agent_runtime.launch_session(...) -> CodexManagedSessionHandle`
- `agent_runtime.session_status(...) -> CodexManagedSessionHandle`
- `agent_runtime.send_turn(...) -> CodexManagedSessionTurnResponse`
- `agent_runtime.steer_turn(...) -> CodexManagedSessionTurnResponse`
- `agent_runtime.interrupt_turn(...) -> CodexManagedSessionTurnResponse`
- `agent_runtime.clear_session(...) -> CodexManagedSessionHandle`
- `agent_runtime.terminate_session(...) -> CodexManagedSessionHandle`
- `agent_runtime.fetch_session_summary(...) -> CodexManagedSessionSummary`
- `agent_runtime.publish_session_artifacts(...) -> CodexManagedSessionArtifactsPublication`
- `agent_runtime.reconcile_managed_sessions(...) -> reconciliation summary payload`

`agent_runtime.publish_artifacts` should return a canonical-result-compatible enriched payload that can be materialized as `AgentRunResult`.

`agent_runtime.launch` is an internal launch/support activity rather than a public canonical runtime contract in the same sense as `status` and `fetch_result`.

The session-oriented activities are remote-session contracts. They must delegate through a session controller or adapter boundary and must not fall back to the worker-local managed runtime launcher/process loop.

The current session-oriented return types are Codex-specific because Codex is the live managed-session implementation. When a second runtime adopts task-scoped managed sessions, introduce a neutral managed-session request/response surface above runtime-specific adapters rather than spreading Codex contracts into the public workflow boundary.

## 8.10 Proposal and review activities

Current implemented activities:

- `proposal.generate`
- `proposal.submit`
- `step.review`

Queues:

- `proposal.generate` → `mm.activity.llm`
- `proposal.submit` → `mm.activity.artifacts`
- `step.review` → `mm.activity.llm`

These are support families, not agent-runtime families.

---

## 9. Target-state additions not yet fully implemented

MoonMind’s broader design still includes a future `agent_skill.*` family.

Target-state activities:

- `agent_skill.resolve`
- `agent_skill.materialize`
- `agent_skill.build_prompt_index`

Purpose:

- resolve active instruction bundles into immutable snapshots
- materialize those snapshots for runtime consumption
- build prompt indexes or other compact runtime-ready skill representations

Important rule:

- resolution semantics must remain centralized
- materialization may vary by runtime
- workflows should consume refs, not inline skill content

This family should be documented as target-state until it is actually added to the live catalog.

---

## 10. Routing rules

Workflows choose activities through Activity Options and catalog-derived routing metadata.

## 10.1 Capability mapping

Each activity family maps to one capability class and one fleet.

Representative capability classes include:

- `artifacts`
- `llm`
- `sandbox`
- `integration:<provider>`
- `agent_runtime`
- `workflow` (for narrow helper activity exceptions)

## 10.2 Selection examples

- `plan.generate` routes to `mm.activity.llm`
- `sandbox.run_tests` routes to `mm.activity.sandbox`
- `integration.jules.start` routes to `mm.activity.integrations`
- `agent_runtime.fetch_result` routes to `mm.activity.agent_runtime`
- `provider_profile.list` routes to `mm.activity.artifacts`
- `integration.resolve_adapter_metadata` routes to `mm.workflow`

## 10.3 No workflow-side route probing

Workflow code should use the live catalog as the routing source of truth. The system should not grow additional ad hoc workflow-side probing or provider-specific routing heuristics when the catalog can express the routing directly.

---

## 11. Reliability contracts

## 11.1 Timeout defaults by family

Typical defaults by family:

- `artifact.*` — short
- `plan.*` — moderate
- `sandbox.*` — longer, often heartbeat-required
- `integration.*` — short per request; long-running external work should be modeled as start/status/fetch or async completion
- `agent_runtime.*` — moderate, with short status reads and bounded launch/fetch/cancel windows

## 11.2 Retry policy rules

- use bounded exponential backoff
- prefer non-retryable classification for invalid inputs or unsupported contract states
- avoid retries that duplicate destructive sandbox side effects
- ensure external starts are idempotent
- ensure canonical contract normalization failures are treated as contract errors, not silently tolerated

## 11.3 Heartbeats

Heartbeat-required activities should be explicitly marked in the catalog.

Representative heartbeat-required cases include:

- long-running sandbox operations
- long-running streaming gateway operations
- managed runtime launch/publish operations where progress visibility is required

Short status reads should remain short and should not become long-running heartbeat loops.

## 11.4 Idempotency

Rules:

- side-effecting activities accept or derive stable idempotency keys
- artifact writes remain naturally retry-safe through integrity checks
- external starts must not create duplicate jobs on retry
- managed launches must not create duplicate runtime executions on retry
- any future `agent_skill.materialize` activity must be safe under retry and must not mutate checked-in source trees in place

---

## 12. Security model

### 12.1 Least privilege per fleet

- sandbox workers do not hold provider API keys by default
- integration workers do not run arbitrary shell commands
- artifact workers do not need sandbox execution privileges
- agent runtime workers have stronger execution privileges but a narrower responsibility set

### 12.2 Network controls

- LLM fleet can reach model endpoints
- integrations fleet can reach provider APIs
- sandbox fleet should have restricted egress
- agent runtime fleet should only have the runtime/proxy/network access it actually needs

### 12.3 Secret handling

- use secret managers or controlled durable auth volumes where appropriate
- do not place raw credentials into workflow payloads, artifacts, or logs
- do not leak provider tokens through metadata or diagnostics artifacts

### 12.4 Data handling

- large content stays in artifacts
- workflow history carries only refs and compact metadata
- previews and redaction are handled through artifact-layer controls, not by bloating workflow payloads

---

## 13. Observability requirements

## 13.1 Logging

Every activity log line should include enough context to answer:

- which workflow/run initiated the activity
- which activity type ran
- which attempt this is
- which correlation ID and idempotency key were involved

At minimum:

- `workflow_id`
- `run_id`
- `activity_type`
- `activity_id`
- `attempt`
- `correlation_id`
- `idempotency_key` or a hash of it

Large logs belong in artifacts.

## 13.2 Metrics

Per fleet, track:

- queue backlog and lag
- execution latency
- retry counts
- failure reasons
- resource usage where relevant
- repeated timeout/retry patterns worth operator attention

For agent-runtime and integration activities, metric dimensions should align with canonical contract states rather than provider-specific raw states.

## 13.3 Tracing

If using OpenTelemetry:

- propagate correlation IDs through activities
- annotate spans with workflow/run identifiers
- keep provider-specific noise out of top-level span naming where possible

---

## 14. Testing strategy

1. **Activity contract tests**
   - validate canonical request and response schemas
   - ensure provider activities return canonical contracts

2. **Worker fleet integration tests**
   - verify activity-to-fleet routing
   - verify helper activities remain narrow and intentional

3. **Load tests**
   - sandbox concurrency and isolation
   - LLM rate limiting correctness
   - managed runtime queue behavior

4. **Failure injection**
   - provider outages
   - artifact store outages
   - worker restarts mid-activity
   - manager restart and lease recovery paths

5. **Canonical contract enforcement**
   - reject unknown provider statuses at the adapter/activity boundary
   - ensure workflows do not depend on provider-shaped payloads
   - ensure metadata carries provider-specific details without breaking canonical top-level schemas

6. **Traceability gate**
   - catalog changes must stay aligned with runtime code, docs, and tests

---

## 15. Decided questions

### 15.1 Provider-specific LLM task queues

Deferred. Start with one `mm.activity.llm` queue. Split only when operational isolation or scaling demands it.

### 15.2 Priority lanes

Deferred for v1. Throughput control comes from concurrency and rate limiting, not queue ordering guarantees.

### 15.3 Search Attributes from activities

Disallowed. Workflows own visibility state.

### 15.4 Workflow fleet helper activities

Allowed only as a narrow exception. Current example: `integration.resolve_adapter_metadata`.

### 15.5 Canonical runtime contract enforcement

Decided. The activity boundary, not workflow code, owns normalization into `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.

---

## 16. Summary

MoonMind’s Temporal activity topology is organized around a small number of capability-based fleets:

- workflow
- artifacts
- llm
- sandbox
- integrations
- agent_runtime

The catalog is already live for:

- artifact lifecycle
- planning
- executable tool dispatch
- sandbox work
- provider integrations
- provider-profile support
- OAuth session support
- managed runtime supervision
- proposals and review

The key architectural rule for current and future work is:

- **Activities own side effects**
- **The catalog owns routing**
- **Canonical runtime contracts cross the workflow boundary**
- **Workflow code should not perform provider-specific coercion**

That keeps MoonMind’s Temporal model easier to reason about, easier to test, and safer to evolve.

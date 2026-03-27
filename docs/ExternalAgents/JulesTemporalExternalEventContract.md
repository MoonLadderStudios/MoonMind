# Jules Temporal External Event Contract

**Implementation tracking:** [`docs/tmp/remaining-work/ExternalAgents-JulesTemporalExternalEventContract.md`](../tmp/remaining-work/ExternalAgents-JulesTemporalExternalEventContract.md)

Status: Draft  
Owner: MoonMind Platform  
Last updated: 2026-03-06  
Audience: backend, workflow, API, worker, and dashboard implementers

## 1) Purpose

This document defines the **Jules-specific implementation contract** for integrating Jules with MoonMind's Temporal-first external monitoring model.

It exists to translate the provider-neutral rules in `docs/Temporal/IntegrationsMonitoringDesign.md` into a concrete Jules profile without polluting the shared design with provider-specific details.

This document covers:

- how MoonMind identifies and correlates Jules operations
- the current non-Temporal Jules adapter surface that already exists in the repo
- the target Temporal activity contract for Jules-backed work
- how polling, callbacks, artifacts, and status normalization should behave for Jules
- what is locked today vs what remains intentionally provisional

---

## 2) Related docs

This document builds on and must stay consistent with:

- `docs/Temporal/IntegrationsMonitoringDesign.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ArtifactPresentationContract.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/ExternalAgents/JulesAdapter.md`

If this document conflicts with the provider-neutral Temporal docs or the unified execution model, the shared docs win unless this file explicitly narrows behavior for Jules.

---

## 3) Scope / Non-goals

### In scope

- Jules provider naming and identifier mapping
- current Jules adapter request/response shapes
- current runtime gating requirements for Jules execution
- the Jules-specific Temporal activity contract
- polling/callback posture for Jules
- recommended artifact and correlation behavior for Jules operations

### Out of scope

- redesigning the generic integrations model
- locking a broad MoonMind-wide status taxonomy beyond what Jules needs
- defining the full UI presentation contract for external integrations
- claiming that Jules is already fully Temporal-backed everywhere in the repo

---

## 4) Current repo baseline

MoonMind contains the following Jules integration components:

- **Configuration**: typed Jules settings in `moonmind/config/jules_settings.py`
- **Schemas**: typed request/response models in `moonmind/schemas/jules_models.py`, including `normalize_jules_status()`
- **HTTP client**: async adapter in `moonmind/workflows/adapters/jules_client.py`
- **Agent adapter**: `JulesAgentAdapter` implementing the `AgentAdapter` protocol in `moonmind/workflows/adapters/jules_agent_adapter.py`
- **MCP tooling**: `JulesToolRegistry` in `moonmind/mcp/jules_tool_registry.py`
- **Runtime gating**: gating helpers in `moonmind/jules/runtime.py`
- **Temporal activities**: `integration.jules.start`, `.status`, and `.fetch_result` registered in `moonmind/workflows/temporal/activity_catalog.py` on the `mm.activity.integrations` queue
- **Worker fleet**: `integration:jules` capability in the integrations fleet (`moonmind/workflows/temporal/workers.py`)

All agent execution (both managed and external) now flows through the `MoonMind.AgentRun` child workflow, dispatched per plan step from `MoonMind.Run._run_execution_stage()` when `tool.type == "agent_runtime"`. Legacy non-Temporal execution paths have been removed (see Phase 4.5 in `ManagedAndExternalAgentExecutionModel.md`).

### Implementation stance

For Jules, MoonMind uses the following posture:

- **today:** `JulesAgentAdapter` exists but external adapter instantiation in `MoonMind.AgentRun` is not yet wired (Phase C migration pending)
- **integration activities:** `integration.jules.start/status/fetch_result` are used for the integration polling stage in `MoonMind.Run._run_integration_stage()`
- **default posture:** callback-first **when Jules supports it reliably**, with polling fallback
- **current safe assumption:** polling is required unless a verified Jules callback path is explicitly implemented

---

## 5) Canonical Jules provider identity

### 5.1 Provider name

The canonical integration name is:

- `jules`

This value should be used anywhere MoonMind stores provider identity in workflow state, correlation records, artifacts, or bounded visibility attributes.

### 5.2 External operation handle

For Jules, the provider handle is:

- `external_operation_id = taskId`

This is the Jules-side identifier returned by the current adapter response model.

### 5.3 Additional provider fields

The current Jules response shape only guarantees a compact provider response:

- `taskId`
- `status`
- optional `url`

MoonMind should preserve the raw `status` string as `provider_status` and treat `url` as an optional external deep link for operators and UI surfaces.

---

## 6) Configuration and feature gating

Jules support is feature-gated and must not be assumed available just because the code exists.

### 6.1 Required configuration

Jules support depends on:

- `JULES_ENABLED`
- `JULES_API_URL`
- `JULES_API_KEY`

Optional operational controls:

- `JULES_TIMEOUT_SECONDS`
- `JULES_RETRY_ATTEMPTS`
- `JULES_RETRY_DELAY_SECONDS`

### 6.2 Runtime gate behavior

The canonical Jules runtime gate is the same one used by the rest of the repo:

- `targetRuntime=jules` is only valid when Jules is enabled and fully configured
- when the runtime gate is not satisfied, Jules-backed execution must be rejected early
- Jules MCP tools should be excluded from discovery when Jules is disabled or missing credentials

### 6.3 Operational rule

Any new Temporal worker, API callback handler, or dashboard control that depends on Jules must obey the same runtime gate. Do not add a second Jules enablement flag for Temporal code paths.

---

## 7) Current non-Temporal Jules contract

This section documents the current adapter behavior that the Temporal implementation must either reuse or intentionally supersede.

### 7.1 Current request models

#### Create task

Current payload fields:

- `title`
- `description`
- optional `metadata`

#### Get task

Current payload fields:

- `taskId`

#### Resolve task

Current payload fields:

- `taskId`
- `resolutionNotes`
- `status` (defaults to `completed` in the current schema)

### 7.2 Current response model

The current typed response model includes:

- `taskId`
- `status`
- optional `url`

### 7.3 Current transport behavior

The current Jules adapter uses:

- bearer-token auth
- JSON request/response transport
- manual retries on `5xx`, `429`, transport errors, and timeouts
- immediate failure on other `4xx`
- scrubbed exception text that must not leak secrets

### 7.4 Current API surface exposed through MoonMind

The adapter contract currently maps to:

- `POST /tasks` for create
- `GET /tasks/{taskId}` for status/detail fetch
- `POST /tasks/{taskId}/finish` for resolve/finish

The current repo does **not** define a Jules cancel endpoint in the adapter.

---

## 8) Jules correlation contract

The provider-neutral Temporal design requires a stable `correlation_id` that survives retry and Continue-As-New. Jules-specific implementation must preserve that rule.

### 8.1 Required identifiers

For Jules-backed external monitoring, MoonMind should track:

- `integration_name = jules`
- `correlation_id` — MoonMind-generated stable identifier
- `external_operation_id` — Jules `taskId`
- `provider_status` — raw Jules status string
- `normalized_status` — MoonMind-mapped status
- optional `external_url` — Jules URL when present

### 8.2 Callback correlation key

Because the current typed Jules contract does not expose a first-class callback field, `callback_correlation_key` is **optional and future-facing** for Jules.

If Jules later supports callbacks/webhooks, MoonMind should prefer a stable callback correlation token that resolves back to the durable MoonMind correlation record rather than depending on Temporal visibility scans.

### 8.3 Where correlation metadata should go today

The current `create_task` request already supports optional `metadata`. Until Jules has a stronger callback/correlation API, MoonMind should use that field as the preferred provider-side place to embed non-secret correlation hints when useful.

Rules:

- never store secrets in Jules metadata
- prefer stable correlation values over ephemeral run identifiers
- do not rely on provider metadata as the only source of truth; MoonMind still owns the durable correlation record

---

## 9) Jules status normalization contract

The current Jules typed response only guarantees an opaque provider `status: str`. That means MoonMind should be conservative about what it treats as locked.

### 9.1 Locked rule

Always preserve the raw Jules status as:

- `provider_status`

### 9.2 Minimum normalization rule

MoonMind should map provider status into a bounded internal state:

- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`
- `unknown`

### 9.3 Current safe mapping posture

Because the current adapter does not yet publish a full, locked Jules status taxonomy, MoonMind should treat the repo's existing worker-side aliases as the current implementation baseline rather than inventing new ad hoc mappings:

- success aliases currently observed in the repo are `completed`, `succeeded`, `success`, `done`, `resolved`, and `finished`
- failure aliases currently observed in the repo are `cancelled`, `canceled`, `error`, `failed`, `rejected`, `timed_out`, and `timeout`
- a raw empty or missing status may be normalized to a non-terminal provider token such as `pending` before MoonMind maps it into a bounded workflow status
- all other mappings should be maintained explicitly in the Jules integration implementation, not inferred ad hoc in UI code
- unknown provider statuses must fall back to `unknown` rather than pretending success or failure

### 9.4 Ownership rule

The mapping from Jules `status` to MoonMind `normalized_status` belongs in the Jules integration implementation, not in generic workflow code and not in the dashboard.

Temporal activity and workflow code should reuse one central Jules status normalizer so the legacy polling worker path and Temporal path cannot drift semantically.

---

## 10) Jules Temporal activity contract

Jules should follow the generic activity naming scheme from the provider-neutral design.

This section distinguishes between:

- the **current repo activity signatures** that already exist today
- the **provider-neutral semantic contract** those activities are expected to satisfy as Temporal adoption continues

### 10.1 Activity names

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`
- `integration.jules.cancel`

Default routing should remain:

- `mm.activity.integrations`

Do **not** create a Jules-specific task queue by default. Only split queues if secrets, quotas, or scaling requirements demand it.

Current repo alignment:

- `integration.jules.start`, `integration.jules.status`, and `integration.jules.fetch_result` are registered today
- `integration.jules.cancel` is a reserved target contract name and should not be documented as implemented until it is added to the activity catalog and runtime bindings

### 10.2 `integration.jules.start`

**Purpose**

Start a Jules task and return the provider handle plus monitoring hints.

**Input**

- semantic contract:
  - `correlation_id`
  - `idempotency_key`
  - task parameters including `title`, `description`, and optional provider metadata
  - optional callback metadata if and when Jules supports it
- current repo signature:
  - optional `principal`
  - optional singular `inputs_ref` artifact used to source the description when `parameters.description` is absent
  - optional `execution_ref`
  - optional `idempotency_key`
  - optional `parameters.title`
  - optional `parameters.description`
  - optional `parameters.metadata`

**Current implementation guidance**

- call the existing Jules create-task adapter
- embed correlation hints inside provider metadata when useful and safe
- if `inputs_ref` is used, treat it as an implementation detail of the current MoonMind artifact contract rather than a provider-level field
- treat the returned Jules `taskId` as `external_operation_id`
- preserve the returned `status` as `provider_status`
- set `callback_supported=false` unless a verified Jules callback path is implemented
- return `external_url` when Jules returns `url`
- keep activity retries safe by deriving provider-side idempotency from stable request identity, never from a Temporal `run_id`

**Output**

- semantic contract:
  - `external_operation_id`
  - `normalized_status`
  - `provider_status`
  - `callback_supported`
  - optional `external_url`
  - optional `provider_summary`
- current repo result shape:
  - `external_id`
  - raw `status`
  - optional `tracking_ref`
  - optional `url`

Workflow-level adapters should map the current repo result shape into the semantic contract rather than forcing dashboard code to infer meaning from raw fields.

### 10.3 `integration.jules.status`

**Purpose**

Read current Jules task state during polling or reconciliation.

**Current implementation guidance**

- call the existing Jules get-task adapter using `taskId`
- preserve raw Jules `status`
- return `external_url` when present
- treat this activity as read-only and aggressively retryable
- persist bounded tracking artifacts when artifact storage is available, but keep the workflow-visible return value compact

**Output**

- semantic contract:
  - `normalized_status`
  - `provider_status`
  - `terminal`
  - optional `external_url`
  - optional compact `provider_summary`
- current repo result shape:
  - `external_id`
  - raw `status`
  - optional `tracking_ref`
  - optional `url`

### 10.4 `integration.jules.fetch_result`

**Purpose**

Fetch or materialize terminal Jules outputs into MoonMind artifacts.

**Current implementation guidance**

The current Jules adapter does **not** define a richer result-download API beyond task fetch and finish. Therefore, the first valid implementation of `integration.jules.fetch_result` should be conservative:

- persist the final Jules task snapshot as an artifact
- persist any MoonMind-authored resolution notes or summary as an artifact when that data actually exists in the calling flow
- return artifact refs for those persisted records
- avoid assuming that Jules already provides diff/log/output endpoints unless a newer adapter contract explicitly adds them

Current repo alignment:

- the current Temporal implementation reuses `integration.jules.status` and returns the resulting tracking artifact ref
- that is acceptable as an initial compatibility step, but it should continue to be documented as a conservative snapshot fetch rather than a rich result-download contract

**Output**

- `output_refs[]`
- optional `summary`
- optional `diagnostics_ref`

### 10.5 `integration.jules.cancel`

**Purpose**

Best-effort provider cancellation.

**Current implementation guidance**

`JulesAgentAdapter.cancel()` exists and attempts cancellation by calling the Jules `resolve_task` endpoint with `status="canceled"`. If the provider rejects the cancel request, the adapter returns `intervention_requested` status with `cancelAccepted: False`.

However:

- `integration.jules.cancel` is **not yet registered** as a Temporal activity in the activity catalog
- workflow cancellation via `MoonMind.AgentRun` proceeds with MoonMind cancellation semantics regardless of provider-side cancel support
- the `invoke_adapter_cancel` activity in `MoonMind.AgentRun` is not yet wired to the Jules adapter (Phase C migration pending)

Current repo alignment:

- the adapter-level cancel path exists but is not yet connected to the Temporal activity and workflow cancel flows
- documentation and UI surfaces should treat provider cancellation for Jules as a best-effort capability, not a guaranteed operation

Do **not** fake provider cancellation success if the Jules API rejects the cancel request.

---

## 11) Polling and callback posture for Jules

### 11.1 Current posture

The current MoonMind design explicitly acknowledges existing Jules polling behavior elsewhere in the codebase and does not claim that all Jules paths are already callback-driven or Temporal-backed.

That means the safe current posture for Jules is:

- polling-capable
- callback-ready in architecture
- callback-optional, not callback-required

### 11.2 Temporal target posture

When a Temporal-backed Jules path is implemented, the preferred posture should still follow the shared design:

- callback-first when Jules supports reliable callbacks
- timer-driven polling fallback when callbacks are absent, late, or untrusted
- terminal-state latch so callback and poll races cannot double-complete the workflow

### 11.3 Current default for `callback_supported`

Unless a concrete, verified Jules callback ingestion endpoint exists, the Jules start activity should default to:

- `callback_supported = false`

### 11.4 Poll timing

Until provider-specific evidence suggests otherwise, Jules should inherit the generic integration polling policy:

- start with a short delay
- back off with jitter
- cap steady-state polling conservatively
- reset backoff when provider status materially changes

The exact defaults should live in config or activity code, not be duplicated in UI logic.

---

## 12) ExternalEvent contract for Jules

This section defines the Jules-specific use of MoonMind's generic `ExternalEvent` signal.

### 12.1 Current state

The repo's generic Temporal design already defines `ExternalEvent` as the entry point for async provider notifications. Jules should use that signal shape if and when inbound callbacks are implemented.

### 12.2 Jules-specific payload expectations

When a Jules callback path exists, the signal payload should include at minimum:

- `source = jules`
- `event_type`
- `external_operation_id` (Jules `taskId`)
- optional `provider_event_id`
- optional `normalized_status`
- optional `provider_status`
- `observed_at`
- optional `payloadArtifactRef`

### 12.3 Current constraint

This document does **not** claim that Jules callbacks are already wired end-to-end in the repo. It only locks the contract that any future callback implementation must follow.

### 12.4 Trust and dedupe rules for future callbacks

If MoonMind adds a Jules callback ingress path, it should follow these rules:

- verify provider authenticity in the API ingress layer before signaling Temporal; do not pass unauthenticated callback bodies into workflow logic
- dedupe on a bounded provider event identity such as `provider_event_id + external_operation_id` when Jules exposes one
- store raw callback payloads as restricted artifacts when they need to be retained; signal workflows with bounded metadata only
- treat callback delivery as advisory until correlation and authenticity checks succeed

---

## 13) Jules artifact contract

Jules data should follow MoonMind's general artifact discipline: keep workflow state compact and move large or volatile payloads into artifacts.

### 13.1 Recommended Jules artifact classes

Recommended artifact categories for Jules-backed monitoring:

- create/start request snapshot
- task status snapshot
- final result snapshot
- resolution summary / completion notes
- raw callback payload (future, if callbacks exist)
- diagnostics / failure summary

### 13.2 Minimum requirement

At minimum, a Temporal-backed Jules integration should artifact:

- the terminal Jules task snapshot
- a failure summary artifact when Jules reaches terminal failure or unsupported cancellation matters

### 13.3 Artifact linking

Jules artifacts should link back to the owning workflow execution using the same execution-link contract used by the general Temporal artifact system.

### 13.4 Backend and storage posture

For Temporal-backed Jules flows, artifact storage should follow the Temporal artifact system design:

- default backend is the Temporal artifact store contract, which is MinIO / S3-compatible by default for MoonMind local and Compose deployments
- legacy filesystem artifact roots such as `var/artifacts/...` remain relevant for non-Temporal Celery/system paths, but they are not the canonical backend for this contract

### 13.5 History discipline

Do not put large Jules payloads, raw event bodies, or verbose status dumps into workflow history or memo fields.

When retaining raw callback bodies or provider snapshots that may contain sensitive operational detail, prefer restricted or preview-only artifact presentation rather than embedding raw payloads into task detail APIs by default.

---

## 14) Security and error-handling rules

### 14.1 Secrets

- Jules credentials belong only in approved config / secret handling paths
- bearer tokens must never appear in workflow history, memo, artifacts, or exception text
- worker/API logs should only use scrubbed error strings

### 14.2 Error handling

The Jules adapter already establishes the basic transport error rules:

- retry transient failures and rate limits
- fail fast on non-retryable `4xx`
- surface structured failure metadata for API error mapping

Temporal activity implementations should preserve those semantics rather than inventing a second retry policy that disagrees with the adapter.

### 14.3 Cancellation truthfulness

If MoonMind cancels a workflow while a Jules task is in flight:

- MoonMind may still close the workflow as canceled
- provider-side cancellation must only be reported as performed if Jules actually exposes and accepts a cancel operation

---

## 15) API and UI compatibility notes

During migration, MoonMind may continue exposing task-oriented product surfaces while Temporal owns durable orchestration underneath.

For Jules-backed work specifically:

- UI/API compatibility layers may still show task-style labels
- the durable provider handle remains Jules `taskId`
- the durable MoonMind orchestration handle remains the workflow execution identity
- for Temporal-backed compatibility rows, `taskId` should continue to follow the Visibility/UI bridge and resolve to the Temporal `workflowId`, not the Jules provider `taskId`
- any compatibility row or detail model must preserve the distinction between MoonMind task/workflow identity and Jules provider identity

Do not let the dashboard treat Jules `taskId` as the primary durable MoonMind execution identity.

---

## 16) Open decisions to lock next

1. What exact Jules provider status values should be mapped to `queued`, `running`, `failed`, and `canceled` once observed in production or test traffic?
2. Does Jules support a reliable signed callback/webhook path that MoonMind should formalize?
3. Should MoonMind embed correlation metadata into Jules `metadata` by default, or only for callback-capable flows?
4. If Jules later exposes richer outputs (logs, patches, artifacts), should `integration.jules.fetch_result` remain a single activity or split into multiple output activities?
5. Should provider-specific rate limiting for Jules stay inside the worker process, or does it need a dedicated queue only after usage grows?

---

## 17) Summary

The Jules integration should be treated as **the first provider-specific implementation profile** for MoonMind's Temporal external-monitoring architecture.

The important contract stance is:

- reuse the current Jules adapter and schemas as the transport baseline
- preserve MoonMind-owned correlation and normalized status semantics
- treat polling as required today unless verified Jules callbacks exist
- move durable waiting, retries, and long-lived state into Temporal
- keep provider-specific details in this document, not in the shared integrations design

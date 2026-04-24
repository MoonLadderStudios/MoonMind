# Jules Temporal External Event Contract

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: **Implemented core contract; callback ingress remains future-facing**
Owner: MoonMind Platform
Last updated: 2026-03-30
Audience: backend, workflow, API, worker, and dashboard implementers

---

## 1. Purpose

Define the **Jules-specific Temporal integration contract** for MoonMind.

This document exists to translate MoonMind’s provider-neutral external-agent rules into a concrete Jules profile without polluting the shared design with provider-specific details.

This document covers:

- how MoonMind identifies and correlates Jules operations
- the Jules adapter and Temporal activity surface
- the canonical runtime contract boundary for Jules
- how polling, callbacks, artifacts, and status normalization should behave for Jules
- what is implemented now vs what remains future-facing

This document does **not** define a separate Jules-only execution model. Shared external-agent architecture lives in:

- `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`

If this document conflicts with those shared docs, the shared docs win unless this file explicitly narrows Jules behavior.

---

## 2. Related docs

This document builds on and must stay consistent with:

- `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`
- `docs/ExternalAgents/JulesAdapter.md`
- `docs/ExternalAgents/AddingExternalProvider.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`

---

## 3. Scope and non-goals

## 3.1 In scope

- Jules provider naming and identifier mapping
- Jules runtime gating requirements
- the Jules-specific Temporal activity contract
- canonical runtime contract rules for Jules
- Jules polling posture and future callback posture
- artifact and correlation behavior for Jules operations
- how Jules-specific events map into MoonMind-owned lifecycle state

## 3.2 Out of scope

- redesigning MoonMind’s generic external integrations model
- redefining the full MoonMind status taxonomy outside Jules narrowing
- defining the entire dashboard presentation contract
- claiming Jules callback ingress is already fully implemented end to end

---

## 4. Current repo baseline

MoonMind contains the following Jules integration components:

- **Configuration:** typed Jules settings in `moonmind/config/jules_settings.py`
- **Schemas:** typed request/response models in `moonmind/schemas/jules_models.py`, including `normalize_jules_status()`
- **HTTP client:** async client in `moonmind/workflows/adapters/jules_client.py`
- **Agent adapter:** `JulesAgentAdapter` in `moonmind/workflows/adapters/jules_agent_adapter.py`
- **MCP tooling:** `JulesToolRegistry` in `moonmind/mcp/jules_tool_registry.py`
- **Runtime gating:** helpers in `moonmind/jules/runtime.py`
- **Temporal activities:** Jules integration activities registered in `moonmind/workflows/temporal/activity_catalog.py`
- **Worker fleet:** Jules capability on the integrations fleet

All true agent execution now flows through `MoonMind.AgentRun`, dispatched per plan step from `MoonMind.Run` when `tool.type == "agent_runtime"`.

### Current execution stance

For Jules, MoonMind uses this posture:

- Jules is a **poll-oriented external provider**
- agent execution runs through `MoonMind.AgentRun`
- normal multi-step workflow progression should prefer **one-shot bundled execution briefs**
- `sendMessage` is reserved for clarification/intervention exception flows
- polling is required today
- callback ingress is future-facing and optional, not required for correctness

---

## 5. Canonical Jules provider identity

## 5.1 Provider name

The canonical provider name is:

- `jules`

This value should be used anywhere MoonMind stores provider identity in:

- workflow state
- artifacts
- bounded visibility attributes
- provider metadata
- API compatibility layers

## 5.2 External operation handle

For Jules, the provider-side durable handle is:

- `external_operation_id = taskId` or Jules-equivalent session ID from the provider response

MoonMind must preserve the distinction between:

- **MoonMind workflow identity** — the durable orchestration identity
- **Jules provider identity** — the provider-side task/session identifier

Jules `taskId` is not the primary MoonMind execution identity.

## 5.3 Additional provider fields

Jules provider responses may include compact provider-specific fields such as:

- raw provider status
- provider URL
- provider tracking reference
- PR URL or related publication metadata

These belong in canonical `metadata`, not in workflow-facing ad hoc top-level payload shapes.

---

## 6. Configuration and feature gating

Jules support is feature-gated and must not be assumed available just because the code exists.

## 6.1 Required configuration

Jules support depends on:

- `JULES_ENABLED`
- `JULES_API_URL`
- `JULES_API_KEY`

Optional operational controls include:

- `JULES_TIMEOUT_SECONDS`
- `JULES_RETRY_ATTEMPTS`
- `JULES_RETRY_DELAY_SECONDS`

## 6.2 Runtime gate behavior

The canonical Jules runtime gate rules are:

- `targetRuntime=jules` is only valid when Jules is enabled and configured
- when the runtime gate is not satisfied, Jules-backed execution must fail early
- Jules MCP tools should be excluded from discovery when Jules is disabled or missing credentials

## 6.3 Operational rule

Any new Temporal worker, API ingress, callback handler, or dashboard control that depends on Jules must obey the same runtime gate. Do not create a second Jules enablement flag for a Temporal-only path.

---

## 7. Canonical runtime contract boundary

The most important Jules contract rule is:

> Normalize Jules-native transport payloads into MoonMind canonical runtime contracts before they reach workflow code.

That means the workflow-facing Jules activity contract is:

- `integration.jules.start(...) -> AgentRunHandle`
- `integration.jules.status(...) -> AgentRunStatus`
- `integration.jules.fetch_result(...) -> AgentRunResult`
- `integration.jules.cancel(...) -> AgentRunStatus`

## 7.1 What is allowed

Jules-specific details may appear in canonical `metadata`, for example:

- `providerStatus`
- `normalizedStatus`
- `externalUrl`
- `trackingRef`
- `pullRequestUrl`
- `callbackSupported`
- clarification/question metadata

## 7.2 What is not allowed

Do not rely on workflow-facing top-level payloads such as:

- `{external_id, tracking_ref}`
- raw Jules-native status dicts
- provider-shaped result dicts that `MoonMind.AgentRun` must coerce

The Jules adapter or Jules activities must own that normalization.

## 7.3 Unsupported status handling

If Jules emits a provider status that MoonMind cannot map into the canonical runtime state model, that is a non-retryable contract failure such as `UnsupportedStatus`.

Workflow code should not attempt to repair unknown Jules statuses on the fly.

---

## 8. Jules Temporal activity contract

Jules follows the generic external provider activity naming scheme.

## 8.1 Activity names

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`
- `integration.jules.cancel`

Default routing:

- `mm.activity.integrations`

MoonMind should not create a Jules-specific task queue by default. Split queues only if secrets, quotas, or scaling requirements demand it.

## 8.2 `integration.jules.start`

### Purpose

Start a Jules task/session and return a canonical run handle.

### Input

Canonical input is based on `AgentExecutionRequest` and may include:

- `correlation_id`
- `idempotency_key`
- instructions and context refs
- publish-related parameters
- safe provider metadata hints

### Output

Must return `AgentRunHandle` with canonical fields such as:

- `run_id`
- `agent_kind`
- `agent_id`
- `status`
- `started_at`
- `poll_hint_seconds`
- `metadata`

Jules-specific metadata may include:

- `providerStatus`
- `normalizedStatus`
- `externalUrl`
- `callbackSupported`

### Implementation rules

- preserve MoonMind correlation metadata
- treat the Jules provider handle as the canonical `run_id` for the external provider path
- default `callbackSupported` to false unless a verified callback ingress exists
- keep idempotency behavior stable across activity retries

## 8.3 `integration.jules.status`

### Purpose

Read current Jules task/session state during polling or reconciliation.

### Output

Must return `AgentRunStatus`.

This status should preserve:

- raw provider status in metadata
- normalized MoonMind runtime status in `status`
- provider deep links in metadata where available

### Implementation rules

- read from the Jules provider using the provider handle
- treat this as a read-only, strongly retryable activity
- do not let UI code own Jules status interpretation
- reuse one central Jules normalization path

## 8.4 `integration.jules.fetch_result`

### Purpose

Fetch or materialize the terminal Jules result into a canonical result envelope.

### Output

Must return `AgentRunResult`.

Representative fields:

- `output_refs[]`
- `summary`
- `diagnostics_ref`
- `failure_class`
- `provider_error_code`
- `metadata`

### Implementation rules

- preserve the terminal Jules snapshot where useful
- publish provider result snapshots as artifacts when large or operationally important
- include PR-related metadata when Jules creates a PR
- if rich result/download APIs are limited, a conservative snapshot-style result is acceptable as long as the top-level contract is canonical

## 8.5 `integration.jules.cancel`

### Purpose

Best-effort provider cancellation.

### Output

Must return `AgentRunStatus`.

### Truthfulness rule

MoonMind may cancel the workflow regardless of provider behavior, but provider-side cancellation must only be reported as successful if Jules actually accepts it.

If Jules rejects cancellation, the returned status/metadata should reflect that truthfully rather than pretending hard cancel succeeded.

---

## 9. Jules correlation contract

MoonMind requires a stable `correlation_id` that survives retries and Continue-As-New.

For Jules-backed work, MoonMind should track:

- `integration_name = jules`
- `correlation_id`
- `external_operation_id` (Jules task/session ID)
- `provider_status`
- `normalized_status`
- optional `external_url`

## 9.1 Callback correlation key

Because current Jules callback/webhook support is not yet a locked live ingress path in MoonMind, `callback_correlation_key` remains future-facing.

If Jules later supports reliable callbacks, MoonMind should prefer a stable callback correlation token that resolves back to the durable MoonMind correlation record rather than relying on visibility scans.

## 9.2 Jules metadata use

The Jules provider request surface supports optional metadata. MoonMind may use that field for safe, non-secret correlation hints.

Rules:

- never store secrets in Jules metadata
- prefer stable MoonMind correlation values over ephemeral runtime IDs
- do not rely on provider metadata as the sole source of truth

---

## 10. Jules status normalization contract

Jules provider status is opaque provider data until normalized.

## 10.1 Locked rule

Always preserve the raw Jules status as provider metadata, for example:

- `providerStatus`

## 10.2 Canonical normalized target

Jules statuses must normalize into MoonMind’s canonical runtime state set, such as:

- `queued`
- `running`
- `awaiting_feedback`
- `awaiting_approval`
- `intervention_requested`
- `completed`
- `failed`
- `canceled`
- `timed_out`

## 10.3 Ownership rule

The mapping from Jules provider status to MoonMind canonical runtime status belongs in the Jules integration implementation, not in:

- generic workflow code
- dashboard code
- API presentation compatibility code

MoonMind should reuse one central Jules normalization path so transport, activities, and optional tooling do not drift.

## 10.4 MoonMind-owned states

Some runtime states are MoonMind-owned outcomes rather than Jules-native provider states.

Important example:

- `intervention_requested`

MoonMind may use `intervention_requested` when:

- clarification requires operator help
- auto-answer is disabled or exhausted
- branch publication failed and requires manual review
- verification failed and requires a human decision

---

## 11. Polling and callback posture for Jules

## 11.1 Current posture

The safe current posture for Jules is:

- polling-capable
- callback-ready in architecture
- callback-optional, not callback-required

## 11.2 Preferred target posture

If Jules callback ingress is added later, the preferred posture remains:

- callback-first when reliable
- polling fallback when callbacks are absent, late, or untrusted
- terminal-state latch so callback and polling races cannot double-complete the workflow

## 11.3 `callbackSupported` default

Unless a concrete, verified Jules callback ingress endpoint exists, Jules start behavior should default to:

- `callbackSupported = false`

## 11.4 Poll timing

Jules polling should follow the generic integration polling posture:

- short initial delay
- backoff with jitter
- capped steady-state polling
- backoff reset when provider status materially changes

Timing defaults belong in code/config, not duplicated in UI logic.

---

## 12. External event contract for future callbacks

MoonMind’s generic async external event model should be used if Jules callback ingress is implemented.

## 12.1 Current state

This document does **not** claim that Jules callbacks are already wired end to end. It only defines the contract any future callback implementation should follow.

## 12.2 Expected signal payload shape

When Jules callback ingress exists, the external event payload should include at minimum:

- `source = jules`
- `event_type`
- `external_operation_id`
- optional `provider_event_id`
- optional `normalized_status`
- optional `provider_status`
- `observed_at`
- optional `payloadArtifactRef`

## 12.3 Trust and dedupe rules

If MoonMind adds Jules callback ingress, it should follow these rules:

- verify provider authenticity before signaling workflows
- dedupe on a bounded provider event identity when available
- store raw callback payloads as artifacts when retention is needed
- signal workflows with bounded metadata only
- treat callback delivery as advisory until authenticity and correlation checks succeed

---

## 13. Jules artifact contract

Jules data should follow MoonMind’s general artifact discipline: keep workflow state compact and move large or volatile payloads into artifacts.

## 13.1 Recommended artifact classes

Recommended Jules artifact categories include:

- start request snapshot
- status snapshot
- final result snapshot
- resolution or completion summary
- raw callback payload (future, if callbacks exist)
- diagnostics or failure summary
- bundle manifest or checklist context for one-shot Jules runs

## 13.2 Minimum requirement

At minimum, a Temporal-backed Jules integration should artifact:

- the terminal Jules task/session snapshot
- a failure summary artifact when Jules reaches terminal failure or when MoonMind-owned post-run publication/verification fails

## 13.3 Artifact linking

Jules artifacts should link back to the owning workflow execution using the same execution-link contract as the rest of the Temporal artifact system.

## 13.4 History discipline

Do not put large Jules payloads, raw callback bodies, or verbose status dumps into workflow history.

When retaining raw provider payloads or operationally sensitive details, prefer restricted artifacts over embedding raw data into task detail APIs by default.

---

## 14. Jules one-shot bundle posture

For multi-step Jules work, MoonMind should prefer one-shot bundled execution rather than repeated provider-driven step progression through follow-up messages.

## 14.1 Execution rule

For compatible Jules-targeted work:

- MoonMind bundles the work into one synthetic execution node
- compiles one checklist-shaped execution brief
- starts one Jules session
- polls that session to completion
- fetches one final result
- handles one publish outcome boundary

## 14.2 Why this matters to the contract

This means:

- one idempotency boundary per bundled Jules run
- one provider run handle
- one terminal result contract
- one artifact/result/publication boundary

`sendMessage` remains valid for clarification and intervention, but it should not be the normal step-to-step progression mechanism for multi-step MoonMind work.

---

## 15. Security and error-handling rules

## 15.1 Secrets

- Jules credentials belong only in approved secret/config paths
- bearer tokens must never appear in workflow history, artifacts, or exception text
- worker and API logs should use scrubbed error strings

## 15.2 Error handling

Jules transport and integration behavior should preserve the basic policy:

- retry transient failures and rate limits
- fail fast on non-retryable 4xx
- treat unsupported provider statuses as non-retryable contract failures
- keep workflow code free of Jules payload repair logic

## 15.3 Cancellation truthfulness

If MoonMind cancels a workflow while a Jules run is in flight:

- MoonMind may still close the workflow as canceled
- provider-side cancellation must only be reported as performed if Jules actually accepts it

---

## 16. API and UI compatibility notes

During migration and compatibility work, MoonMind may continue exposing task-oriented product surfaces while Temporal owns durable orchestration underneath.

For Jules-backed work:

- UI/API layers may still show task-style labels
- the durable provider handle remains the Jules task/session ID
- the durable MoonMind orchestration handle remains the workflow identity
- compatibility layers must preserve the distinction between MoonMind execution identity and Jules provider identity

Dashboard code must not treat the Jules provider handle as the primary durable MoonMind execution identity.

---

## 17. Open decisions

The following remain worth locking more explicitly as the integration evolves:

1. What exact Jules provider statuses are observed in production and should remain first-class in the normalization table?
2. Does Jules support a reliable signed callback/webhook path MoonMind should formalize?
3. Should MoonMind always embed correlation hints in Jules metadata, or only for callback-capable flows?
4. If Jules later exposes richer outputs, should `integration.jules.fetch_result` remain a single activity or split further?
5. Are there any scaling/quoting reasons to give Jules a dedicated queue in the future?

---

## 18. Summary

Jules should be treated as the reference poll-based provider profile for MoonMind’s Temporal external-agent architecture.

The important contract stance is:

- reuse the current Jules transport and adapter baseline
- preserve MoonMind-owned correlation and canonical status semantics
- keep polling as the required correctness path today
- treat callbacks as future-facing and optional
- return canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` from the Jules Temporal activities
- keep provider-specific details in Jules metadata and Jules-specific docs, not in generic workflow code
- prefer one-shot bundled execution for multi-step Jules work

# Technical Design: Jules Provider Adapter

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: **Implemented as reference poll-based provider**
Last updated: 2026-03-30
Related:
- [`./ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md)
- [`./AddingExternalProvider.md`](./AddingExternalProvider.md)
- [`../Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`../Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`../Temporal/ErrorTaxonomy.md`](../Temporal/ErrorTaxonomy.md)

---

## 1. Objective

Describe Jules as a provider-specific implementation of MoonMind’s shared external-agent architecture.

This document intentionally does **not** define a separate Jules-only execution model. The canonical shared models live in:

- [`ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md)
- [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

This file narrows those shared designs for one provider: Jules.

### Important references

When working on the Jules adapter, consult the official Jules documentation:

- [Jules API Reference](https://developers.google.com/jules/api/reference/rest)
- [Jules Sessions Reference](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions)

---

## 2. Jules in the canonical external-agent stack

Jules should be understood using the same five-part model used for every external agent:

1. configuration and runtime gate
2. provider transport
3. universal external agent adapter
4. workflow orchestration
5. optional tooling surfaces

This document focuses primarily on Layers 1, 2, and the Jules-specific parts of Layer 3.

---

## 3. Jules provider components

## 3.1 Configuration and runtime gate

Jules configuration lives behind typed settings and runtime-gate helpers.

Primary pieces:

- `moonmind/config/jules_settings.py`
- `moonmind/jules/runtime.py`
- `moonmind/config/settings.py`

Representative environment variables:

- `JULES_API_URL`
- `JULES_API_KEY`
- `JULES_ENABLED`
- `JULES_TIMEOUT_SECONDS`
- `JULES_RETRY_ATTEMPTS`
- `JULES_RETRY_DELAY_SECONDS`

This layer answers whether Jules is enabled and safe to use for execution or tooling.

## 3.2 Provider transport

Jules transport is provider-specific and should stay thin.

Primary pieces:

- `moonmind/schemas/jules_models.py`
- `moonmind/workflows/adapters/jules_client.py`

Transport responsibilities:

- define Jules request and response schemas
- speak the Jules HTTP API
- handle auth headers
- apply transport retries and timeouts
- scrub secrets from raised errors
- expose thin provider-native operations

The transport layer is not the workflow lifecycle.

## 3.3 Jules provider adapter

Jules plugs into the shared external-agent boundary through:

- `moonmind/workflows/adapters/jules_agent_adapter.py`

This adapter implements the shared `AgentAdapter` contract and should be treated as the Jules-specific subclass of MoonMind’s universal external adapter pattern.

Current responsibilities include:

- translate `AgentExecutionRequest` into Jules session/task creation payloads
- inject MoonMind correlation and idempotency metadata
- normalize Jules statuses into canonical MoonMind runtime states
- map Jules responses into `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`
- provide truthful best-effort cancellation behavior
- set `automationMode` to `AUTO_CREATE_PR` when `publishMode` is `pr` or `branch`

## 3.4 Workflow orchestration

Jules execution is orchestrated by generic workflow code, not by provider-specific orchestration.

Primary pieces:

- `MoonMind.Run`
- `MoonMind.AgentRun`

Those workflows should remain provider-neutral and select Jules only through adapter dispatch and capability metadata.

## 3.5 Optional tooling surfaces

Jules also has optional tooling/operator surfaces such as:

- `moonmind/mcp/jules_tool_registry.py`

These are useful, but they are not the core execution architecture. They should consume the same transport, runtime-gate, and normalization rules rather than redefining them.

---

## 4. Canonical contract boundary

The most important rule for the Jules adapter is:

> Normalize Jules-native payloads into MoonMind canonical runtime contracts before they reach workflow code.

That means the workflow-facing contract surface must be:

- `start(...) -> AgentRunHandle`
- `status(...) -> AgentRunStatus`
- `fetch_result(...) -> AgentRunResult`
- `cancel(...) -> AgentRunStatus`

## 4.1 What is allowed

Jules-specific details may appear inside canonical `metadata`.

Examples:

- `providerStatus`
- `normalizedStatus`
- `externalUrl`
- `trackingRef`
- callback support hints
- PR URLs
- clarification-related metadata

## 4.2 What is not allowed

Do not rely on workflow-facing top-level payloads such as:

- `{external_id, tracking_ref}`
- raw Jules-native status dicts
- provider-shaped result dicts that `MoonMind.AgentRun` must coerce
- alternate top-level Jules fields outside canonical contracts

The Jules adapter or Jules integration activities must own this normalization.

## 4.3 Unsupported status handling

If Jules emits a provider state that MoonMind does not support, the Jules boundary should raise a non-retryable contract/status error such as `UnsupportedStatus`.

Workflow code should not silently paper over unknown Jules statuses.

---

## 5. Jules transport design

## 5.1 Schema definitions

Jules schema models live in `moonmind/schemas/jules_models.py`.

Representative core models include:

- `JulesCreateTaskRequest`
- `JulesResolveTaskRequest`
- `JulesGetTaskRequest`
- `JulesTaskResponse`

This module also owns Jules-specific status normalization via `normalize_jules_status()`.

That normalizer is the Jules-specific source of truth for raw provider status mapping and should be reused consistently anywhere Jules statuses are interpreted below the canonical workflow boundary.

## 5.2 Jules async client

`JulesClient` in `moonmind/workflows/adapters/jules_client.py` is the low-level HTTP transport wrapper.

Current design goals:

- long-lived `httpx.AsyncClient`
- constructor-driven timeout and retry settings
- manual retry loop where needed
- testability through client injection
- scrubbed `JulesClientError` failures

Representative public operations include:

- `create_task()` or provider-equivalent session creation
- `resolve_task()`
- `get_task()`
- `list_activities()`
- `send_message()`
- `merge_pull_request()`
- `update_pull_request_base()`

This layer should remain transport-oriented and should not accumulate workflow semantics such as:

- Temporal wait behavior
- artifact publishing semantics
- bundle orchestration semantics
- parent/child workflow coordination
- MoonMind task-state transitions

---

## 6. Jules adapter design

## 6.1 Role of `JulesAgentAdapter`

`JulesAgentAdapter` bridges:

- MoonMind canonical runtime contracts
- Jules-native transport calls

It translates between:

- `AgentExecutionRequest` → Jules provider request payload
- Jules transport response → `AgentRunHandle`
- Jules transport response → `AgentRunStatus`
- Jules transport response → `AgentRunResult`

## 6.2 Shared behaviors

The adapter should follow the same shared behaviors expected of all external providers:

- validate `agent_kind == "external"`
- validate provider identity
- preserve stable idempotency behavior
- inject MoonMind correlation metadata
- assemble canonical metadata fields such as:
 - `providerStatus`
 - `normalizedStatus`
 - `externalUrl`
- return canonical runtime contracts only

These behaviors should remain aligned with `BaseExternalAgentAdapter`.

## 6.3 Jules-specific behaviors

The following logic should remain Jules-specific:

- shaping MoonMind instructions into Jules task/session payloads
- deriving fallback title/description text when needed
- Jules status normalization aliases
- Jules-specific result-summary construction
- Jules-specific cancel behavior through the Jules API
- optional clarification/user-feedback flows
- Jules-specific PR and merge metadata extraction

---

## 7. Temporal activity surface for Jules

Jules is a standard poll-oriented external provider and should expose the standard integration lifecycle activities:

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`
- `integration.jules.cancel`

These activities are expected to return canonical runtime contracts:

- `integration.jules.start(...) -> AgentRunHandle`
- `integration.jules.status(...) -> AgentRunStatus`
- `integration.jules.fetch_result(...) -> AgentRunResult`
- `integration.jules.cancel(...) -> AgentRunStatus`

Additional Jules-specific helper activities may exist when Jules product semantics require them, for example:

- `integration.jules.list_activities`
- `integration.jules.answer_question`
- `integration.jules.get_auto_answer_config`
- `integration.jules.send_message`

Those are provider-specific extensions, not replacements for the core lifecycle set.

---

## 8. Branch publish auto-merge

## 8.1 Problem

The Jules API only supports `AUTO_CREATE_PR` as an automation mode. There is no true “branch only” mode.

That means when a user requests `publishMode == "branch"`, MoonMind must treat "land changes directly on the authored branch" as a MoonMind-owned post-completion outcome rather than a Jules-native publish mode.

Jules receives a single authored `branch` reference. MoonMind no longer assumes authored inputs provide both a base branch and a target branch. For PR mode, `branch` is the PR base and Jules/provider automation manages work/head branch creation.

## 8.2 Mechanism

MoonMind handles branch publication with a post-completion flow:

1. Jules creates a PR using `AUTO_CREATE_PR`
2. MoonMind waits for terminal completion
3. MoonMind fetches the Jules result and extracts the PR URL
4. MoonMind verifies or corrects the PR base so it targets the authored `branch` when the provider result does not already do so
5. MoonMind merges the PR through `repo.merge_pr`

This lets MoonMind support:

- `publishMode == "pr"` → leave PR open
- `publishMode == "branch"` → merge changes into the authored branch

## 8.3 Workflow flow

When `publishMode == "branch"` and the provider is Jules:

1. `JulesAgentAdapter.do_start()` sets `automationMode = "AUTO_CREATE_PR"`
2. `MoonMind.AgentRun` waits for Jules to reach terminal completion
3. `integration.jules.fetch_result` returns a canonical `AgentRunResult` containing PR metadata
4. MoonMind verifies or corrects the PR base branch against the single authored `branch`
5. MoonMind calls `repo.merge_pr`
6. the final MoonMind result reflects whether branch publication actually succeeded

## 8.4 Truthfulness rule

If `publishMode == "branch"`, MoonMind must not report success unless the changes actually land on the authored branch.

That means these failures must prevent a successful branch-publication outcome:

- no PR URL could be extracted
- base-branch update failed
- merge failed
- verification of landed branch changes failed
- transport failure prevented the post-run publish phase from completing

Jules provider success is not enough on its own for MoonMind-owned branch publication success.

---

## 9. One-shot bundled execution

## 9.1 Why Jules should not be treated as a normal multi-turn step worker

Jules has proven fragile when driven through repeated follow-up turns for normal multi-step workflow progression.

Common failure modes include:

- later prompts losing earlier durable intent
- session brittleness after intermediate clarification or state changes
- fragmented local reasoning instead of one coherent repo-wide plan
- excessive provider-specific choreography in MoonMind

For Jules, the better model is:

> compile the whole Jules-targeted work into one cohesive execution brief and run one Jules session.

## 9.2 Standard Jules execution rule

For multi-step Jules work, MoonMind should prefer:

- one compiled brief
- one Jules session
- one `MoonMind.AgentRun` child workflow
- one provider result boundary
- one publish outcome boundary

It should avoid using `sendMessage` as the normal way to drive step-by-step workflow progression.

## 9.3 Lifecycle shape

```text id="01026"
MoonMind plan
 ↓ identify Jules-targeted work
 ↓ bundle compatible ordered steps
 ↓ compile one-shot execution brief
 ↓ integration.jules.start
 ↓ Jules runs one cohesive session
 ↓ MoonMind polls / processes callbacks
 ↓ integration.jules.fetch_result
 ↓ optional branch publication steps
 ↓ final MoonMind outcome
````

## 9.4 Bundling rule

For Jules, MoonMind should bundle **compatible ordered work** into one synthetic Jules execution node when the work shares the same:

* repository
* workspace or branch context
* publish mode
* high-level objective
* runtime/provider requirement

MoonMind should not bundle across boundaries such as:

* different repositories
* different runtime/auth requirements
* required human approval boundaries
* steps that depend on not-yet-existing artifacts
* incompatible side-effect profiles

When bundling is unsafe, MoonMind should emit multiple synthetic Jules execution nodes rather than revert to normal step-driving through repeated `sendMessage` turns.

## 9.5 One-shot brief design

The one-shot Jules brief should be:

* specific
* ordered
* checklist-shaped
* low-ambiguity
* outcome-oriented
* explicit about constraints and validation

It should generally contain:

1. mission
2. repository/workspace context
3. execution rules
4. ordered checklist
5. validation checklist
6. final response requirements

This helps Jules produce one coherent implementation plan instead of a fragmented sequence of local reactions.

## 9.6 Division of responsibility

### Workflow/planner side

MoonMind orchestration should:

* decide which steps are bundled
* compile the one-shot brief
* persist any bundle manifest metadata
* create one `AgentExecutionRequest`

### Jules adapter side

The Jules adapter should:

* translate the already-compiled request into Jules provider payloads
* preserve correlation metadata
* preserve idempotency behavior
* map status/result as normal

This keeps bundling as a MoonMind orchestration concern, not a Jules transport concern.

---

## 10. Clarification and auto-answer flows

Jules may enter a clarification or user-feedback state.

This remains an **exception path**, not the normal multi-step execution path.

## 10.1 Design rule

`sendMessage` remains valid for:

* clarification responses
* operator intervention
* explicit resume flows
* question auto-answer flows

`sendMessage` should **not** be the standard mechanism for advancing normal multi-step MoonMind workflow execution.

## 10.2 `intervention_requested` ownership rule

`intervention_requested` is a MoonMind-owned execution state, not a Jules-native provider status.

MoonMind may use `intervention_requested` when:

* Jules requires clarification and automation is disabled or exhausted
* branch publication fails and requires human review
* result completeness is insufficient for automatic acceptance
* verification fails and requires operator judgment

---

## 11. Relationship to the universal external adapter pattern

Jules should be the reference provider when extracting or refining the reusable external-adapter base.

### Shared base responsibilities

* idempotency guard behavior
* correlation metadata helpers
* common handle/status/result construction
* capability declaration shape
* default cancel fallback behavior
* canonical contract shaping

### Jules override responsibilities

* provider request payload creation
* provider response parsing
* provider status normalization
* provider result extraction
* provider cancel translation
* Jules-specific clarification paths
* Jules-specific PR metadata extraction

If a future provider needs materially different behavior, that divergence should be explicit and justified rather than silently bypassing the shared pattern.

---

## 12. MCP tooling posture

Earlier descriptions sometimes made Jules MCP tooling look like a separate architecture layer equal to the adapter and workflow lifecycle. That framing is misleading.

The correct posture is:

* MCP tooling is an optional consumer surface
* it should reuse Jules client, capability, and runtime-gate rules
* it should not become the source of truth for execution semantics
* it should not redefine Jules status normalization independently

`JulesToolRegistry` is therefore an adjunct surface, not the core Jules architecture.

---

## 13. Implementation guidance

To keep Jules aligned with the shared external-agent design and the canonical contract rule, practical implementation work should continue to follow these guidelines:

1. keep `JulesClient` focused on transport
2. keep canonical contract shaping in the adapter/activity boundary
3. remove any reliance on workflow-side Jules payload coercion
4. prefer one-shot bundled execution over repeated `sendMessage` workflow progression
5. preserve Jules-specific status normalization in one clear source of truth
6. keep workflow orchestration in `MoonMind.AgentRun`, not in Jules-specific code
7. ensure MCP, dashboard, and other surfaces reuse the same Jules normalization and runtime-gate logic
8. surface incomplete checklist items clearly in final summaries and metadata
9. keep branch publication truthful for `publishMode == "branch"`
10. preserve independent MoonMind verification for acceptance-critical runs

---

## 14. Summary

Jules should not be described as a special-case architecture and should not be treated as a general step-by-step conversational worker for normal multi-step workflow progression.

The correct standard is:

* Jules transport lives in schemas and `JulesClient`
* Jules runtime translation lives in `JulesAgentAdapter`
* Jules lifecycle activities return canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`
* MoonMind prefers one-shot bundled execution for multi-step Jules work
* `sendMessage` is reserved for clarification/intervention-style exception flows
* generic execution lifecycle lives in `MoonMind.AgentRun`
* optional tooling surfaces sit on top of the same provider boundaries

In short, Jules is the reference poll-based provider implementation of MoonMind’s universal external-agent adapter pattern.

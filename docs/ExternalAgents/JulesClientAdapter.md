# Technical Design: Jules Provider Adapter

## 1. Objective

Describe Jules as a provider-specific implementation of MoonMind's shared external-agent architecture.

### Important References
When working on the Jules adapter, consult the official documentation:
- [Jules API Reference](https://developers.google.com/jules/api/reference/rest)
- [Jules API Sessions Reference (Automation Mode Enum)](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions)

This document intentionally does **not** define a separate Jules-only integration model. The canonical shared model lives in:

- [`ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md)
- [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

This file narrows that shared design for one provider: Jules.

## 2. Jules in the Canonical External-Agent Stack

Jules should be understood through the same five-part model used for every external agent:

1. **Configuration and runtime gate**
2. **Provider transport**
3. **Universal external agent adapter**
4. **Workflow orchestration**
5. **Optional tooling surfaces**

This doc focuses primarily on Layers 1, 2, and the Jules-specific part of Layer 3.

## 3. Jules Provider Components

### 3.1 Configuration and Runtime Gate

Jules configuration lives behind typed settings and runtime gate helpers.

Primary pieces:

- `moonmind/config/jules_settings.py`
- `moonmind/jules/runtime.py`
- `moonmind/config/settings.py`

Environment variables:

- `JULES_API_URL`
- `JULES_API_KEY`
- `JULES_ENABLED`
- `JULES_TIMEOUT_SECONDS`
- `JULES_RETRY_ATTEMPTS`
- `JULES_RETRY_DELAY_SECONDS`

This layer answers whether Jules is enabled and safe to use for execution or tooling.

### 3.2 Provider Transport

Jules transport is provider-specific and should stay thin.

Primary pieces:

- `moonmind/schemas/jules_models.py`
- `moonmind/workflows/adapters/jules_client.py`

Transport responsibilities:

- define Jules request and response schemas
- speak the Jules HTTP API
- handle bearer auth
- retry `5xx`, `429`, transport errors, and timeouts
- fail fast on non-retryable `4xx`
- scrub secrets from raised error text

The transport layer is not the agent-runtime lifecycle. It is the provider client beneath the adapter.

### 3.3 Jules Provider Adapter

Jules plugs into the shared external-agent boundary through:

- `moonmind/workflows/adapters/jules_agent_adapter.py`

This adapter implements the shared `AgentAdapter` contract and should be treated as the Jules-specific subclass/profile of the universal external adapter pattern.

Current responsibilities:

- translate `AgentExecutionRequest` into Jules create-task payloads
- inject MoonMind correlation and idempotency metadata into Jules metadata
- normalize Jules status strings into canonical MoonMind run states
- map Jules task responses into `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`
- provide truthful best-effort cancellation behavior
- set `automationMode` to `AUTO_CREATE_PR` when `publishMode` is `"pr"` or `"branch"` so Jules creates a PR

### 3.4 Workflow Orchestration

Jules execution is orchestrated by the generic workflow layer, not by this provider document.

Primary pieces:

- `MoonMind.Run`
- `MoonMind.AgentRun`

These workflows should remain provider-neutral and select Jules only through adapter dispatch.

### 3.5 Optional Tooling Surfaces

Jules also exposes optional operator/agent-facing tooling:

- `moonmind/mcp/jules_tool_registry.py`

This is useful, but it is not part of the core execution architecture. It should consume the same Jules transport and runtime gate rules rather than re-defining them.

## 4. Jules Transport Design

### 4.1 Schema Definitions

Jules schema models live in `moonmind/schemas/jules_models.py`.

Current core models:

- `JulesCreateTaskRequest`
- `JulesResolveTaskRequest`
- `JulesGetTaskRequest`
- `JulesTaskResponse`

This module also owns Jules status normalization through `normalize_jules_status()`.

That normalizer is the Jules-specific source of truth for raw provider status mapping and should be reused everywhere Jules statuses are interpreted.

### 4.2 Jules Async Client

`JulesClient` in `moonmind/workflows/adapters/jules_client.py` is the low-level HTTP transport wrapper.

Current design:

- long-lived `httpx.AsyncClient`
- constructor-driven timeout and retry settings
- manual retry loop
- testable via optional client injection
- scrubbed `JulesClientError`

Public operations:

- `create_task()`
- `resolve_task()`
- `get_task()`
- `merge_pull_request()` — merges a Jules-created GitHub PR via the GitHub API (used for branch publish auto-merge)

This layer should remain transport-oriented and should not accumulate workflow semantics such as polling policy, Temporal wait behavior, or artifact publication.

## 5. Jules Adapter Design

### 5.1 Role of `JulesAgentAdapter`

`JulesAgentAdapter` is the provider adapter that bridges:

- MoonMind canonical runtime contracts
- Jules-native transport calls

It translates between:

- `AgentExecutionRequest` -> `JulesCreateTaskRequest`
- `JulesTaskResponse` -> `AgentRunHandle`
- `JulesTaskResponse` -> `AgentRunStatus`
- `JulesTaskResponse` -> `AgentRunResult`

### 5.2 Current Shared Behaviors

The current adapter already follows patterns that should become shared across all external providers:

- validate `agent_kind == "external"`
- validate provider identity
- maintain per-attempt idempotency cache
- inject MoonMind correlation metadata
- normalize common metadata fields such as:
  - `providerStatus`
  - `normalizedStatus`
  - `externalUrl`

These behaviors should eventually move into a reusable universal external-adapter base so provider adapters only override provider-specific translation.

### 5.3 Jules-Specific Behaviors

The following logic should remain Jules-specific:

- mapping MoonMind task inputs into Jules `title`, `description`, and `metadata`
- deciding how to derive a fallback description from instruction or artifact refs
- Jules status normalization aliases
- Jules-specific result summary construction
- Jules-specific cancel path via task resolution

## 6. Branch Publish Auto-Merge

### 6.1 Problem

The Jules API only supports `AUTO_CREATE_PR` as an automation mode — there is no "branch only" mode. When a user selects `publishMode: "branch"`, the intent is to land changes directly on a target branch, not to leave an open PR.

Additionally, the `startingBranch` field controls both where Jules starts its work **and** the PR's merge target. This means users cannot natively say "start from `main` but merge into `feature-branch`."

### 6.2 Mechanism

MoonMind solves both problems with a two-step post-completion flow:

1. **Base branch update** — If `targetBranch` is specified and differs from `startingBranch`, the PR's base branch is updated via `PATCH /repos/.../pulls/...` before merging.
2. **Auto-merge** — The PR is merged via `PUT /repos/.../pulls/.../merge`.

This decouples "where Jules works from" and "where changes should land."

### 6.3 Workflow Flow

When `publishMode == "branch"` and `integration == "jules"`:

1. **Adapter** — `JulesAgentAdapter.do_start()` sets `automation_mode = "AUTO_CREATE_PR"` for both `pr` and `branch` publish modes.
2. **Integration stage** — `MoonMind.Run._run_integration_stage()` polls Jules until the session reaches a terminal state.
3. **Fetch result** — On `succeeded`, the workflow calls `integration.jules.fetch_result` to get the session data and extracts the PR URL.
4. **Update base** *(conditional)* — If `targetBranch` is set and differs from `startingBranch`, the activity calls `JulesClient.update_pull_request_base()` to change the PR's base branch using the GitHub API.
5. **Auto-merge** — The workflow calls `integration.jules.merge_pr`. This activity delegates to `JulesClient.merge_pull_request()`, which merges the PR via the GitHub API.
6. **Result** — Changes land directly on the target branch. The Jules-created PR is closed automatically by GitHub upon merge.

```
Jules (AUTO_CREATE_PR, startingBranch=main)
  ↓ creates PR targeting main
  ↓ MoonMind polls until completed
  ↓ MoonMind extracts PR URL from session output
  ↓ [if targetBranch ≠ startingBranch] PATCH PR base → targetBranch
  ↓ PUT merge PR
  ↓ Changes land on targetBranch ✓
```

When `targetBranch` is the same as `startingBranch` (or not specified), the base-update step is skipped.

### 6.4 Error Handling

The auto-merge step is **best-effort**:

- If the PR URL cannot be extracted from the session result, the workflow logs a warning and continues.
- If the base-branch update fails (e.g., target branch does not exist), the merge is **not attempted** and the PR remains open for manual resolution.
- If the GitHub merge API returns an error (e.g., merge conflicts, branch protection rules), the merge fails gracefully and the PR remains open for manual resolution.
- Network or timeout errors are caught and logged without failing the workflow.

### 6.5 Configuration

- **`GITHUB_TOKEN`** — Required environment variable for the merge API call. Must have `repo` scope or sufficient permissions to update and merge PRs.
- **`startingBranch`** — Set via `workspaceSpec.startingBranch` or `workspaceSpec.branch` in the `AgentExecutionRequest`. Defaults to `main`. This is where Jules starts its work.
- **`targetBranch`** — Set via `parameters.targetBranch` or `workspaceSpec.targetBranch`. When set and different from `startingBranch`, the PR's base branch is updated before merging. When not set, the PR merges into `startingBranch`.
- **Merge method** — Currently defaults to `"merge"` (merge commit). Configurable in `JulesClient.merge_pull_request(merge_method=...)`.

### 6.6 Temporal Activities

| Activity | Queue | Purpose |
|----------|-------|---------|
| `integration.jules.merge_pr` | `mm.activity.integrations` | Optionally update PR base, then merge via GitHub API |

### 6.7 Schema

`JulesIntegrationMergePRResult` (in `moonmind/schemas/jules_models.py`):

| Field | Type | Description |
|-------|------|-------------|
| `pr_url` | `str` | The GitHub PR URL that was merged |
| `merged` | `bool` | Whether the merge succeeded |
| `merge_sha` | `str \| None` | SHA of the merge commit (if successful) |
| `summary` | `str` | Human-readable summary of the result |

### 6.8 Client Methods

| Method | Purpose |
|--------|---------|
| `JulesClient.merge_pull_request()` | `PUT` merge a GitHub PR |
| `JulesClient.update_pull_request_base()` | `PATCH` a PR's base (target) branch |

## 7. Multi-Step Workflow Execution

### 7.1 Problem

When a MoonMind plan contains multiple steps that target Jules, the current system creates an independent Jules session per step. Each session has no context of prior steps — it cannot see what was changed, why, or what the broader plan looks like. This leads to:

- No shared context between steps (step 2 does not know what step 1 did)
- One session per step (N billing events for an N-step plan)
- No accumulated file changes (each session starts from the same branch snapshot)
- Multiple independent PRs instead of one cohesive PR

### 7.2 Solution: `sendMessage`-Based Multi-Turn Sessions

The Jules API exposes a `sendMessage` endpoint that allows additional prompts to be sent to an existing session:

```
POST https://jules.googleapis.com/v1alpha/{session=sessions/*}:sendMessage
{ "prompt": string }
```

MoonMind uses this to treat a multi-step Jules plan as a **single long-lived session** with multiple turns:

1. **Step 1**: `sessions.create` with the first step's prompt → poll until `COMPLETED`
2. **Step N** (for each subsequent step): `sessions.sendMessage` with the next step's prompt → poll until `COMPLETED` again
3. **Final**: the session's accumulated changes produce one PR

This keeps Jules's full working context (file changes, conversation, reasoning) across all steps in a single session.

### 7.3 Session Lifecycle

```
Step 1: sessions.create(prompt=step_1_instructions)
  ↓ Jules: QUEUED → PLANNING → IN_PROGRESS → COMPLETED
  ↓ MoonMind detects COMPLETED, checks if more steps remain
  ↓
Step 2: sessions.sendMessage(prompt=step_2_instructions)
  ↓ Jules: COMPLETED → QUEUED/IN_PROGRESS → COMPLETED
  ↓ MoonMind detects COMPLETED, checks if more steps remain
  ↓
 ... (repeat for each remaining step)
  ↓
Final: all steps COMPLETED → mark workflow as succeeded
```

**Key state transitions:**

| Event | MoonMind action |
|-------|----------------|
| Session reaches `COMPLETED` and more steps remain | Call `sendMessage` with next step's prompt, resume polling |
| Session reaches `COMPLETED` and no steps remain | Mark workflow as succeeded, extract PR URL |
| Session reaches `FAILED` at any step | Mark workflow as failed, stop sending steps |
| Session reaches `AWAITING_USER_FEEDBACK` | Auto-answer sub-flow triggers in `MoonMind.AgentRun` (see §10); maps to `awaiting_feedback` |

### 7.4 Transport Layer

A new `send_message()` method on `JulesClient` handles the `sendMessage` API call:

| Method | API | Purpose |
|--------|-----|---------|
| `JulesClient.send_message()` | `POST /sessions/{id}:sendMessage` | Send follow-up prompt to existing session |

Request model:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | ID of the existing session |
| `prompt` | `str` | The next step's instructions |

The response body is empty on success. After calling `sendMessage`, the caller must resume polling `sessions.get` to track the session's state through its next execution cycle.

### 7.5 Adapter Layer

The adapter layer decides whether to use `create_task` or `send_message` based on whether the request includes a `session_id` from a prior step:

- **First step**: No `session_id` → `do_start()` calls `create_task()` → returns handle with the new `session_id`
- **Subsequent steps**: Has `session_id` → `do_continue()` calls `send_message()` → returns updated handle reusing the same `session_id`

### 7.6 Workflow Orchestration

Multi-step Jules dispatch is coordinated by `MoonMind.AgentRun` (or `MoonMind.Run`'s execution loop):

1. The workflow receives the ordered list of plan steps.
2. For the first Jules step, it dispatches via `integration.jules.start` as today.
3. After Jules completes step 1, the workflow checks whether more Jules steps remain in the plan.
4. If yes, it calls `integration.jules.send_message` with the next step's prompt and the existing `session_id`, then resumes polling.
5. This repeats until all steps complete or a step fails.
6. Only after the final step completes does the workflow mark the run as succeeded.

### 7.7 Error Handling

- **Step failure**: If Jules reaches `FAILED` at any step, the workflow stops sending further steps and marks the run as failed. The PR (if any) reflects partial progress.
- **`sendMessage` transport failure**: Retried using the same retry policy as `create_task`. If retries exhaust, the workflow fails.
- **Session not resumable**: If `sendMessage` returns a `4xx` error (session is in a non-resumable state), the workflow fails with a clear error.
- **Cancellation mid-step**: If the workflow is cancelled while waiting for Jules to complete an intermediate step, the existing cancel path via `resolve_task` is used.

### 7.8 Single-Step Backward Compatibility

When a plan has only one Jules step, the flow is identical to the current behavior — `create_task`, poll, fetch result. No `sendMessage` call is made.

## 8. Relationship to the Universal External Adapter Plan

Jules should be the reference provider when extracting a reusable external-adapter base.

That means future refactoring should aim to preserve this separation:

### Shared Base Responsibilities

- idempotency guard behavior
- correlation metadata helpers
- common handle/status/result metadata assembly
- capability declaration shape
- default cancel fallback behavior

### Jules Override Responsibilities

- provider request payload creation
- provider response parsing
- provider status normalization
- provider cancel translation

If a future provider requires materially different behavior, it should justify that divergence explicitly rather than silently bypassing the shared pattern.

## 9. MCP Tooling Posture

Earlier descriptions made Jules MCP tooling look like a separate architectural layer equal to the adapter and workflow lifecycle. That framing is misleading.

The correct posture is:

- MCP tooling is an optional consumer surface
- it should reuse Jules client and runtime-gate rules
- it should not become the source of truth for execution semantics

`JulesToolRegistry` is therefore an adjunct surface, not the core Jules architecture.

## 10. Question Auto-Answer

When Jules enters `AWAITING_USER_FEEDBACK`, the `MoonMind.AgentRun` polling loop detects this via the `awaiting_feedback` normalized status and initiates an automatic question-answer cycle:

1. **Detect** — status normalizer maps `awaiting_user_feedback` → `awaiting_feedback` (distinct from `running`)
2. **Extract** — calls `GET /sessions/{id}/activities` to find the latest `AgentMessaged` activity with `originator == "agent"`
3. **Answer** — builds a clarification prompt and sends it back via `POST /sessions/{id}:sendMessage`
4. **Resume** — sets run status back to `running` and continues polling

### 10.1 Scope

Auto-answer logic lives **exclusively** in `MoonMind.AgentRun` (`agent_run.py`). The parent `MoonMind.Run` workflow does not duplicate this logic — it delegates to `AgentRun` child workflows for all external agent execution.

### 10.2 Guardrails

| Guard | Env Variable | Default | Behavior |
|-------|-------------|---------|----------|
| Opt-out | `JULES_AUTO_ANSWER_ENABLED` | `true` | When `false`, maps to `intervention_requested` |
| Max cycles | `JULES_MAX_AUTO_ANSWERS` | `3` | After N answers, escalates to `intervention_requested` |
| Deduplication | — | — | Tracks answered activity IDs; skips already-answered questions |
| Runtime | `JULES_AUTO_ANSWER_RUNTIME` | `llm` | Configures the answer generation backend |
| Timeout | `JULES_AUTO_ANSWER_TIMEOUT_SECONDS` | `300` | Per-cycle timeout |

### 10.3 Activities

| Activity | Queue | Purpose |
|----------|-------|---------|
| `integration.jules.list_activities` | `mm.activity.integrations` | Extract latest question from session activities |
| `integration.jules.answer_question` | `mm.activity.integrations` | Full question-answer cycle (prompt → sendMessage) |
| `integration.jules.get_auto_answer_config` | `mm.activity.integrations` | Read env var config (determinism-safe) |

## 11. Implementation Guidance

To align Jules with the shared external-agent design, the practical next steps are:

1. Keep `JulesClient` focused on transport.
2. Extract shared external-adapter logic from `JulesAgentAdapter` and `CodexCloudAgentAdapter` into a reusable base.
3. Keep Jules-specific status normalization in `normalize_jules_status()`.
4. Ensure workflow orchestration remains in `MoonMind.AgentRun`, not in Jules-specific code.
5. Ensure MCP, dashboard, and REST surfaces consume the same Jules normalization and runtime-gate logic.

## 12. Summary

Jules should no longer be described as a separate "4-layer integration."

The correct standard is:

- Jules transport lives in schemas and `JulesClient`
- Jules runtime translation lives in `JulesAgentAdapter`
- generic execution lifecycle lives in `MoonMind.AgentRun`
- MCP tooling is optional and sits on top of the same provider foundations

In short, Jules is the reference provider implementation of MoonMind's universal external-agent adapter pattern.

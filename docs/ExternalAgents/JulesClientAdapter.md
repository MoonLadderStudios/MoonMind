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

## 7. Relationship to the Universal External Adapter Plan

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

## 8. MCP Tooling Posture

Earlier descriptions made Jules MCP tooling look like a separate architectural layer equal to the adapter and workflow lifecycle. That framing is misleading.

The correct posture is:

- MCP tooling is an optional consumer surface
- it should reuse Jules client and runtime-gate rules
- it should not become the source of truth for execution semantics

`JulesToolRegistry` is therefore an adjunct surface, not the core Jules architecture.

## 9. Implementation Guidance

To align Jules with the shared external-agent design, the practical next steps are:

1. Keep `JulesClient` focused on transport.
2. Extract shared external-adapter logic from `JulesAgentAdapter` and `CodexCloudAgentAdapter` into a reusable base.
3. Keep Jules-specific status normalization in `normalize_jules_status()`.
4. Ensure workflow orchestration remains in `MoonMind.AgentRun`, not in Jules-specific code.
5. Ensure MCP, dashboard, and REST surfaces consume the same Jules normalization and runtime-gate logic.

## 10. Summary

Jules should no longer be described as a separate "4-layer integration."

The correct standard is:

- Jules transport lives in schemas and `JulesClient`
- Jules runtime translation lives in `JulesAgentAdapter`
- generic execution lifecycle lives in `MoonMind.AgentRun`
- MCP tooling is optional and sits on top of the same provider foundations

In short, Jules is the reference provider implementation of MoonMind's universal external-agent adapter pattern.

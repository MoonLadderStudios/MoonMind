# Technical Design: Jules Provider Adapter

**Implementation tracking:** [`docs/tmp/remaining-work/ExternalAgents-JulesAdapter.md`](../tmp/remaining-work/ExternalAgents-JulesAdapter.md)

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
- `list_activities()` — optional helper used when the session requires clarification or user feedback
- `send_message()` — reserved for clarification/manual-feedback flows, not normal multi-step workflow progression
- `merge_pull_request()` — delegates to `GitHubService` for merging PRs via the GitHub API (used for branch publish auto-merge)

This layer should remain transport-oriented and should not accumulate workflow semantics such as polling policy, Temporal wait behavior, artifact publication, or plan assembly.

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
- building a **single consolidated execution brief** when multiple workflow steps target Jules
- deciding how to derive a fallback description from instruction or artifact refs
- Jules status normalization aliases
- Jules-specific result summary construction
- Jules-specific cancel path via task resolution
- optional clarification response behavior for `AWAITING_USER_FEEDBACK`

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
2. **Integration stage** — `MoonMind.Run` or `MoonMind.AgentRun` polls Jules until the session reaches a terminal state.
3. **Fetch result** — On `succeeded`, the workflow calls `integration.jules.fetch_result` to get the session data and extracts the PR URL.
4. **Update base** *(conditional)* — If `targetBranch` is set and differs from `startingBranch`, the activity calls `JulesClient.update_pull_request_base()` to change the PR's base branch using the GitHub API.
5. **Auto-merge** — The workflow calls `repo.merge_pr`. This activity uses `GitHubService` to merge the PR via the GitHub API.
6. **Result** — Changes land directly on the target branch. The Jules-created PR is closed automatically by GitHub upon merge.

```text
Jules (AUTO_CREATE_PR, startingBranch=main)
  ↓ creates PR targeting main
  ↓ MoonMind polls until completed
  ↓ MoonMind extracts PR URL from session output
  ↓ [if targetBranch ≠ startingBranch] PATCH PR base → targetBranch
  ↓ PUT merge PR
  ↓ Changes land on targetBranch ✓
````

When `targetBranch` is the same as `startingBranch` (or not specified), the base-update step is skipped.

### 6.4 Error Handling

The auto-merge step is **best-effort**:

* If the PR URL cannot be extracted from the session result, the workflow logs a warning and continues.
* If the base-branch update fails (e.g., target branch does not exist), the merge is **not attempted** and the PR remains open for manual resolution.
* If the GitHub merge API returns an error (e.g., merge conflicts, branch protection rules), the merge fails gracefully and the PR remains open for manual resolution.
* Network or timeout errors are caught and logged without failing the workflow.

### 6.5 Configuration

* **`GITHUB_TOKEN`** — Required environment variable for the merge API call. Must have `repo` scope or sufficient permissions to update and merge PRs.
* **`startingBranch`** — Set via `workspaceSpec.startingBranch` or `workspaceSpec.branch` in the `AgentExecutionRequest`. Defaults to `main`. This is where Jules starts its work.
* **`targetBranch`** — Set via `parameters.targetBranch` or `workspaceSpec.targetBranch`. When set and different from `startingBranch`, the PR's base branch is updated before merging. When not set, the PR merges into `startingBranch`.
* **Merge method** — Currently defaults to `"merge"` (merge commit). Configurable in `JulesClient.merge_pull_request(merge_method=...)`.

### 6.6 Temporal Activities

| Activity        | Queue                      | Purpose                                              |
| --------------- | -------------------------- | ---------------------------------------------------- |
| `repo.merge_pr` | `mm.activity.integrations` | Optionally update PR base, then merge via GitHub API |

### 6.7 Schema

`MergePRResult` (in `moonmind/workflows/adapters/github_service.py`):

| Field       | Type          | Description                             |
| ----------- | ------------- | --------------------------------------- |
| `pr_url`    | `str`         | The GitHub PR URL that was merged       |
| `merged`    | `bool`        | Whether the merge succeeded             |
| `merge_sha` | `str \| None` | SHA of the merge commit (if successful) |
| `summary`   | `str`         | Human-readable summary of the result    |

### 6.8 Client Methods

| Method                                   | Purpose                             |
| ---------------------------------------- | ----------------------------------- |
| `JulesClient.merge_pull_request()`       | `PUT` merge a GitHub PR             |
| `JulesClient.update_pull_request_base()` | `PATCH` a PR's base (target) branch |

## 7. Multi-Step Workflow Execution

### 7.1 Problem

Jules is fragile when asked to execute a long workflow as a sequence of follow-up turns. In practice, the `sendMessage` approach adds too many state transitions and too many opportunities for session drift, confusion, partial context loss, or degraded planning quality.

The main failure modes of multi-turn step progression are:

* a later follow-up prompt can be interpreted without enough durable context from earlier turns
* the session can become brittle after intermediate completions or user-feedback cycles
* per-step progression encourages short, local reasoning instead of a coherent repo-wide implementation plan
* the plan can fragment into "do this next" turns instead of one integrated checklist
* MoonMind must manage more provider-specific session choreography than is warranted

For Jules, the more reliable approach is to provide the **entire Jules-targeted workflow at once** as one execution brief and let Jules build and execute one coherent plan.

### 7.2 Solution: One-Shot Consolidated Execution Brief

MoonMind should treat a multi-step Jules workflow as **one Jules execution**, not one session per step and not one session with multiple workflow-driving follow-up turns.

The orchestration rule is:

1. Collect the ordered workflow steps that should be executed by Jules.
2. Compile them into one consolidated execution brief.
3. Start exactly one Jules session with that brief.
4. Poll that session until terminal completion.
5. Fetch one final result and PR URL.
6. Do **not** drive normal step progression through `sendMessage`.

This yields:

* one Jules session
* one cohesive plan
* one set of accumulated file changes
* one PR
* fewer provider round trips
* less workflow/provider coupling

### 7.3 Session Lifecycle

```text
MoonMind plan
  ↓ select contiguous or bundled Jules-targeted work
  ↓ compile one-shot execution brief
  ↓ sessions.create(prompt=consolidated_brief)
  ↓ Jules: QUEUED → PLANNING → IN_PROGRESS → COMPLETED
  ↓ MoonMind polls until terminal state
  ↓ fetch result
  ↓ optional PR base update / auto-merge
  ↓ workflow succeeds or fails
```

There is no normal "step 2 via sendMessage" path in the standard multi-step execution model.

### 7.4 Workflow Assembly Rule

For Jules, MoonMind should introduce a provider-specific **workflow bundling** phase before adapter start.

The bundling rule is:

* when multiple ordered steps are intended for Jules and are safe to execute together, MoonMind compiles them into one consolidated brief
* the consolidated brief becomes the `instructions` payload for a single `AgentExecutionRequest`
* the individual plan nodes remain part of MoonMind's logical workflow history, but Jules receives one combined execution package

This preserves MoonMind's internal structure without forcing Jules to mirror that structure turn by turn.

### 7.5 Bundling Scope

The default recommendation is to bundle **all consecutive Jules-compatible steps** into one Jules run when they share the same:

* repository
* workspace / branch context
* publish mode
* high-level task objective

MoonMind should **not** bundle across clear workflow boundaries such as:

* different repositories
* materially different auth/runtime requirements
* steps that require human approval between them
* steps that intentionally target different runtimes
* steps where later instructions depend on artifacts that do not yet exist at bundle time
* steps with incompatible side-effect profiles

The point is not "always bundle everything." The point is: **when Jules is chosen, prefer one-shot bundles over multi-turn progression.**

### 7.6 One-Shot Brief Design

The one-shot brief should be designed to help Jules create a stable internal checklist.

MoonMind should compile the brief into the following structure.

#### 7.6.1 Brief Sections

1. **Mission**

   * one short paragraph describing the overall objective

2. **Repository and Workspace Context**

   * repo name
   * starting branch
   * target branch if applicable
   * publish mode
   * relevant workspace constraints

3. **Execution Rules**

   * preserve user intent
   * make minimal necessary changes
   * avoid unrelated refactors
   * do not change generated/vendor files unless required
   * stop and ask if blocked by ambiguity that cannot be safely resolved

4. **Ordered Work Checklist**

   * a numbered list of concrete tasks
   * each task should be phrased as an outcome, not just a topic
   * each task may include sub-bullets with files, constraints, or acceptance conditions

5. **Validation Checklist**

   * tests to run
   * linters or formatters to run
   * static analysis or build steps if relevant
   * what to report if validation cannot be completed

6. **Deliverable Requirements**

   * summarize changes
   * note tradeoffs / assumptions
   * include validation results
   * identify any incomplete or risky items

#### 7.6.2 Style Guidance

The compiled brief should be:

* specific
* strongly ordered
* checklist-shaped
* low-ambiguity
* outcome-oriented
* explicit about constraints and validation

It should avoid:

* conversational multi-turn phrasing
* references like "now do the next step"
* unnecessary chain-of-thought style instructions
* vague placeholders such as "handle the rest"
* duplicated or conflicting task wording

### 7.7 Recommended Prompt Shape

MoonMind should prefer a prompt shape similar to the following:

```text
You are implementing a multi-part repository task as one cohesive change.

Mission:
<overall objective>

Repository Context:
- Repository: <repo>
- Starting branch: <branch>
- Target branch: <target branch or same as starting>
- Publish mode: <pr|branch>
- Runtime: Jules via MoonMind

Execution Rules:
- Complete the work as one cohesive implementation.
- Follow the ordered checklist below.
- Make the minimum necessary code and documentation changes.
- Avoid unrelated refactors.
- Preserve existing architecture and conventions unless the checklist requires otherwise.
- If a checklist item cannot be completed safely, explain why in the final summary.

Ordered Checklist:
1. <clear task outcome>
   - Relevant files or subsystems: <...>
   - Constraints: <...>
   - Acceptance notes: <...>

2. <clear task outcome>
   - Relevant files or subsystems: <...>
   - Constraints: <...>
   - Acceptance notes: <...>

3. <clear task outcome>
   - Relevant files or subsystems: <...>
   - Constraints: <...>
   - Acceptance notes: <...>

Validation Checklist:
- Run: <tests>
- Run: <lint/build/typecheck commands if applicable>
- If any validation cannot run, say so and explain why.

Final Response Requirements:
- Summarize the changes made.
- State which checklist items were completed.
- Note any incomplete items, blockers, assumptions, or follow-up recommendations.
- Include validation results.
```

### 7.8 Checklist Compilation Heuristics

To improve Jules results, MoonMind should compile steps into a checklist using these heuristics.

#### 7.8.1 Convert Step Instructions into Outcomes

Prefer:

* "Implement the new adapter registry entry for Jules and register it in `build_default_registry()`"

Instead of:

* "Look at the adapter registry and update it"

#### 7.8.2 Include File and Subsystem Hints

When known, add likely touch points:

* exact files
* relevant docs
* related modules
* tests that should be updated

This helps Jules ground the checklist in the codebase.

#### 7.8.3 Preserve Order but Reduce Redundancy

If two steps say essentially:

* add new config
* wire new config into settings
* document the new config

MoonMind should keep them as distinct checklist items when order matters, but collapse repeated prose and restate them clearly.

#### 7.8.4 Attach Acceptance Notes

Where available, each task should include a short "done means" note, for example:

* config is typed, wired into settings, and documented
* adapter returns canonical normalized status values
* docs align with the implemented behavior

#### 7.8.5 Put Validation at the End, Not Mixed Throughout

Jules tends to do better when implementation tasks are grouped first and validation is expressed as a final checklist section.

### 7.9 Adapter Responsibilities for One-Shot Bundles

The Jules adapter should not need to know the full logical workflow graph. It only needs the already-compiled consolidated brief.

That means the division of responsibility is:

**Workflow / planner side**

* decide which steps are bundled
* compile the ordered one-shot brief
* create one `AgentExecutionRequest`

**Jules adapter side**

* translate that request into `JulesCreateTaskRequest`
* preserve correlation metadata
* preserve idempotency behavior
* map status and result as normal

This keeps bundling as a MoonMind orchestration concern, not a transport concern.

### 7.10 Idempotency and Replays

One-shot bundling works better with Temporal durability than a provider-specific multi-turn protocol.

Recommendations:

* use one idempotency key per bundled Jules run, not per original step
* persist the compiled brief as an artifact or artifact-backed payload when it is large
* include metadata indicating which logical plan node IDs were bundled into the run
* ensure workflow replay reconstructs the same compiled brief deterministically

Suggested metadata:

* `moonmind.bundleId`
* `moonmind.bundledNodeIds`
* `moonmind.bundleStrategy = "one_shot_jules"`
* `moonmind.correlationId`
* `moonmind.idempotencyKey`

### 7.11 Error Handling

* **Session failure**: If Jules reaches `FAILED`, the bundled run fails as one unit.
* **Transport failure**: Create/start retries use the same policy as normal create-task requests.
* **Checklist under-completion**: If Jules succeeds but leaves checklist items incomplete, MoonMind should surface that in the result summary rather than pretending all logical steps succeeded.
* **Oversized brief**: If the bundled brief exceeds practical provider limits, MoonMind should fail early or split into multiple bundles using an explicit bundling policy rather than silently truncating.
* **Ambiguity**: If Jules requires clarification, the `AWAITING_USER_FEEDBACK` path remains available as an exception path, not the primary workflow progression mechanism.

### 7.12 Backward Compatibility

Single-step Jules execution remains unchanged:

* compile the one-step brief
* create one Jules session
* poll to completion
* fetch result

The main behavioral change is for multi-step Jules workflows:

* **old model:** create + repeated `sendMessage`
* **new model:** one compiled brief + one create call

### 7.13 Why This Model Is Better for Jules

This approach is preferred because it is more aligned with how Jules appears to operate successfully:

* it encourages one coherent plan
* it keeps the repo state and implementation intent in one execution frame
* it reduces fragile turn-by-turn choreography
* it produces a clearer plan/checklist shape
* it decreases the amount of provider-specific orchestration MoonMind must maintain

In short: MoonMind should stop treating Jules like a durable conversational worker for workflow steps and instead treat it as a **one-shot implementation agent** that works best from a strong upfront brief.

## 8. Relationship to the Universal External Adapter Plan

Jules should be the reference provider when extracting a reusable external-adapter base.

That means future refactoring should aim to preserve this separation:

### Shared Base Responsibilities

* idempotency guard behavior
* correlation metadata helpers
* common handle/status/result metadata assembly
* capability declaration shape
* default cancel fallback behavior

### Jules Override Responsibilities

* provider request payload creation
* provider response parsing
* provider status normalization
* provider cancel translation
* provider-specific prompt assembly hints when the execution style is one-shot bundled work

If a future provider requires materially different behavior, it should justify that divergence explicitly rather than silently bypassing the shared pattern.

## 9. MCP Tooling Posture

Earlier descriptions made Jules MCP tooling look like a separate architectural layer equal to the adapter and workflow lifecycle. That framing is misleading.

The correct posture is:

* MCP tooling is an optional consumer surface
* it should reuse Jules client and runtime-gate rules
* it should not become the source of truth for execution semantics

`JulesToolRegistry` is therefore an adjunct surface, not the core Jules architecture.

## 10. Question Auto-Answer

When Jules enters `AWAITING_USER_FEEDBACK`, the `MoonMind.AgentRun` polling loop detects this via the `awaiting_feedback` normalized status and may initiate an automatic question-answer cycle.

This is now an **exception path**, not the normal multi-step execution path.

### 10.1 Scope

Auto-answer logic lives **exclusively** in `MoonMind.AgentRun` (`agent_run.py`). The parent `MoonMind.Run` workflow does not duplicate this logic — it delegates to `AgentRun` child workflows for all external agent execution.

### 10.2 Guardrails

| Guard         | Env Variable                        | Default | Behavior                                                       |
| ------------- | ----------------------------------- | ------- | -------------------------------------------------------------- |
| Opt-out       | `JULES_AUTO_ANSWER_ENABLED`         | `true`  | When `false`, maps to `intervention_requested`                 |
| Max cycles    | `JULES_MAX_AUTO_ANSWERS`            | `3`     | After N answers, escalates to `intervention_requested`         |
| Deduplication | —                                   | —       | Tracks answered activity IDs; skips already-answered questions |
| Runtime       | `JULES_AUTO_ANSWER_RUNTIME`         | `llm`   | Configures the answer generation backend                       |
| Timeout       | `JULES_AUTO_ANSWER_TIMEOUT_SECONDS` | `300`   | Per-cycle timeout                                              |

### 10.3 Activities

| Activity                                   | Queue                      | Purpose                                           |
| ------------------------------------------ | -------------------------- | ------------------------------------------------- |
| `integration.jules.list_activities`        | `mm.activity.integrations` | Extract latest question from session activities   |
| `integration.jules.answer_question`        | `mm.activity.integrations` | Full question-answer cycle (prompt → sendMessage) |
| `integration.jules.get_auto_answer_config` | `mm.activity.integrations` | Read env var config (determinism-safe)            |

### 10.4 Design Rule

`sendMessage` remains valid for:

* clarification responses
* operator intervention
* explicit resume flows

`sendMessage` should **not** be the standard mechanism for advancing normal multi-step MoonMind workflow execution.

## 11. Implementation Guidance

To align Jules with the shared external-agent design and the new one-shot execution model, the practical next steps are:

1. Keep `JulesClient` focused on transport.
2. Remove `sendMessage` as the normal step-to-step workflow progression strategy.
3. Add a deterministic bundling/compiler phase that converts ordered Jules-targeted steps into one consolidated execution brief.
4. Persist bundle metadata so MoonMind can explain which logical steps were represented in the one-shot run.
5. Keep Jules-specific status normalization in `normalize_jules_status()`.
6. Ensure workflow orchestration remains in `MoonMind.AgentRun`, not in Jules-specific code.
7. Ensure MCP, dashboard, and REST surfaces consume the same Jules normalization and runtime-gate logic.
8. Surface incomplete checklist items clearly in final result summaries when Jules only partially completes the bundled brief.

## 12. Summary

Jules should no longer be described as a provider that executes MoonMind multi-step workflows through repeated `sendMessage` turns.

The correct standard is:

* Jules transport lives in schemas and `JulesClient`
* Jules runtime translation lives in `JulesAgentAdapter`
* MoonMind compiles multi-step Jules work into one consolidated execution brief
* generic execution lifecycle lives in `MoonMind.AgentRun`
* `sendMessage` is reserved for clarification/intervention flows
* MCP tooling is optional and sits on top of the same provider foundations

In short, Jules is the reference provider implementation of MoonMind's universal external-agent adapter pattern, and its preferred execution style for multi-step work is **one-shot bundled execution with a checklist-shaped brief**.

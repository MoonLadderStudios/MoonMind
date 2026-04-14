# Task Proposal System

Status: Active
Owners: MoonMind Engineering
Last Updated: 2026-04-14
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskFinishSummarySystem.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/MissionControlArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`, `docs/Tasks/AgentSkillSystem.md`, `docs/Tasks/SkillAndPlanContracts.md`
Implementation tracking: `docs/tmp/015-TaskProposalSystemPlan.md`

---

## 1. Summary

MoonMind proposals are the reviewable follow-up work items discovered during a
`MoonMind.Run`. They exist to preserve useful next steps without automatically
starting new executions.

Each proposal:

1. Stores a canonical `taskCreateRequest` that is valid for later promotion.
   - Proposal payloads may specifically carry runtime selection, publish behavior, executable tool selection, and agent skill selection or inherited skill intent.
2. Remains a control-plane review object until a human explicitly promotes it.
3. Promotes into a new `MoonMind.Run` execution through the same Temporal-backed
   create path used by `/api/executions`.
4. Preserves repository-aware deduplication, review priority, notification, and
   origin metadata.

*Note: Canonical agent-skill storage, precedence, and snapshot semantics live in `docs/Tasks/AgentSkillSystem.md`. This document only defines how proposals preserve and promote task-facing skill intent.*

The canonical architecture is Temporal-native:

1. `MoonMind.Run` executes work.
2. A dedicated `proposals` stage runs after execution and before finalization.
3. Generator activities create candidate follow-up tasks from run artifacts and
   normalized agent results.
4. Submission activities validate and store proposals in the proposal queue.
5. Promotion creates a new Temporal execution only after human approval.

---

## 2. Core Invariants

These rules are fixed:

1. `taskCreateRequest` is the canonical promote-to-task payload.
2. Proposal creation is not execution; promotion is the only action that starts
   new work.
3. Promotion creates a new `MoonMind.Run` execution, not a legacy queue job.
4. `taskCreateRequest.payload.repository` is the canonical repository target for
   deduplication and future execution.
5. Human review is required before any proposal becomes durable running work.
6. Proposal generation is best-effort and must never compromise the correctness
   of the parent run result.
7. Proposal payloads must conform to the canonical Temporal submit contract used
   by `/api/executions`.
8. Proposal origin metadata must identify the durable workflow that produced the
   proposal.
9. When a proposal depends on agent skill context, that context must be preserved explicitly in the stored `taskCreateRequest` or preserved through documented inheritance semantics.
10. Proposal promotion must not silently drift to unrelated skill defaults when the original proposal logic depended on explicit skill selection.

---

## 3. `MoonMind.Run` Lifecycle Integration

### 3.1 Submit-time contract

Task-shaped submit requests may include:

1. `task.proposeTasks`
2. `task.proposalPolicy`
3. `task.skills`
4. `step.skills`

These values are part of the durable run contract and must be preserved in
`initialParameters` for the run. These fields directly impact downstream proposal generation and promotion.

The canonical direction is:

1. Preserve the raw `task.proposalPolicy` object in `initialParameters`.
2. Resolve global defaults plus per-task overrides inside the proposal stage or
   inside `proposal.submit`.
3. Do not rely on a parallel flattened proposal-policy contract for new work.

### 3.1.1 Canonical submission-path normalization

All new task submission paths must normalize proposal intent into the same
canonical nested task payload accepted by `/api/executions` before
`MoonMind.Run` starts.

Codex managed sessions do not define a parallel proposal contract. They are the
highest-risk producer because session containers may create additional MoonMind
tasks through the internal API path, but the rule also applies to ordinary API
submission, proposal promotion, schedules, and any future task-creation surface.

Required mapping:

1. session-level or adapter-level proposal opt-in becomes
   `initialParameters.task.proposeTasks`
2. session-level or adapter-level proposal routing overrides become
   `initialParameters.task.proposalPolicy`
3. task/runtime/repository metadata continues to flow through the canonical task
   payload instead of a Codex-only side channel

Non-canonical locations such as root-level `initialParameters.proposeTasks`,
turn metadata, session binding metadata, container environment, or adapter-local
state are not the durable write contract for new work.

Workflow code may temporarily read root-level `initialParameters.proposeTasks`
or older policy shapes for replay and in-flight migration safety. New
submission paths must still write the nested `task.*` fields so future behavior
does not depend on compatibility fallbacks.

For Codex, this applies both when a user submits a task targeting a managed
Codex runtime and when a Codex managed session creates additional MoonMind tasks
through the internal API path.

### 3.2 Run states

For proposal-capable runs, the relevant state vocabulary is:

1. `scheduled`
2. `initializing`
3. `waiting_on_dependencies`
4. `planning`
5. `awaiting_slot`
6. `executing`
7. `awaiting_external`
8. `proposals`
9. `finalizing`
10. `completed`
11. `failed`
12. `canceled`

The proposal system must use this lifecycle vocabulary consistently across:

1. workflow state
2. execution API responses
3. Mission Control status mapping
4. related documentation

### 3.3 When the `proposals` stage runs

The `proposals` stage runs only when both conditions are true:

1. global proposal generation is enabled by workflow settings
2. the submitted task enables proposals through `task.proposeTasks`

This gives operators a real global off switch while still allowing task-level
opt-in when the feature is enabled system-wide.

For Codex managed-session originated runs, the durable gate is the canonical
nested value preserved in `initialParameters.task.proposeTasks`; session-local
flags alone must not determine whether the workflow enters `proposals`.

### 3.4 Proposal generation

Proposal generation happens in Temporal activities, never in workflow code.

Generators analyze:

1. run artifacts
2. plan-step outcomes
3. normalized `AgentRunResult` data from managed and external agents
4. finish-summary signals and execution diagnostics
5. resolved skill snapshot metadata and skill-related execution context, specifically observing if a specific skill set or context contributed to the detected follow-up work (using artifact-backed skill metadata where needed rather than treating the runtime workspace as the sole source of truth)

Generators must:

1. treat run inputs and logs as untrusted
2. use artifact-backed references for large context
3. avoid side effects such as commits, pushes, or task creation
4. redact or exclude secrets and unsafe command output
5. **Preserve Skill Selectors:** Only emit explicit skill selectors when they materially affect the correctness or expected behavior of the follow-up work. Generic project follow-up proposals do not need to redundantly stamp all inherited defaults if doing so adds noise without preserving important intent. When the originating task explicitly selected non-default skill sets, proposals should preserve that intent unless documented otherwise.

### 3.5 Proposal submission

Proposal submission is a separate side-effecting activity that:

1. validates candidate entries (including any `task.skills` or `step.skills` selectors)
2. resolves proposal policy
3. normalizes origin metadata
4. enforces repository and run-quality routing rules
5. preserves explicit skill-selection intent when present, rejecting or normalizing malformed skill-selection fields before storage
6. creates proposal records through `/api/proposals`

Submission creates proposals only. It never promotes them.

### 3.6 Finish summary integration

The run finish summary must record proposal outcomes in both the typed result and
`reports/run_summary.json`.

At minimum it records:

1. whether proposal generation was requested
2. generated candidate count
3. submitted proposal count
4. redacted submission or validation errors

---

## 4. Proposal Policy

Proposal routing follows global defaults plus optional per-task overrides.

*Note: `proposalPolicy` does **not** independently redefine agent skill precedence. Skill selection is strictly preserved from the candidate payload or inherited from canonical system semantics, not recomputed by the proposal policy itself.*

### 4.1 Global controls

The canonical global controls are:

1. workflow-level proposal enable switch
2. default targets
3. default per-target proposal caps
4. default MoonMind severity floor
5. MoonMind repository target

Operator-facing configuration remains the source of truth for these defaults.

### 4.2 Per-task policy contract

`task.proposalPolicy` controls:

1. `targets`
2. `maxItems.project`
3. `maxItems.moonmind`
4. `minSeverityForMoonMind`
5. `defaultRuntime`

`defaultRuntime` is a real part of the desired-state contract. It supplies the
default `task.runtime.mode` for generated proposals when the candidate does not
already specify one. It does not block operators from overriding runtime during
promotion.

### 4.3 Policy resolution

Policy resolution happens at proposal submission time.

The resolved policy must:

1. merge global defaults with `task.proposalPolicy`
2. preserve explicit candidate values over defaults
3. enforce per-target capacity limits
4. enforce MoonMind severity and tag gates
5. stamp `defaultRuntime` only when the candidate omitted a runtime

### 4.4 Project-targeted proposals

Project-targeted proposals keep the triggering repository as
`taskCreateRequest.payload.repository`.

They are used for follow-up feature work, cleanup, tests, or project-local
quality work discovered during the run.

### 4.5 MoonMind-targeted proposals

MoonMind-targeted proposals are reserved for MoonMind run-quality improvements.

When routed to MoonMind:

1. the repository is rewritten to the configured MoonMind repository
2. category is normalized to `run_quality`
3. signal severity must meet the configured floor
4. tags must match approved run-quality signal tags

This keeps generic project follow-ups out of MoonMind's internal run-quality
backlog.

---

## 5. Canonical Proposal Payload Contract

### 5.1 Canonical direction

Stored proposals must use the same task-shaped contract accepted by Temporal
submission through `/api/executions`.

That means:

1. `taskCreateRequest` stores a normal task payload
2. `task.runtime.mode` selects the runtime
3. `task.tool` and `step.tool`, when present, use the canonical Temporal submit shape
4. `task.tool.type` must be `skill` when a tool selector is provided (representing an executable tool, not an agent instruction bundle)
5. proposal payloads do not use `tool.type = "agent_runtime"`
6. `task.skills` and `step.skills` may be included and must follow the canonical Agent Skill System contract defined in `docs/Tasks/AgentSkillSystem.md`.

### 5.2 Candidate example

```json
[
  {
    "title": "Add regression coverage for retry loop detection",
    "summary": "The run retried the same recoverable failure pattern without a targeted regression test.",
    "category": "run_quality",
    "tags": ["retry", "loop_detected"],
    "signal": {
      "severity": "high"
    },
    "taskCreateRequest": {
      "type": "task",
      "priority": 0,
      "maxAttempts": 3,
      "payload": {
        "repository": "MoonLadderStudios/MoonMind",
        "task": {
          "instructions": "Add a regression test covering retry loop detection in the Temporal runtime.",
          "tool": {
            "type": "skill",
            "name": "auto",
            "version": "1.0"
          },
          "skills": {
            "sets": ["deployment-default", "temporal-runtime-quality"],
            "include": [
              { "name": "moonmind-doc-writer", "version": "2.3.0" }
            ]
          },
          "runtime": {
            "mode": "codex"
          },
          "publish": {
            "mode": "pr"
          }
        }
      }
    }
  }
]
```

### 5.3 Payload rules

1. `taskCreateRequest` must already validate against the canonical task contract.
2. `taskCreateRequest.payload.repository` determines deduplication and future
   execution target.
3. `taskCreateRequest.payload.task.runtime.mode` uses current supported task
   runtimes: `codex`, `gemini_cli`, `claude`, and optionally `jules`.
4. Candidate payloads must not include secrets, raw credentials, or unsafe log
   dumps.
5. Promotion-time overrides must also validate against the same canonical task
   contract before execution starts.
6. If `task.skills` or `step.skills` are present, they must validate against the canonical agent-skill contract. Proposals must not embed full agent skill bodies inline when refs or selectors are the correct contract.
7. Proposals should preserve execution intent, not raw runtime materialization state. Proposal payloads must **not** store mutable `.agents/skills` directory state, runtime-local materialization outputs, or ephemeral prompt bundles produced only for one adapter session.
8. Proposal-capable work originating from a Codex managed session must already carry `task.proposeTasks` and any `task.proposalPolicy` in the canonical task payload. Proposal generation must not reconstruct this intent from session-local metadata.

---

## 6. Origin, Identity, and Naming

### 6.1 Origin source

Temporal-backed proposals use:

1. `origin.source = "workflow"`
2. `origin.id = workflow_id`

`temporal` is not a canonical proposal origin value.

### 6.2 Origin metadata

Origin metadata uses snake_case keys consistently across workflow payloads,
stored proposal records, API responses, and docs.

Canonical keys are:

1. `workflow_id`
2. `temporal_run_id`
3. `trigger_repo`
4. `starting_branch`
5. `working_branch`

### 6.3 Identity rules

1. `workflow_id` is the durable source task identity for workflow-originated
   proposals.
2. `temporal_run_id` is diagnostic metadata, not the durable task handle.
3. Task-oriented Temporal surfaces continue to use `taskId == workflowId`.
4. Continue-as-new does not change the proposal origin identity; the durable
   workflow remains the source.

---

## 7. Review, Promotion, and Execution

Proposal creation does not start work. Promotion does.

### 7.1 Promotion flow

Promotion must follow this algorithm:

1. load the stored proposal
2. verify the proposal is still `open`
3. merge `taskCreateRequestOverride` into the stored `taskCreateRequest`
4. apply shortcut `runtimeMode` only by constructing that override
5. validate the merged payload against the canonical task contract, including verification of any agent-skill selectors
6. preserve explicit skill-selection intent from the stored proposal unless the operator intentionally overrides it, ensuring promotion-time overrides do not silently drop or corrupt skill fields
7. submit the merged task through the same Temporal-backed create path used by
   `/api/executions`
8. create a new `MoonMind.Run` through `TemporalExecutionService.create_execution()`
9. store the promoted workflow or execution identifier on the proposal record
10. return both the updated proposal and the new execution metadata

Promotion is therefore a control-plane-to-Temporal bridge, not a proposal-local
mutation only.

### 7.2 Skill preservation and inheritance

Proposal promotion preserves agent skill intent from the original execution.

* **Defaults vs Explicit:** When the original task relied only on deployment/default inheritance, proposal payloads may omit redundant explicit skill selectors. When the original task included explicit non-default skill selection that materially affects execution behavior, proposals should preserve that selection explicitly.
* **Promotion Overrides:** Operators may override runtime at promotion time. Agent skill selectors are preserved by default. Changing the runtime does not automatically erase or rewrite agent skill intent.
* **Incompatibilities:** If a selected skill set is incompatible with the chosen runtime, promotion must fail validation or require an explicit override path. Proposal promotion does not re-resolve skill-source precedence as an undocumented side effect.

### 7.3 Runtime selection

Proposal promotion supports operator runtime selection.

Rules:

1. the backend-served runtime list is the source of truth
2. supported task-facing runtime values are `codex`, `gemini_cli`, `claude`, and
   optionally `jules`
3. runtime overrides apply to the promoted execution request, not to the stored
   proposal payload
4. disabled runtimes must fail validation before a workflow is created

### 7.4 Response contract

The promote API response must include:

1. the updated proposal record
2. the created execution metadata for the new `MoonMind.Run`

---

## 8. Observability and UI Contract

### 8.1 Status mapping

`proposals` is a first-class workflow state and must be visible across API and UI
surfaces.

For dashboard compatibility mapping:

1. `scheduled -> queued`
2. `waiting_on_dependencies -> waiting`
3. `awaiting_slot -> queued`
4. `proposals -> running`
5. `completed -> completed`

### 8.2 Proposal-stage visibility

The system must surface:

1. `mm_state = proposals` while proposal generation or submission is in progress
2. proposal counts and errors in finish summary data
3. links from execution detail to proposals filtered by
   `originSource=workflow` and `originId=<workflow_id>`
4. **Proposal-Review Visibility:** Proposal detail or promotion UI may need to present a compact summary of execution context showing whether the proposal carries explicit skill selectors or inherits deployment defaults, alongside runtime, repository, and publish settings.

### 8.3 Failure handling

Proposal generation and submission are best-effort.

Rules:

1. a successful run may still report proposal-stage errors
2. malformed candidates are skipped, not promoted
   - Specifically, malformed or incompatible skill selectors in generated candidates should be skipped with a visible validation error and not silently dropped in a way that changes execution meaning.
3. submission retries must be bounded and idempotent
4. partial success must be visible through generated and submitted counts

---

## 9. Worker-Boundary Architecture

The proposal system follows the same Temporal rule as the rest of MoonMind:
workflows orchestrate and activities perform side effects.

Queue placement is:

1. proposal generation on the LLM-capable activity fleet
2. proposal submission and storage on a control-plane or integrations-capable
   activity fleet

Generation and submission are intentionally separate concerns. LLM-facing work and
control-plane writes must not share an undifferentiated activity boundary.

* **Constraint:** Proposal generation may inspect resolved skill snapshot metadata or related execution artifacts, while proposal submission strictly validates skill-selection payload fields but avoids materializing runtime skill context.

---

## 10. Current Implementation Snapshot

This feature is partially implemented. Phase tracking lives in
`docs/tmp/015-TaskProposalSystemPlan.md`.

### 10.1 Already implemented

1. `MoonMind.Run` enters a real `proposals` stage after execution and before
   finalization.
2. `proposal.generate` and `proposal.submit` exist in the Temporal activity
   catalog.
3. The workflow records proposal counts and errors in the finish summary during
   finalization.
4. Proposal APIs and Mission Control review surfaces already exist.
5. Promotion creates a new `MoonMind.Run` through the Temporal execution service.
6. `TaskProposalPolicy.defaultRuntime` is modeled and `proposal.submit` stamps it
   onto candidates that omit `task.runtime.mode`.
7. The workflow proposal stage consumes nested `task.proposalPolicy` and ignores
   flattened proposal-policy fields for new behavior.
8. Core `/api/executions` task submission preserves nested `task.proposeTasks`,
   `task.proposalPolicy`, `task.skills`, and `step.skills` into
   `initialParameters.task`.

### 10.2 Partially implemented

1. Codex managed-session-created task paths still need end-to-end verification
   that proposal intent is normalized into canonical `initialParameters.task.*`
   fields before `MoonMind.Run` starts.
2. migration-only read fallbacks may still exist for in-flight workflows, but new
   managed-session work must not depend on root-level or session-local proposal
   flags as the only durable source.
3. the workflow has a `proposals` state, but related UI and execution mappings
   are not yet fully aligned everywhere
4. promotion returns the created execution identifier, but the proposal storage
   model does not yet persist a promoted execution/workflow linkage field

### 10.3 Still missing

1. persisted promotion linkage from proposal to created execution
2. fully standardized origin naming and metadata shape across workflow, storage,
   API, and UI
3. proposal payloads do not yet model `task.skills` / `step.skills` end-to-end
   across every generator, storage, promotion, and UI surface
4. proposal UI does not yet expose skill-related execution context clearly
5. end-to-end preservation and validation of explicit agent-skill selection through proposal storage and promotion
6. fully standardized Codex managed-session normalization for `task.proposeTasks`
   and `task.proposalPolicy` before Temporal run creation

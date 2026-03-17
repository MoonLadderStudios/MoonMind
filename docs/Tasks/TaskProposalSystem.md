# Task Proposal System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-16  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskFinishSummarySystem.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`

---

## 1. Summary

MoonMind supports a **proposal** primitive for follow-up work discovered during a
run. Proposals are the system's mechanism for continuous improvement — agents
surface insights about fixes, refactors, tests, and run-quality improvements, and
operators decide which of those insights become real work.

Each proposal:

1. Stores a canonical `taskCreateRequest` — a complete, promotable task payload.
2. Remains reviewable and dismissible by humans before any new work starts.
3. Preserves repository-aware deduplication, priority, and notification behavior.

### Architectural context

MoonMind's execution substrate is **Temporal**. The root workflow `MoonMind.Run`
represents a task. Each task contains a plan of ordered steps, where agent-runtime
steps dispatch to `MoonMind.AgentRun` child workflows and non-agent steps dispatch
to standard Temporal activities. Both **managed agents** (Gemini CLI, Claude Code,
Codex CLI) and **external agents** (Jules, future BYOA integrations) follow this
model through the unified `AgentAdapter` protocol.

Proposal generation is a deliberate phase in the `MoonMind.Run` lifecycle — it runs
after execution and before finalization. The Temporal workflow decides whether
proposals are enabled for the run, executes proposal generation through activities,
submits valid proposals to the Proposal Queue API, and records counts and errors in
the run finish summary.

This design keeps proposal creation durable, observable, and compatible with
Temporal's workflow/activity model while preserving the human-review boundary that
prevents automated follow-up work from running without operator approval.

---

## 2. Core Invariants

These rules are fixed and must not be weakened by any implementation changes:

1. `taskCreateRequest` remains the canonical promote-to-task payload.
2. Promotion creates a new `MoonMind.Run` workflow execution from `taskCreateRequest`;
   proposal review is not execution.
3. `taskCreateRequest.payload.repository` remains the repository the promoted task
   will operate on.
4. Deduplication remains based on `(repository + normalized title)`.
5. Notifications remain repository-aware and priority-aware.
6. Human review is required before any proposal becomes a durable Temporal execution.
7. Proposals are part of the Proposal Queue product surface; `proposals` is not a new
   Temporal execution source.

---

## 3. Lifecycle within `MoonMind.Run`

### 3.1 Submit-time contract

When a caller submits a task, the request may include:

1. `task.proposeTasks` — boolean opt-in for proposal generation.
2. `task.proposalPolicy` — optional per-task policy overrides.

These values are preserved in the Temporal workflow's `initialParameters`. Proposal
intent is part of the durable run contract, not a worker-local toggle.

### 3.2 Workflow stages

The `MoonMind.Run` lifecycle for proposal-capable tasks:

1. `initializing`
2. `planning`
3. `executing` — dispatches plan steps as activities or `MoonMind.AgentRun` child
   workflows
4. `proposals` — generates and submits follow-up proposals
5. `finalizing`
6. terminal state

The `proposals` stage runs only when **both** conditions are true:

1. Global proposal generation is enabled (`MOONMIND_PROPOSAL_TARGETS` is not disabled).
2. The task payload requests proposal generation, either explicitly or via default.

If proposal generation is disabled, the workflow skips the stage and records zero
generated/submitted proposals in the finish summary.

### 3.3 Proposal generation

Proposal generation runs through **Temporal activities**, not inside workflow code.

The workflow invokes one or more generator activities. These generators analyze
execution artifacts, agent run results, and step outcomes to produce candidate
proposal records. The generator output is a structured JSON array where each
candidate contains:

1. `title`
2. `summary`
3. `taskCreateRequest`
4. optional `category`
5. optional `tags`
6. optional signal metadata for run-quality routing

#### Proposal sources from agent runs

Because `MoonMind.Run` dispatches agent-runtime steps to `MoonMind.AgentRun` child
workflows, proposal generators have access to the collected `AgentRunResult` for
each step. This provides structured inputs for proposal generation:

- **Managed agents** (Gemini CLI, Claude Code, Codex CLI) — the `ManagedRunSupervisor`
  produces structured results including summaries, output artifact refs, failure
  classification, and diagnostics. Proposal generators can analyze these to identify
  follow-up fixes, missed tests, or run-quality improvements.
- **External agents** (Jules, BYOA) — the `ExternalAgentAdapter` normalizes provider
  results into the same `AgentRunResult` contract. Proposal generators can analyze
  external agent outputs using the same logic as managed agent outputs.

This means proposal generation is runtime-agnostic at the generator level — it
operates on `AgentRunResult` and step-level artifacts regardless of whether the
step ran as a managed CLI agent, an external cloud agent, or a plain activity.

#### Generator safety rules

Generator activities must:

1. Treat task instructions, logs, summaries, and repository content as untrusted input.
2. Read execution artifacts and repository context only through artifact-backed refs.
3. Never directly enqueue new tasks, push branches, or commit code.
4. Never include secrets, raw credentials, or unsafe command logs in proposals.

### 3.4 Proposal submission

Once candidate proposals exist, a separate side-effecting activity submits them to
the Proposal Queue API.

Submission logic must:

1. Parse the generated proposal JSON.
2. Discard malformed entries.
3. Apply `task.proposalPolicy` overrides when present.
4. Apply global default targeting when overrides are absent.
5. Normalize proposal origin metadata.
6. Validate repository targeting.
7. Enforce run-quality severity/tag gates when routing MoonMind-targeted proposals.
8. Create proposals through `/api/proposals`.

The submission activity authenticates as a trusted worker/service principal
authorized to create proposals. Proposal writes are not anonymous workflow side
effects.

Submission is proposal creation only. It does not promote or auto-run the task.

### 3.5 Finish summary integration

The workflow records proposal results in `reports/run_summary.json` and the typed
finish summary payload. At minimum it records:

1. Whether proposal generation was requested.
2. Which generator hooks ran.
3. How many proposal candidates were generated.
4. How many proposals were successfully submitted.
5. Redacted submission errors.

This allows Mission Control to show proposal outcomes without requiring operators
to inspect raw workflow artifacts.

---

## 4. Targeting and Policy

Proposal submission follows a resolved policy: global defaults plus optional
per-task overrides.

### 4.1 Global policy knobs

1. `MOONMIND_PROPOSAL_TARGETS=project|moonmind|both`
2. `MOONMIND_CI_REPOSITORY=MoonLadderStudios/MoonMind` (default)
3. `TASK_PROPOSALS_MAX_ITEMS_PROJECT`
4. `TASK_PROPOSALS_MAX_ITEMS_MOONMIND`
5. `TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND`

Behavior:

1. `project` — proposals exclusively target the execution's project repository.
2. `moonmind` — proposals exclusively target `MOONMIND_CI_REPOSITORY`.
3. `both` — generators may emit both types when signals match.

### 4.2 Per-task override

`task.proposalPolicy` within the task payload dynamically alters targeting for
individual workflows.

The per-task policy controls:

1. `targets`
2. `maxItems.project`
3. `maxItems.moonmind`
4. `minSeverityForMoonMind`

The resolved policy is evaluated during proposal submission, not during proposal
review.

### 4.3 Project-targeted proposals

Project proposals preserve the triggering task's repository. Submission logic
ensures `taskCreateRequest.payload.repository` matches the execution repository
before the proposal is stored.

These proposals are used for follow-up feature work, refactors, tests, or other
project-local next steps.

### 4.4 MoonMind-targeted proposals

MoonMind-targeted proposals are used for run-quality improvements affecting MoonMind
itself — retries, loops, artifact gaps, missing references, or flaky-test handling.

When routing to MoonMind:

1. The repository is rewritten to `MOONMIND_CI_REPOSITORY`.
2. The category is normalized to `run_quality`.
3. Signal severity must meet the configured floor.
4. Tags must include an approved run-quality signal tag.

This prevents generic project follow-ups from leaking into MoonMind's internal
run-quality backlog.

---

## 5. Origin and Identity

Proposals need stable origin metadata so operators can trace them back to the exact
run that produced them.

The canonical origin rules are:

1. `origin.source = "temporal"`
2. `origin.id = workflowId`
3. `origin.metadata.workflowId = workflowId`
4. `origin.metadata.temporalRunId = current run id`
5. `origin.metadata.triggerRepo = execution repository`
6. `origin.metadata.startingBranch` and `origin.metadata.workingBranch` are included
   when known

For product-facing task identity, `taskId == workflowId` for Temporal-backed work.
Proposal deep links and review tooling should treat the durable workflow identifier
as the source task handle.

If a workflow continues as new, proposal origin should still resolve to the durable
`workflowId`. The current run ID is useful for debugging but not for durable identity.

---

## 6. Generated Proposal Contract

Proposal generators produce candidate entries shaped like this:

```json
[
  {
    "title": "Add regression coverage for retry loop detection",
    "summary": "The run retried a recoverable failure pattern multiple times without a targeted regression test.",
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
          "instructions": "Add a regression test for retry loop detection in the Temporal runtime.",
          "tool": {
            "type": "agent_runtime",
            "name": "auto",
            "version": "1.0"
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

Rules:

1. `taskCreateRequest` must already be a valid future task payload.
2. `taskCreateRequest.payload.repository` determines dedup and future execution target.
3. Generators may omit MoonMind-specific fields for project-targeted proposals.
4. Generators must not include secrets, raw credentials, or unsafe command logs.
5. The `tool.type` in proposed `taskCreateRequest` should be `"agent_runtime"` to
   route promoted tasks through the `MoonMind.AgentRun` execution path.

---

## 7. Review, Promotion, and Execution

Proposal creation does not start work.

The lifecycle remains:

1. `MoonMind.Run` creates proposal records via submission activities.
2. Reviewers inspect, snooze, reprioritize, dismiss, or edit proposals.
3. Promotion creates a new task request from the stored `taskCreateRequest`.
4. That promoted task becomes a new `MoonMind.Run` workflow execution, which
   dispatches its steps through `MoonMind.AgentRun` child workflows as usual.

This separation matters:

1. Proposal generation is exploratory and best-effort.
2. Promotion is explicit human approval.
3. Temporal is the execution substrate only after promotion.

---

## 8. Priority, Notifications, and Observability

### 8.1 Review priority

Review priority is derived from category, tags, and signal metadata. High-signal
run-quality items such as flaky tests, repeated failure loops, or severe artifact
gaps should rank above generic cleanups.

### 8.2 Notifications

Notification behavior remains tied to stored proposal records, not to the originating
workflow directly. Once a `MoonMind.Run` successfully creates a proposal, the
existing proposal notification rules apply.

### 8.3 Observability

The implementation should surface:

1. Proposal stage start/finish in workflow progress (via `mm_state` search attribute).
2. Proposal generation/submission errors as activity failures or structured warnings.
3. Proposal counts in `reports/run_summary.json`.
4. Links from Mission Control run detail to proposals filtered by
   `originSource=temporal` and `originId=<workflowId>`.

---

## 9. Failure Handling and Safety

Proposal generation is best-effort and must not compromise the correctness of the
underlying run result.

The runtime rules are:

1. A successful execution may still finish with proposal submission errors.
2. Malformed proposal candidates are skipped rather than promoted implicitly.
3. Proposal submission errors are redacted before persistence.
4. Retries for proposal submission must be bounded and idempotent.
5. A proposal generator must never commit code, push branches, or mutate unrelated
   repository state.

If proposal creation partially succeeds, the finish summary must report both the
generated count and submitted count so operators can see the loss precisely.

---

## 10. Implementation Status

The proposal system is **designed but not yet wired** into the Temporal workflow.

### What exists today

- `task.proposeTasks` is accepted in the submit path and preserved in workflow
  parameters.
- The Proposal Queue API (`/api/proposals`) exists and accepts proposal records.
- Mission Control exposes a `proposeTasks` checkbox in the task submission form.
- The finish summary contract includes a `proposals` section.

### What remains

- **Generator activities** — Temporal activities for analyzing execution artifacts
  and `AgentRunResult` data to produce candidate proposals. These should analyze
  step-level outcomes from both managed and external agent runs.
- **Submission activity** — A Temporal activity that validates, filters, and submits
  generated proposals to `/api/proposals`.
- **Proposals phase in `MoonMind.Run`** — Wiring the proposal generation and
  submission activities into the `MoonMind.Run` lifecycle between the `executing`
  and `finalizing` stages.
- **Finish summary population** — Recording proposal generation/submission results
  in `reports/run_summary.json`.

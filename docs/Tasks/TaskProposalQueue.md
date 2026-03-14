# Task Proposal Queue

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskQueueSystem.md`, `docs/Tasks/TaskUiArchitecture.md`, `docs/Temporal/TemporalAgentExecution.md`, `docs/Tasks/TaskFinishSummarySystem.md`

---

## 1. Summary

MoonMind supports a proposal primitive for follow-up work discovered during a run.

Each proposal:

1. Stores a canonical `taskCreateRequest`.
2. Remains reviewable and dismissible by humans before any new work starts.
3. Preserves repository-aware dedup, priority, and notification behavior.

In the Temporal world, proposal generation is not a queue-worker side effect. It is a
deliberate phase in the `MoonMind.Run` workflow lifecycle. The Temporal workflow
decides whether proposal generation is enabled for the run, executes proposal
generation logic through activities, submits valid proposals to the Proposal Queue
API, and records counts/errors in the run finish summary.

The proper Temporal design is:

1. A task submit request includes `task.proposeTasks` and optional `task.proposalPolicy`.
2. `POST /api/queue/jobs` routes the work to Temporal and preserves those fields in
   `initialParameters`.
3. `MoonMind.Run` executes the requested work normally.
4. After execution and before finalization, the workflow enters a dedicated
   `proposals` phase when proposal generation is enabled.
5. Proposal generation activities produce candidate proposals as structured JSON.
6. Proposal submission activities validate policy, route targets, and create proposal
   records through `/api/proposals`.
7. Reviewers triage those proposals and may later promote them into new Temporal task
   executions.

This keeps proposal creation durable, observable, and compatible with Temporal's
workflow/activity model while preserving the existing human-review boundary.

---

## 2. Core Invariants

The Temporal migration must not weaken the established proposal semantics.

The following rules remain fixed:

1. `taskCreateRequest` remains the canonical promote-to-task payload.
2. Promotion creates a new task execution from `taskCreateRequest`; proposal review is
   not execution.
3. `taskCreateRequest.payload.repository` remains the repository the promoted task will
   operate on.
4. Dedup remains based on `(repository + normalized title)`.
5. Notifications remain repository-aware and priority-aware.
6. Human review remains required before any proposal becomes a durable Temporal
   execution.
7. Temporal-backed proposals are still part of the Proposal Queue product surface;
   `proposals` is not a new Temporal execution source.

---

## 3. Temporal Lifecycle

### 3.1 Submit-time contract

When a caller submits a Temporal-backed task, the request may include:

1. `task.proposeTasks`
2. `task.proposalPolicy`

The submit adapter preserves these values in the Temporal execution's
`initialParameters`. That means proposal intent is part of the run contract, not a
worker-local toggle that disappears once the run starts.

### 3.2 Workflow stages

The proper `MoonMind.Run` lifecycle for proposal-capable tasks is:

1. `initializing`
2. `planning`
3. `executing`
4. `proposals`
5. `finalizing`
6. terminal state

The `proposals` stage runs only when both conditions are true:

1. global proposal generation is enabled
2. the task payload requests proposal generation, either explicitly or via default

If proposal generation is disabled, the workflow skips the stage and records zero
generated/submitted proposals in the finish summary.

### 3.3 Proposal generation

Proposal generation must run through Temporal activities, not inside workflow code.
The workflow may invoke one or more generator activities, including prompt-driven
skills such as:

1. `fix-proposal`
2. `continuation-proposal`

These activities analyze execution artifacts and produce candidate proposal records.
The generator output is a small structured JSON array equivalent to the legacy
`task_proposals.json` artifact. Each candidate contains:

1. `title`
2. `summary`
3. `taskCreateRequest`
4. optional `category`
5. optional `tags`
6. optional signal metadata for MoonMind CI/run-quality routing

Generator activities must treat task instructions, logs, summaries, and repository
content as untrusted input. They may read execution artifacts and repository context,
but they must not directly enqueue new tasks.

### 3.4 Proposal submission

Once candidate proposals exist, a separate side-effecting activity submits them to the
Proposal Queue API.

Submission logic must:

1. parse the generated proposal JSON
2. discard malformed entries
3. apply `task.proposalPolicy` overrides when present
4. apply global default targeting when overrides are absent
5. normalize proposal origin metadata
6. validate repository targeting
7. enforce MoonMind CI severity/tag gates when routing run-quality proposals
8. create proposals through `/api/proposals`

The submission activity must authenticate as a trusted worker/service principal
authorized to create proposals. Proposal writes are not anonymous workflow side
effects.

Submission is proposal creation only. It does not promote or auto-run the task.

### 3.5 Finish summary integration

The workflow records proposal results in the run finish summary artifact and summary
payload. At minimum it records:

1. whether proposal generation was requested
2. which generator hooks ran
3. how many proposal candidates were generated
4. how many proposals were successfully submitted
5. redacted submission errors

This allows Mission Control to show proposal outcomes for Temporal runs without
requiring operators to inspect raw artifacts.

---

## 4. Targeting and Policy

Temporal proposal submission follows a resolved policy: global defaults plus optional
per-task overrides.

### 4.1 Global policy knobs

1. `MOONMIND_PROPOSAL_TARGETS=project|moonmind|both`
2. `MOONMIND_CI_REPOSITORY=MoonLadderStudios/MoonMind` (default)
3. `TASK_PROPOSALS_MAX_ITEMS_PROJECT`
4. `TASK_PROPOSALS_MAX_ITEMS_MOONMIND`
5. `TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND`

Behavior:

1. `project`: proposals exclusively target the execution's project repository.
2. `moonmind`: proposals exclusively target `MOONMIND_CI_REPOSITORY`.
3. `both`: workers may emit both types when signals match.

### 4.2 Per-task override (preferred)

`task.proposalPolicy` within the execution payload dynamically alters this state for individual workflows.

The per-task policy controls:

1. `targets`
2. `maxItems.project`
3. `maxItems.moonmind`
4. `minSeverityForMoonMind`

The resolved policy is evaluated during proposal submission, not during proposal
review.

### 4.3 Project-targeted proposals

Project proposals preserve the triggering task's repository. Submission logic ensures
`taskCreateRequest.payload.repository` matches the execution repository before the
proposal is stored.

These proposals are used for follow-up feature work, refactors, tests, or other
project-local next steps.

### 4.4 MoonMind-targeted proposals

MoonMind-targeted proposals are used for run-quality improvements affecting MoonMind
itself, such as retries, loops, artifact gaps, missing references, or flaky-test
handling.

When routing to MoonMind:

1. the repository is rewritten to `MOONMIND_CI_REPOSITORY`
2. the category is normalized to `run_quality`
3. signal severity must meet the configured floor
4. tags must include an approved run-quality signal tag

This prevents generic project follow-ups from leaking into MoonMind's internal
run-quality backlog.

---

## 5. Origin and Identity

Temporal-backed proposals need stable origin metadata so operators can trace them back
to the exact run that produced them.

The canonical origin rules are:

1. `origin.source = "temporal"`
2. `origin.id = workflowId`
3. `origin.metadata.workflowId = workflowId`
4. `origin.metadata.temporalRunId = current run id`
5. `origin.metadata.triggerRepo = execution repository`
6. `origin.metadata.startingBranch` and `origin.metadata.workingBranch` are included
   when known

For product-facing task identity, `taskId == workflowId` for Temporal-backed work.
Proposal deep links and review tooling should treat that durable workflow identifier as
the source task handle.

If a workflow continues as new, proposal origin should still resolve to the durable
`workflowId`. The current run ID is useful for debugging but not for durable identity.

---

## 6. Generated Proposal Contract

A Temporal proposal generator produces candidate entries shaped like this:

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
            "type": "skill",
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

---

## 7. Review, Promotion, and Execution

Proposal creation does not start work.

The lifecycle remains:

1. Temporal run creates proposal records.
2. Reviewers inspect, snooze, reprioritize, dismiss, or edit them.
3. Promotion creates a new task request from the stored `taskCreateRequest`.
4. That promoted task may itself route to Temporal and become a new `MoonMind.Run`
   execution.

This separation matters:

1. proposal generation is exploratory and best-effort
2. promotion is explicit human approval
3. Temporal remains the execution substrate only after promotion

---

## 8. Priority, Notifications, and Observability

### 8.1 Review priority

Review priority is derived from category, tags, and signal metadata. High-signal
run-quality items such as flaky tests, repeated failure loops, or severe artifact gaps
should rank above generic cleanups.

### 8.2 Notifications

Notification behavior remains tied to stored proposal records, not to the originating
workflow directly. Once a Temporal run successfully creates a proposal, the existing
proposal notification rules apply.

### 8.3 Observability

A proper Temporal implementation should surface:

1. proposal stage start/finish in workflow progress
2. proposal generation/submission errors as activity failures or structured warnings
3. proposal counts in `reports/run_summary.json`
4. links from Temporal run detail to proposals filtered by `originSource=temporal` and
   `originId=<workflowId>`

---

## 9. Failure Handling and Safety

Proposal generation is best-effort and must not compromise the correctness of the
underlying run result.

The runtime rules are:

1. a successful execution may still finish with proposal submission errors
2. malformed proposal candidates are skipped rather than promoted implicitly
3. proposal submission errors are redacted before persistence
4. retries for proposal submission must be bounded and idempotent
5. a proposal generator must never commit code, push branches, or mutate unrelated
   repository state

If proposal creation partially succeeds, the finish summary must report both the
generated count and submitted count so operators can see the loss precisely.

---

## 10. Current Migration Note

This document describes the correct Temporal behavior for the proposal system.

At the time of writing, the Temporal submit path already preserves `proposeTasks`,
but the full proposal phase still needs to be wired into `MoonMind.Run`. The legacy
queue worker currently contains the mature implementation for:

1. deciding whether proposals are requested
2. invoking `fix-proposal` and `continuation-proposal`
3. reading generated `task_proposals.json`
4. applying proposal policy and MoonMind severity/tag routing
5. submitting proposals through `/api/proposals`

The Temporal migration is complete for this feature only when the same semantics are
implemented as explicit Temporal proposal-generation and proposal-submission
activities, and the results are reflected in the Temporal finish summary.

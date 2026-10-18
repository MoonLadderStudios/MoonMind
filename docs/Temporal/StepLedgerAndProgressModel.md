# Step Ledger and Progress Model

**Document Class:** Module Contract Specification
Status: Normative  
Owners: MoonMind Platform + Dashboard  
Last updated: 2026-06-13

## 1. Purpose

This document is the single owner for MoonMind's operator-facing **step ledger** and **execution progress** model.

It defines:

- the canonical source of planned steps
- the live step-state ledger maintained by `MoonMind.UserWorkflow`
- the bounded progress summary exposed on execution detail
- step identity, status, attempts, checks, refs, and artifact semantics
- latest-run behavior for workflow detail
- how retry from a step relates to the ledger's step order
- the boundary between workflow state, artifacts, projections, and observability

This document does **not** redefine plan syntax, artifact storage internals, or managed-run log transport. Those remain owned by:

- `docs/Workflows/SkillAndPlanContracts.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Observability/LiveLogs.md`

## 2. Core model

MoonMind exposes step state through three layers:

1. **Immutable plan layer**
   - The canonical planned step list comes from the resolved executable **plan artifact** once planning is complete.
   - UI and APIs must treat the plan artifact as the source of planned node order, titles, IDs, and dependencies.

2. **Live step-state layer**
   - `MoonMind.UserWorkflow` maintains a compact in-memory step ledger for the current/latest run.
   - The workflow exposes that ledger through a Query such as `GetStepLedger`.
   - The ledger carries only bounded state, summaries, refs, and identifiers.

3. **Durable evidence layer**
   - Large outputs, logs, diagnostics, provider payloads, review details, and rich summaries remain in artifacts or managed-run observability APIs.
   - The ledger links to that evidence by semantic refs; it does not inline it.

4. **Step order in history**
   - When a step starts, `MoonMind.UserWorkflow` writes its ledger `order` to the `mm_current_step_order` memo.
   - Retry from a step finds the step's start in Temporal history by the first memo change to that order, so ledger `order` and the memo value must stay the same number.

## 3. Canonical source rules

### 3.1 Planned steps

After planning completes:

- the **plan artifact** is the authoritative planned-step source
- `payload.task.steps` is input intent only and must not remain the canonical post-planning step list
- step display fields must come from stable plan-node metadata, not from log parsing

Required operator-safe plan-node fields:

- `id` -> becomes `logicalStepId`
- `title`
- `tool`
- dependency information (`dependsOn` or graph-equivalent data)

### 3.2 Live state

The workflow-owned ledger is authoritative for current step state during execution.

Rules:

- the workflow ledger owns status, attempt, waiting state, check state, and current refs
- the ledger must be queryable while executing and after completion
- the ledger must remain compact enough to live safely in workflow state

### 3.3 Evidence

The following must stay out of workflow state:

- long stdout/stderr content
- merged log bodies
- large diffs or patches
- provider-native payload dumps
- long review feedback bodies
- diagnostics bundles

Those belong in artifacts or `/api/agent-runs/*`.

## 4. Latest-run semantics

MoonMind workflow detail is anchored on the logical execution:

- `taskId == workflowId`
- `workflowId` remains stable across Continue-As-New
- `runId` identifies the current/latest run instance

Step detail uses these v1 rules:

- `/workflows/{workflowId}` shows the **latest/current run's** step ledger only
- `GET /api/executions/{workflowId}/steps` returns the latest/current run by default
- prior-run step history must not be mixed into the default workflow detail view
- historical runs and cross-run step history are future explicit surfaces

## 5. Progress summary model

Execution detail should expose a lightweight progress summary so normal polling stays cheap.

Representative shape:

```json
{
  "total": 6,
  "pending": 2,
  "ready": 0,
  "executing": 1,
  "awaitingExternal": 0,
  "reviewing": 0,
  "completed": 3,
  "failed": 0,
  "skipped": 0,
  "canceled": 0,
  "currentStepTitle": "Run test suite",
  "updatedAt": "2026-04-04T18:11:15Z"
}
```

Rules:

- this object is execution-level summary data, not the full ledger
- it should be returned by `GET /api/executions/{workflowId}`
- it must remain bounded and display-safe
- it should be derivable from workflow state without artifact hydration

## 6. Step ledger query contract

`MoonMind.UserWorkflow` should expose a Query such as `GetStepLedger`.

Representative response:

```json
{
  "workflowId": "wf_123",
  "runId": "run_456",
  "runScope": "latest",
  "steps": [
    {
      "logicalStepId": "run-tests",
      "order": 4,
      "title": "Run test suite",
      "tool": { "type": "skill", "name": "repo.run_tests", "version": "1" },
      "dependsOn": ["apply-patch"],
      "status": "executing",
      "waitingReason": null,
      "attentionRequired": false,
      "attempt": 1,
      "startedAt": "2026-04-04T18:10:00Z",
      "updatedAt": "2026-04-04T18:11:15Z",
      "timing": {
        "startedAt": "2026-04-04T18:10:00Z",
        "endedAt": null,
        "durationMs": null,
        "elapsedMs": 75000,
        "serverNow": "2026-04-04T18:11:15Z",
        "precision": "live"
      },
      "summary": "Executing tests in sandbox",
      "checks": [],
      "refs": {
        "childWorkflowId": null,
        "childRunId": null,
        "agentRunId": null
      },
      "artifacts": {
        "outputSummary": null,
        "outputPrimary": null,
        "runtimeStdout": null,
        "runtimeStderr": null,
        "runtimeMergedLogs": null,
        "runtimeDiagnostics": null,
        "providerSnapshot": null
      },
      "lastError": null
    }
  ]
}
```

## 7. Step row contract

Each row in the ledger represents the **current view of one logical step execution** for the current/latest run.

Required fields:

| Field | Meaning |
| --- | --- |
| `logicalStepId` | Stable plan-node identifier |
| `order` | Stable display order for the current plan |
| `title` | Operator-facing step title |
| `tool` | Display-safe executable tool descriptor |
| `dependsOn` | Upstream logical step IDs |
| `status` | Canonical step status from §8 |
| `waitingReason` | Bounded blocked-state reason when applicable |
| `attentionRequired` | Whether the step currently needs operator action |
| `attempt` | Current attempt number for the run-scoped logical step |
| `startedAt` | Timestamp for the current attempt start |
| `updatedAt` | Last meaningful user-visible mutation for the step |
| `timing` | Logical step timing object for row chips, expanded Timing details, duration bars, and attempt-history display |
| `summary` | Short bounded operator summary |
| `checks[]` | Structured review/check verdicts from §9 |
| `refs` | Child-workflow / workflow-run references from §10 |
| `artifacts` | Semantic artifact refs from §11 |
| `lastError` | Bounded latest error summary |

Rules:

- `logicalStepId` comes from the plan node and must remain stable within that plan
- `timing` carries `startedAt`, `endedAt`, `durationMs`, `elapsedMs`, `serverNow`, and `precision`
- `precision: "exact"` means the terminal end and duration come from known step-end timing; `precision: "live"` means active elapsed time as of `serverNow`, which can be the last row write rather than the read, so the dashboard keeps an active row ticking from `startedAt` to the current time and never shows less than `elapsedMs`; `precision: "fallback"` means lifecycle timestamps were used because exact end timing was unavailable; `precision: "unavailable"` means the dashboard must show unavailable, ready, or not-started copy instead of inventing a duration
- `updatedAt` remains the last user-visible mutation timestamp and must not be treated as the primary terminal end when explicit timing is available
- `workload.durationSeconds` is runner/workload metadata and is not the logical step duration source for the row timing chip
- clients must not infer step identity from array position alone
- `summary` must stay bounded and display-safe
- `lastError` is a summary only; large error details belong in artifacts

If a step exhausts retries because of model-provider rate limits, the current
step row must include a bounded operator-facing summary such as:

> Failed after 5 attempts. The step hit a model-provider rate limit and
> exhausted exponential-backoff retries.

`lastError.error_code` should classify the failure as `RATE_LIMITED`. Detailed
stderr, provider payloads, and diagnostics remain in artifacts.

## 8. Step status vocabulary

The canonical v1 step statuses are:

| Status | Meaning |
| --- | --- |
| `pending` | Planned but not yet ready to run |
| `ready` | Dependencies satisfied; eligible for dispatch |
| `executing` | The step is actively executing |
| `awaiting_external` | Waiting on external provider/runtime progress |
| `reviewing` | Structured review/check processing is active |
| `completed` | Completed successfully |
| `failed` | Ended in failure for the current run |
| `skipped` | Intentionally not executed |
| `canceled` | Canceled before successful completion |

Rules:

- step statuses are more specific than workflow/dashboard rollup statuses
- `waitingReason` provides the bounded cause for blocked states
- `attentionRequired` indicates whether the current stall requires operator action

## 9. Checks and review results

Checks and review gates are first-class structured data, not log-parsing conventions.

Representative `checks[]` row:

```json
{
  "kind": "approval_policy",
  "status": "pending",
  "summary": null,
  "retryCount": 0,
  "artifactRef": null
}
```

Rules:

- `checks[]` is the canonical place for review/check state attached to a step
- review verdicts, retry counts, and feedback summaries should appear here
- large review payloads belong in artifacts linked by `artifactRef`
- the core step ledger schema must remain stable as new check kinds are added

## 10. Attempts and retries

The step ledger must preserve retry semantics without rerunning completed earlier work.

Rules:

- the primary row reflects the current/latest attempt for `(workflowId, runId, logicalStepId)`
- attempts are run-scoped; reruns do not merge attempt history across run IDs
- the authoritative key shape for derived storage is `(workflowId, runId, logicalStepId, attempt)`
- the default workflow detail view may show a compact current row plus attempt count
- detailed attempt history may be lazy-loaded later, but attempt identity must already be explicit
- exhausted model-provider rate-limit retries must remain visible in the latest
  attempt summary and `lastError`, with detailed attempt evidence linked through
  artifacts rather than expanded inline

### 10.1 Retried runs

Retry from a step resets a closed run to the start of the chosen step. The reset run's ledger carries the earlier steps as their replayed history recorded them, and the chosen step and later steps run again as new attempts of the reset run. The failed run's attempts remain in its own history and artifacts.

## 11. Parent/child refs and artifact refs

### 11.1 Parent/child boundary

`MoonMind.UserWorkflow` owns:

- plan ordering
- step status
- checks
- current summaries
- refs to child runs and artifacts

`MoonMind.AgentRun` owns:

- true managed/external agent lifecycle
- runtime/provider wait states
- detailed logs and diagnostics
- final runtime results

Therefore the parent step ledger should carry refs such as:

- `childWorkflowId`
- `childRunId`
- `agentRunId`

### 11.2 Artifact ref groups

The step ledger should group step evidence by semantic meaning rather than generic blob lists.

Recommended artifact slots:

- `outputSummary`
- `outputPrimary`
- `runtimeStdout`
- `runtimeStderr`
- `runtimeMergedLogs`
- `runtimeDiagnostics`
- `providerSnapshot`
- `workspaceCheckpoint`
- `recoveryCheckpoint`

Recommended artifact-link metadata:

- `step_id`
- `attempt`
- optional `scope = "step"`

Clients must not guess “latest” step evidence by sorting artifacts locally. The server groups by `(step_id, attempt, link_type)` and returns the canonical refs.

## 12. Source of truth, projection, and degraded reads

The authoritative sources for step data are:

- planned structure: plan artifact
- live status: workflow state/query
- durable evidence: artifact system and managed-run observability APIs

A derived read model such as `execution_step_projection` is allowed for:

- fast dashboard reads
- compatibility joins
- degraded mode when the query path is impaired
- future run-history surfaces

Rules:

- Postgres projection rows are **not** the authority for step truth
- projection rows must be keyed by `(workflowId, runId, logicalStepId, attempt)`
- projection repair must reconcile from workflow state and artifact linkage, not invent state locally

## 13. Visibility, Memo, and `mm_updated_at`

The step ledger must **not** live in Search Attributes or Memo.

Rules:

- Search Attributes remain bounded, execution-level list/query fields only
- Memo remains small human-readable execution metadata only
- step rows, attempt history, check arrays, and error bodies must not be stored there

`mm_updated_at` should move on meaningful user-visible mutations such as:

- plan resolved
- step becomes ready
- step starts
- child run launched
- check/review verdict changes
- step succeeds
- step fails
- step is canceled or skipped

`mm_updated_at` should **not** move on:

- every heartbeat
- every log chunk
- every low-level retry/backoff detail

## 14. UI and telemetry posture

The dashboard workflow detail should treat the **Steps** section as the primary operator comprehension surface, positioned above generic timeline and artifact views.

Step row expansion should group:

- Summary
- Checks
- Logs & Diagnostics
- Artifacts
- Metadata

For a retried run, the Steps section shows the earlier steps with their recorded results and the retried step as a new attempt.

OpenTelemetry remains supplemental:

- spans and metrics may carry `workflowId`, `runId`, `logicalStepId`, `attempt`, and `agentRunId`
- telemetry is not the source of product-facing step truth
- step detail must be driven by workflow state plus artifact/observability refs

## 15. Summary

MoonMind's step model is:

- **plan artifact** for planned structure
- **workflow query/state** for current step truth
- **artifacts and `/api/agent-runs/*`** for durable evidence
- **Temporal history** for keeping completed steps when a failed run is retried from a step
- **latest-run workflow detail** as the default operator view

This keeps step tracking workflow-aligned, Temporal-idiomatic, and compact enough to preserve healthy workflow history.

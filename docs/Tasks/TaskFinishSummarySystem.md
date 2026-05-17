# Task Finish Summary System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-05-17  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskProposalQueue.md`,
`docs/Temporal/ErrorTaxonomy.md`, `docs/Temporal/StepLedgerAndProgressModel.md`

---

## 1. Summary

MoonMind requires a clear "what happened?" summary at the end of every
`MoonMind.Run` execution so Mission Control operators can quickly distinguish:

* **Published output** (PR/branch updated successfully) vs
* **No changes** (publish skipped because the repository was already correct) vs
* **Publish disabled** (intentionally set `publish.mode=none` for a dry run) vs
* **Failure** (and at which Temporal Activity boundary) vs
* **Cancelled**

This document describes the finish-summary system executed during the workflow's
finalization path. The system produces a structured, non-secret summary artifact
and syncable result payload for rapid UI indexing.

---

## 2. Finish Summary Contract

### 2.1 Outcome Codes

At the conclusion of a `MoonMind.Run` Temporal Workflow, the system guarantees an outcome code of:

* `PUBLISHED_PR`
* `PUBLISHED_BRANCH`
* `NO_CHANGES`
* `PUBLISH_DISABLED`
* `FAILED`
* `CANCELLED`

It also logs a `finishOutcomeStage` indicating which stage of the workflow it reached (e.g., `prepare`, `llm_execution`, `publish`, `proposals`).

### 2.2 JSON Artifact Shape

The finish summary data is small and stored as JSON. A `reports/run_summary.json` is generated natively inside the Workflow and uploaded to the Artifact API before termination.

```json
{
  "schemaVersion": "v1",
  "jobId": "0e8f1f2f-...",
  "targetRuntime": "codex",
  "timestamps": {
    "startedAt": "2026-03-13T12:00:00Z",
    "finishedAt": "2026-03-13T12:03:05Z",
    "durationMs": 185000
  },
  "finishOutcome": {
    "code": "NO_CHANGES",
    "stage": "publish",
    "reason": "publish skipped: no local changes"
  },
  "publish": {
    "mode": "pr",
    "status": "skipped",
    "reason": "no local changes",
    "prUrl": null
  },
  "proposals": {
    "generatedCount": 0,
    "submittedCount": 0,
    "errors": []
  }
}
```

### 2.3 Failure Diagnostics Contract

Failed and canceled runs MUST include a bounded, redacted, operator-meaningful
failure reason. Generic Temporal wrappers (for example `Activity task failed`,
`Child Workflow execution failed`) are not acceptable as the operator-visible
reason — the workflow MUST walk the exception chain and surface the deepest
non-generic root cause.

`finishOutcome.reason` is the canonical operator-facing failure string. When a
run fails, it MUST be sourced from a structured failure diagnostic captured at
the failure boundary, not reconstructed from the terminal `summary` field
alone.

Failed runs SHOULD additionally include a structured `failure` object alongside
`finishOutcome`. The object is small, free of secrets, and shaped as:

```json
{
  "failure": {
    "stage": "executing",
    "category": "integration_error",
    "source": "child_workflow",
    "stepId": "apply-patch",
    "stepTitle": "Apply patch",
    "childWorkflowId": "task-123:agent:apply-patch",
    "message": "Provider authentication failed with HTTP 401 for profile codex-prod.",
    "rootCauseType": "ApplicationError",
    "diagnosticsRef": "artifact://..."
  }
}
```

Field semantics:

* `stage`: the workflow stage that was active when the failure was captured
  (`prepare`, `planning`, `executing`, `publish`, `proposals`, `finalizing`).
* `category`: aligns with `ExecutionTerminalStateInput.error_category` and the
  policy categories in `docs/Temporal/ErrorTaxonomy.md`. Permitted values are
  `user_error`, `integration_error`, `execution_error`, and `system_error`.
  `ApplicationError.type` values such as `INVALID_INPUT`,
  `UnsupportedStatus`, `ProfileResolutionError`, `SlotAcquisitionTimeout`, and
  `RATE_LIMITED` are mapped onto these four categories.
* `source`: where the failure originated — `child_workflow`, `activity`, or
  `workflow`.
* `stepId` / `stepTitle`: the failing plan node when applicable.
* `childWorkflowId`: the child workflow id when the failure originated from a
  child workflow.
* `message`: the redacted, bounded operator-facing root cause. Truncated to
  ~1000 characters.
* `rootCauseType`: the deepest non-generic exception class name observed in
  the failure chain.
* `diagnosticsRef`: an optional artifact ref pointing at larger structured
  diagnostics. Large diagnostic payloads MUST NOT be embedded inline — they
  belong in artifacts. See `docs/Temporal/StepLedgerAndProgressModel.md` for
  the same rule applied to per-step `lastError`.

When a structured `failure` is present, the `lastStep` block SHOULD reflect
the failure so operators see the failing tool, not a stale prior step:

* `lastStep.id` is set to the failing step's `stepId`.
* `lastStep.summary` matches the diagnostic `message`.
* `lastStep.lastError` carries the diagnostic `category` (mirroring the
  step-ledger `lastError` semantics defined in
  `docs/Temporal/StepLedgerAndProgressModel.md`).
* `lastStep.diagnosticsRef` is set when the diagnostic carries one.

The terminal-state activity (`execution.record_terminal_state`) uses the same
diagnostic when recording the `summary` and `errorCategory` so the visibility
projection and the finish-summary artifact never disagree about why a run
failed.

#### First-failure-wins capture

Failure diagnostics are captured at the first failure boundary that surfaces a
non-generic root cause (for example the `except Exception` block around a
`MoonMind.AgentRun` child workflow execution or a plan-step activity). Later
generic re-raises in higher-level handlers MUST NOT overwrite an earlier,
deeper diagnostic.

### 2.5 Secret Handling

Finish summaries MUST NOT contain tokens, API keys, credential strings, or full command lines with secret arguments. All strings are passed through redaction mechanisms before sync.

This rule applies to the structured `failure` diagnostic defined in §2.3 as
well. The diagnostic `message` is passed through the same redaction policy
used for `operatorSummary` and step summaries (for example
`scrub_github_tokens`) before being written to `reports/run_summary.json` or
sent to the terminal-state activity.

### 2.6 Preset Summary Ownership

Task presets do not own generic end-of-run narration. Presets may emit structured
facts that are useful after execution, such as a Jira issue key, pull request
URL, verification verdict, publish handoff, or outcome data, but those facts are
inputs to operator surfaces and workflow finalization rather than a replacement
for the canonical finish summary.

For orchestration presets, the final operational step should be the last action
needed by that preset, such as MoonSpec verification or a Jira workflow
transition. A preset should not add a final agent-authored report step whose only
purpose is to summarize normal completion. This keeps success, failure,
cancellation, and no-change runs on the same `reports/run_summary.json` contract
even when late preset steps do not run.

---

## 3. Worker Implementation (Temporal Workflow)

Inside the Python Temporal workflow logic (`MoonMind.Run`):

1. The Workflow coordinates stage timings across all child Activities.
2. Even in failure or `CancelledError` paths, a `finally:` or `except:` block captures the execution state.
3. The Workflow saves `reports/run_summary.json` to the unified Artifacts API.
4. The Workflow returns a final typed Result payload that the API syncs (or reads via Webhooks) into the Postgres `agent_jobs` table columns (`finish_outcome_code`, `finish_summary_json`) to make List queries faster in the UI.

## 4. Proposals Integration

The finish summary includes a first-class `proposals` block for the proposal
phase that runs after execution and before finalization.

When proposal generation is enabled for the run:

1. the workflow enters the `proposals` stage
2. candidate proposals are generated and submitted on a best-effort basis
3. generated and submitted counts are recorded in the finish summary
4. redacted proposal-stage errors are recorded alongside those counts

This wiring already exists in the Temporal run workflow and is part of the
canonical finish-summary surface presented in Mission Control.

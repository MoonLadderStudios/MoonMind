# Workflow Finish Summary System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-06-28
Related: `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/WorkflowProposalSystem.md`,
`docs/Temporal/ErrorTaxonomy.md`, `docs/Temporal/StepLedgerAndProgressModel.md`,
`docs/Workflows/NoCommitStatus.md`

---

## 1. Summary

MoonMind requires a clear "what happened?" summary at the end of every
`MoonMind.UserWorkflow` execution so dashboard operators can quickly distinguish:

* **Published output** (PR/branch updated successfully) vs
* **No commit** (publish skipped because no repository commit was needed) vs
* **Publish disabled** (intentionally set `publish.mode=none` for a dry run) vs
* **Failure** (and at which Temporal Activity boundary) vs
* **Cancelled**

`NO_COMMIT` replaces the older `NO_CHANGES` wording because workflows may still
perform non-repository side effects such as Jira issue transitions, comments,
verification records, or artifact publication. The finish summary must describe
repository publication separately from those side effects.

This document describes the finish-summary system executed during the workflow's
finalization path. The system produces a structured, non-secret summary artifact
and syncable result payload for rapid UI indexing.

---

## 2. Finish Summary Contract

### 2.1 Outcome Codes

At the conclusion of a `MoonMind.UserWorkflow` Temporal Workflow, the system guarantees an outcome code of:

* `PUBLISHED_PR`
* `PUBLISHED_BRANCH`
* `NO_COMMIT`
* `PUBLISH_DISABLED`
* `FAILED`
* `CANCELLED`

Legacy artifacts or compatibility adapters may still expose `NO_CHANGES`; new
workflow histories and UI copy should treat that value as an alias for
`NO_COMMIT` when the reason is that no repository commit was needed.

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
    "code": "NO_COMMIT",
    "stage": "publish",
    "reason": "No repository commit was needed."
  },
  "publish": {
    "mode": "pr",
    "status": "skipped",
    "reasonCode": "no_commit",
    "reason": "No repository changes were available to commit or publish.",
    "commitCreated": false,
    "branchPushed": false,
    "prUrl": null
  },
  "sideEffects": [
    {
      "kind": "jira",
      "status": "completed",
      "summary": "Issue transitioned to Done."
    }
  ],
  "proposals": {
    "generatedCount": 0,
    "submittedCount": 0,
    "errors": []
  }
}
```

The `sideEffects` block is optional and bounded, but it is the preferred way to
make side effects visible when a run has no repository commit. A Jira Implement
preset run that moves an issue to Done but creates no commit should therefore
summarize as `NO_COMMIT`, not as "no changes." See
`docs/Workflows/NoCommitStatus.md` for the full lifecycle contract.

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
    "childWorkflowId": "mm:123:agent:apply-patch",
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

Failed runs MAY also include a `failureSummary` object. `failure` remains the
low-level diagnostic captured at the failure boundary; `failureSummary` is the
compact operator classification that the dashboard can render without parsing
Markdown reports or long failure strings.

For MoonSpec publication gates, `failureSummary` uses this shape:

```json
{
  "failureSummary": {
    "type": "moonspec_verification_gate",
    "category": "validation_environment_blocked",
    "blockedBy": "moonspec_verify",
    "verdict": "BLOCKED",
    "classification": "environment failure / validation infrastructure unavailable",
    "diagnosticsRef": "art_...",
    "recommendedNextAction": "restore_validation_environment",
    "summary": "MoonSpec verification blocked publication: BLOCKED. environment failure / validation infrastructure unavailable.",
    "blockers": [
      "native_unreal_toolchain_missing",
      "docker_registry_unauthorized"
    ],
    "publishContext": {
      "branch": "jira-orchestrate-example",
      "baseRef": "origin/main",
      "headSha": "abc123",
      "commitCount": 6,
      "pullRequestUrl": "https://github.com/org/repo/pull/123"
    }
  }
}
```

Allowed MoonSpec summary categories are:

* `validation_environment_blocked`: required local/CI validation
  infrastructure is unavailable, for example native Unreal tooling is missing
  or a required Docker registry pull is unauthorized.
* `validation_evidence_missing`: implementation may be present, but required
  current-head CI or lane evidence is missing.
* `verification_gaps_remaining`: the verifier reported concrete remaining
  implementation or validation gaps.

For managed agent runtime failures, `failureSummary` uses:

```json
{
  "failureSummary": {
    "type": "agent_runtime_failure",
    "category": "transient_agent_runtime",
    "failureCause": "app_server_protocol_empty_turn",
    "retryRecommendedAction": "clear_session",
    "diagnosticsRef": "art_...",
    "recommendedNextAction": "retry_finalization_after_clear_session",
    "summary": "Agent runtime failed with execution_error ...",
    "partialSuccess": {
      "moonSpecVerdict": "FULLY_IMPLEMENTED",
      "pullRequestUrl": "https://github.com/org/repo/pull/123",
      "branch": "jira-orchestrate-example",
      "headSha": "abc123"
    }
  }
}
```

When a managed-runtime failure occurs after a publishable verification result or
after a pull request has already been created, `partialSuccess` MUST preserve
that evidence so the UI can distinguish "implementation verified but final
runtime turn failed" from "implementation failed".

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

Workflow presets do not own generic end-of-run narration. Presets may emit structured
facts that are useful after execution, such as a Jira issue key, pull request
URL, verification verdict, publish handoff, side-effect outcome, or no-commit
data, but those facts are inputs to operator surfaces and workflow finalization
rather than a replacement for the canonical finish summary.

For orchestration presets, the final operational step should be the last action
needed by that preset, such as MoonSpec verification or a Jira workflow
transition. A preset should not add a final agent-authored report step whose only
purpose is to summarize normal completion. This keeps success, failure,
cancellation, and no-commit runs on the same `reports/run_summary.json` contract
even when late preset steps do not run.

---

## 3. Worker Implementation (Temporal Workflow)

Inside the Python Temporal workflow logic (`MoonMind.UserWorkflow`):

1. The Workflow coordinates stage timings across all child Activities.
2. Even in failure or `CancelledError` paths, a `finally:` or `except:` block captures the execution state.
3. The Workflow saves `reports/run_summary.json` to the unified Artifacts API.
4. The Workflow records the final typed terminal-state payload into the Postgres
   execution source and projection columns (`finish_outcome_code`,
   `finish_summary_json`) to make List queries faster in the UI.

## 4. Proposals Integration

The finish summary includes a first-class `proposals` block for the proposal
phase that runs after execution and before finalization.

When proposal generation is enabled for the run:

1. the workflow enters the `proposals` stage
2. candidate proposals are generated and submitted on a best-effort basis
3. generated and submitted counts are recorded in the finish summary
4. redacted proposal-stage errors are recorded alongside those counts

This wiring already exists in the Temporal run workflow and is part of the
canonical finish-summary surface presented in the dashboard.

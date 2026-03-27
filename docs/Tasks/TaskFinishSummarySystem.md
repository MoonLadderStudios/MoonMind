# Task Finish Summary System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskProposalQueue.md`

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

### 2.3 Secret Handling

Finish summaries MUST NOT contain tokens, API keys, credential strings, or full command lines with secret arguments. All strings are passed through redaction mechanisms before sync.

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

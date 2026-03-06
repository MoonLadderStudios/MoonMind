# Task Finish Summary System

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-02-24
Related: `docs/TaskQueueSystem.md`, `docs/TaskProposalQueue.md`, `api_service/static/task_dashboard/dashboard.js`

---

## 1. Summary

MoonMind needs a clearer “what happened?” summary at the end of every queue job so dashboard operators can quickly distinguish:

* **Published output** (PR/branch) vs
* **No changes** (publish skipped because repo was already correct) vs
* **Publish disabled** (intentionally `publish.mode=none`) vs
* **Failure** (and which stage failed) vs
* **Cancelled**

This document introduces a **Finisher System** that always produces a structured, non-secret **finish summary** for each job and exposes it in the Mission Control. The existing proposals system remains intact, but becomes a **subset** of finishing behavior: proposals are surfaced as a “finisher output” and linked from the finished run.

---

## 2. Problem Statement

Today, “end of job” information exists but is not surfaced consistently:

* Queue jobs store `resultSummary` and `errorMessage`, but list views do not emphasize them.
* Publish outcomes are partially represented by artifacts and events, but not summarized in one place for fast scanning.
* When “nothing is published,” it’s hard to tell if that is:

  * a successful no-op (no changes needed),
  * publish disabled,
  * publish failed,
  * or execution failed before publish.

We need a **single, stable** summary contract per job that can be shown at-a-glance.

---

## 3. Goals and Non-goals

### 3.1 Goals

1. **At-a-glance outcome** in `/tasks/queue` and “Active Tasks” list:

   * Published PR/branch, No Changes, Publish Disabled, Failed (with stage), Cancelled.
2. **Structured finish summary** available in the job detail view without downloading artifacts.
3. **Deterministic, machine-readable artifact** for archival/debugging (`reports/run_summary.json`).
4. **Proposals integrated into finishing**

   * Show “Proposals created/submitted” counts and link to the proposals list filtered to the originating job.

### 3.2 Non-goals

1. Reworking the proposals queue semantics (promotion flow remains unchanged).
2. Introducing a new frontend framework; the dashboard stays vanilla JS.
3. Building a full analytics pipeline (this is an operator-facing summary, not long-term BI).

---

## 4. Proposed Solution Overview

### 4.1 Add a Finisher Stage for Task Jobs

Introduce a post-run stage:

* `moonmind.task.finalize` (or `moonmind.task.finish`)

This stage is responsible for producing:

1. A **Finish Summary** (small JSON) persisted with the job record.
2. A matching artifact `reports/run_summary.json`.
3. Optional `reports/errors.json` for failures (non-secret, compact).

### 4.2 Treat Proposals as a Finisher Output

Keep proposal generation and submission behavior, but record the results in the finish summary:

* Whether proposals were requested (`task.proposeTasks`)
* Whether proposal skills ran
* How many proposals were emitted and how many were successfully submitted
* If proposal submission failed, store a non-secret failure reason

### 4.3 Dashboard UI Uses `finishOutcome` + `finishSummary`

* Queue list/cards show an **Outcome** badge derived from `finishOutcome`.
* Detail page shows a “Finish Summary” panel:

  * outcome + reason
  * publish outcome (PR url / branch / skip reason)
  * proposals counts + link filtered to the run

---

## 5. Finish Summary Contract

### 5.1 Outcome Codes

`finishOutcome.code` MUST be one of:

* `PUBLISHED_PR`
* `PUBLISHED_BRANCH`
* `NO_CHANGES`
* `PUBLISH_DISABLED`
* `FAILED`
* `CANCELLED`

`finishOutcome.stage` SHOULD be one of:

* `prepare`
* `execute`
* `publish`
* `proposals`
* `finalize`
* `unknown`

`finishOutcome.reason` is a short string intended for list views and MUST be persisted as `finish_outcome_reason` so list endpoints do not depend on `finishSummary` payload inclusion.

### 5.2 JSON Shape

The finish summary JSON is a small object, intended to stay under ~4KB.

Example (`reports/run_summary.json` and DB `finishSummary` share the same shape):

```json
{
  "schemaVersion": "v1",
  "jobId": "00000000-0000-0000-0000-000000000000",
  "jobType": "task",
  "repository": "MoonLadderStudios/MoonMind",
  "targetRuntime": "codex",
  "timestamps": {
    "startedAt": "2026-02-24T12:00:00Z",
    "finishedAt": "2026-02-24T12:03:05Z",
    "durationMs": 185000
  },
  "finishOutcome": {
    "code": "NO_CHANGES",
    "stage": "publish",
    "reason": "publish skipped: no local changes"
  },
  "stages": {
    "prepare": { "status": "succeeded", "durationMs": 12000 },
    "execute": { "status": "succeeded", "durationMs": 150000 },
    "publish": { "status": "skipped", "durationMs": 4000 },
    "proposals": { "status": "not_run" },
    "finalize": { "status": "succeeded", "durationMs": 200 }
  },
  "publish": {
    "mode": "pr",
    "status": "skipped",
    "reason": "no local changes",
    "workingBranch": "task/20260224/abcd1234",
    "baseBranch": "main",
    "prUrl": null
  },
  "changes": {
    "hasChanges": false,
    "patchArtifact": "patches/changes.patch"
  },
  "proposals": {
    "requested": false,
    "hookSkills": [],
    "generatedCount": 0,
    "submittedCount": 0,
    "errors": []
  }
}
```

### 5.3 Secret Handling

Finish summaries MUST NOT contain:

* Tokens, API keys, credential-like strings
* Raw env var dumps
* Full command lines containing secret arguments

All text fields written into `finishSummary` / `finish_summary_json` MUST be passed through the existing redaction mechanism.

---

## 6. Storage and API Surfaces

### 6.1 Database Additions

Add additive columns to `agent_jobs`:

* `finish_outcome_code` (nullable string, ~64 chars)
* `finish_outcome_stage` (nullable string, ~32 chars)
* `finish_outcome_reason` (nullable string, ~256 chars)
* `finish_summary_json` (nullable JSONB)

Rationale:

* List views need `finishOutcome` without fetching artifacts.
* Detail view can show full finish summary without downloading `reports/run_summary.json`.

### 6.2 Schema Additions (Pydantic)

Update `moonmind/schemas/agent_queue_models.py`:

* `JobModel` adds:

  * `finishOutcomeCode` / `finishOutcomeStage` / `finishOutcomeReason`
  * `finishSummary` (optional; omitted in list responses unless explicitly requested)

Add optional finish metadata to worker mutation requests:

* `CompleteJobRequest` accepts optional `finishOutcomeCode`, `finishOutcomeStage`, `finishOutcomeReason`, `finishSummary`.
* `FailJobRequest` accepts optional `finishOutcomeCode`, `finishOutcomeStage`, `finishOutcomeReason`, `finishSummary`.
* `CancelJobAckRequest` accepts optional `finishOutcomeCode`, `finishOutcomeStage`, `finishOutcomeReason`, `finishSummary`.

### 6.3 API Behavior

* `GET /api/queue/jobs` (list):

  * include `finishOutcomeCode`, `finishOutcomeStage`, and `finishOutcomeReason`
  * by default **omit** `finishSummary` to keep responses small
* `GET /api/queue/jobs/{id}` (detail):

  * include `finishSummary` when present
* Optional: Add `GET /api/queue/jobs/{id}/finish-summary` returning the same JSON for clients that want a stable endpoint.

---

## 7. Worker Changes (CodexWorker and Future Runtimes)

### 7.1 Generate Finish Summary in Finalize Stage

In `moonmind/agents/codex_worker/worker.py`:

* Track stage timing (monotonic time) for:

  * prepare, execute, publish, proposals, finalize
* Build `finishSummary` at the end (even on failure/cancel, best-effort)
* Write:

  * artifact name `reports/run_summary.json` (always, best-effort)
  * artifact name `reports/errors.json` (only when failed; optional)

### 7.2 Determine Publish Outcome Reliably

Use `publish_result.json` when present to populate:

* `publish.status`: `published|skipped|failed|not_run`
* `publish.reason`
* `publish.prUrl`
* `publish.workingBranch`, `publish.baseBranch`

If `publish.mode=none`:

* `finishOutcome.code = PUBLISH_DISABLED` **only when prepare/execute completed successfully and publish was intentionally skipped**
* `publish.status = not_run`
* `finishOutcome.stage = publish`

If `publish.mode=none` and any earlier stage failed:

* keep `finishOutcome.code = FAILED` and the failing stage

If `publish_result.json` says `skipped: true` + reason `no local changes`:

* `finishOutcome.code = NO_CHANGES`

If PR URL exists:

* `finishOutcome.code = PUBLISHED_PR`

If branch publish succeeded without PR:

* `finishOutcome.code = PUBLISHED_BRANCH`

### 7.3 Capture Proposal Finisher Output

Update `_maybe_submit_task_proposals()` to return a small report:

```python
@dataclass
class ProposalSubmissionReport:
    requested: bool
    hook_skills: list[str]
    generated_count: int
    submitted_count: int
    errors: list[str]
```

* `generated_count` from the JSON array length in `task_proposals.json`
* `submitted_count` from submission loop increments
* `errors` contains short redacted reasons (never stack traces)

Populate finish summary `proposals` from this report.

### 7.4 Propagate Finish Summary to the API

Extend queue client calls so the terminal transition sends finish metadata:

* On success: `complete_job(..., finishOutcomeCode, finishOutcomeStage, finishOutcomeReason, finishSummary)`
* On failure: `fail_job(..., finishOutcomeCode="FAILED", finishOutcomeStage=<stage>, finishOutcomeReason, finishSummary)`
* On cancellation ack: `ack_cancel(..., finishOutcomeCode="CANCELLED", finishOutcomeStage="unknown", finishOutcomeReason, finishSummary)`

Also upload `reports/run_summary.json` before sending completion/failure, best-effort.

---

## 8. Dashboard UI Changes

### 8.1 Queue List / Active Views

Add one queue field definition:

* **Outcome**:

  * shows `finishOutcomeCode` as a badge (with a friendly label)
  * tooltip shows `finishOutcomeStage` + `finishOutcomeReason` from the list payload

Also add optional columns/fields (depending on layout constraints):

* `resultSummary` (short)
* `errorMessage` (short, only when failed)

### 8.2 Queue Detail Page

Add a “Finish Summary” panel near the top:

* Outcome code + stage + reason
* Publish:

  * PR URL (link) OR “No changes” OR “Publish disabled”
* Proposals:

  * `submittedCount` + link: `/tasks/proposals?originSource=queue&originId=<jobId>`

### 8.3 Proposals Page Filter: `originId`

Extend the proposals list API and dashboard UI to support filtering by job id:

* API: `GET /api/proposals?originSource=queue&originId=<uuid>`
* UI: read query params on load and set the initial filter state (so deep links work).

---

## 9. Migration and Rollout

### Phase 1 (UI-first, no DB changes required)

* Surface existing `resultSummary` / `errorMessage` in queue list/detail UI.
* Add Outcome computed client-side as a fallback heuristic.

### Phase 2 (Structured finish summary + DB fields)

* Add DB columns + API plumbing for `finishOutcomeCode` + `finishSummary`.
* Update worker to write `reports/run_summary.json` and send finish metadata on terminal transitions.
* Update UI to prefer structured fields and fall back when missing.

### Phase 3 (Proposals linkage polish)

* Add `originId` filtering to proposals list endpoint + UI query param parsing.
* Add “Proposals for this run” section to queue detail view.

---

## 10. Acceptance Criteria

1. `/tasks/queue` shows an **Outcome** for any terminal job:

   * Published PR/branch, No Changes, Publish Disabled, Failed (with stage), Cancelled.
2. Queue detail shows a **Finish Summary** panel without requiring artifact downloads.
3. A successful job with no repo changes is labeled **NO_CHANGES** (not “succeeded” without explanation).
4. A successful job with `publish.mode=none` is labeled **PUBLISH_DISABLED**.
5. A published PR job is labeled **PUBLISHED_PR** and shows the PR link.
6. Failed jobs show **FAILED** and include `finishOutcomeStage`.
7. Every task job produces `reports/run_summary.json` (best-effort).
8. Queue detail links to proposals filtered to that job’s origin id.

---

## 11. Testing Strategy

1. Worker unit tests (`tests/unit/agents/codex_worker/test_worker.py`):

   * success + publish none
   * success + no changes
   * success + PR publish
   * failure in execute
   * failure in publish
   * cancellation path
2. API unit tests:

   * completion endpoints accept finish metadata and persist
   * job list omits finishSummary but includes outcome fields
   * job detail includes finishSummary
3. Dashboard JS tests (or lightweight harness):

   * Outcome badge rendering from finishOutcomeCode
   * Deep link filters for proposals by originId
4. Manual smoke:

   * run one job of each outcome type and confirm list + detail behavior.

---

## 12. Open Questions

1. Should `finishSummary` always be stored in DB, or only as an artifact plus on-demand fetch?
2. Should we standardize `reports/run_summary.json` across *all* job types (task + manifest + orchestrator) to unify UI further?
3. Do we want to introduce a general `task.finishers[]` payload contract (future) to replace `task.proposeTasks`?

---

### Source anchors (not part of the doc)

* Queue job API model already has `resultSummary` and `errorMessage` fields available to surface in UI: 
* Task system already defines required artifacts including `publish_result.json` (good input for finish summary): 
* Spec workflow Celery tasks already emit `run_summary.json` at artifact root (task finisher adopts `reports/run_summary.json` as a task-specific convention): 
* Proposals list endpoint currently filters by `originSource` but not `originId` (we add it):

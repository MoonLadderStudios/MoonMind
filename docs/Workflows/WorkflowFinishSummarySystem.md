# Workflow Finish Summary System

**Document Class:** Canonical declarative  
**Status:** Desired-state contract  
**Owners:** MoonMind Engineering  
**Last Updated:** 2026-09-06

Related: `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/WorkflowPublishing.md`,
`docs/Temporal/ErrorTaxonomy.md`, `docs/Temporal/StepLedgerAndProgressModel.md`,
`docs/Workflows/NoCommitStatus.md`, `docs/RepositoryAccessAndWorkspaceDesign.md`

---

## 1. Summary

MoonMind requires a clear "what happened?" summary at the end of every
`MoonMind.UserWorkflow` execution so dashboard operators can quickly distinguish:

* **Published output**: verified PR, branch, or merge results.
* **No commit**: a verified publishing-context no-op because no repository commit was needed.
* **No local publication**: an explicit no-publication scope or an execution role without its own repository deliverable. These are different reasons and must be identified.
* **Coordinator result**: actual children queued, no eligible targets, partial dispatch, or dispatch failure, separately from descendant completion.
* **Failure**: the original cause and affected boundary, with independently verified saved work or remote results preserved.
* **Cancelled**: the cancellation outcome, without implying rollback of already performed effects.

`NO_COMMIT` replaces the older `NO_CHANGES` wording because workflows may still
perform non-repository side effects such as Jira issue transitions, comments,
verification records, or artifact publication. The finish summary must describe
repository publication separately from those side effects.

Explicit None is not dry run. A dry-run batch does not enqueue children; None
can permit independently authorized tracker and dispatch operations but cannot
permit repository publication by descendants. A non-publishing coordinator under
a PR scope is not an explicit-None batch. User-facing Auto and the coordinator's
or resolver's compiled mode are likewise different layers.

This document describes the target finish-summary contract executed during
finalization. It produces a structured, non-secret summary artifact and syncable
result payload for rapid UI indexing. It does not claim that current runtime
producers already implement every single-policy projection described here.

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

Legacy artifacts or compatibility adapters may still expose `NO_CHANGES`; named
historical readers normalize that alias to `NO_COMMIT` only when the reason is
that no repository commit was needed. New domain writes use the canonical value.

`PUBLISH_DISABLED` describes local compiled publication disposition. Its reason
must distinguish explicit scope-wide None from a coordinator/read-only role.
It is not the whole objective summary of a successful batch. The existing bounded
side-effect/child-result evidence supplies the coordinator's actual outcome;
this design does not introduce another root lifecycle state or a new summary store.

The system also logs `finishOutcomeStage` indicating the reached stage, such as
`prepare`, `llm_execution`, `publish`, or `finalizing`. A later reporting failure
cannot erase independently verified compute, save, push, PR, or merge evidence,
although an unmet required objective remains failed or blocked.

### 2.2 JSON Artifact Shape

Finish summary data is small and stored as JSON. `reports/run_summary.json` is
generated through the native workflow finalization path and uploaded to the
Artifact API before successful finalization. Required artifact failure is an
explicit preservation/finalization failure, not a fabricated summary reference.

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
  ]
}
```

This existing summary example shows a compiled local result, not a complete new
authored-input schema. Historical `jobId` naming in a summary is not a new
queue identity; workflow/run and exact-attempt references follow their owning
contracts. New projection fields evolve under the summary's versioning rules,
not by silently reinterpreting old bytes or hashes.

The `sideEffects` block is optional and bounded, but it is the preferred way to
make side effects visible when a run has no repository commit. A Jira Implement
run that explicitly establishes the issue is already implemented, completes its
trusted tracker operation, and creates no commit summarizes as `NO_COMMIT`, not
"no changes." See `docs/Workflows/NoCommitStatus.md` for the full lifecycle contract.

### 2.3 Failure Diagnostics Contract

Failed and canceled runs MUST include a bounded, redacted, operator-meaningful
failure reason. Generic Temporal wrappers such as `Activity task failed` or
`Child Workflow execution failed` are not acceptable as the operator-visible
reason. Walk the exception chain and surface the deepest non-generic root cause.

`finishOutcome.reason` is the canonical operator-facing failure string. For a
failed run it comes from structured failure diagnostics captured at the failure
boundary, not reconstructed from the terminal `summary` field alone.

Failed runs SHOULD additionally include a small secret-free `failure` object:

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

* `stage`: the stage active at capture: prepare, planning, executing, publish, or finalizing.
* `category`: aligns with `ExecutionTerminalStateInput.error_category` and `docs/Temporal/ErrorTaxonomy.md`: user_error, integration_error, execution_error, or system_error. ApplicationError types such as INVALID_INPUT, UnsupportedStatus, ProfileResolutionError, SlotAcquisitionTimeout, and RATE_LIMITED map to these categories.
* `source`: child_workflow, activity, or workflow.
* `stepId` / `stepTitle`: the failing plan node when applicable.
* `childWorkflowId`: the child identity when failure originated there.
* `message`: redacted bounded root cause, truncated to approximately 1000 characters.
* `rootCauseType`: the deepest non-generic observed exception class.
* `diagnosticsRef`: optional larger artifact-backed diagnostics, never a large embedded payload. The same rule applies to per-step lastError under StepLedgerAndProgressModel.

Failed runs MAY also include `failureSummary`. The `failure` object remains the
low-level captured diagnostic; `failureSummary` is the compact operator
classification rendered without parsing Markdown or long strings.

For MoonSpec publication gates:

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

Allowed MoonSpec categories are validation_environment_blocked for unavailable
required validation infrastructure, validation_evidence_missing for missing
current-head CI/lane evidence, and verification_gaps_remaining for concrete
implementation/validation gaps. These classifications cannot authorize a draft
PR outside the admitted scope policy.

For managed agent runtime failures:

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

When failure follows verified implementation or PR publication, preserve that
evidence in partialSuccess so the UI distinguishes implementation success with a
later failure from implementation failure. Such evidence must come from the
canonical verifier/publisher, not untrusted step metadata or a PR-shaped URL.

When `failure` exists, the `lastStep` block reflects the failing step rather than
stale prior work: id equals stepId, summary equals the diagnostic message,
lastError carries its category, and diagnosticsRef is included when available.

`execution.record_terminal_state` uses that same diagnostic for summary and
errorCategory so the projection and artifact agree about why the run failed.

#### First-failure-wins capture

Capture at the first boundary that identifies a non-generic root cause, such as
the handler around AgentRun or a plan-step Activity. Later generic re-raises do
not overwrite it. Independently verified results remain separately recorded;
first-failure-wins is not permission to discard prior remote/save facts.

### 2.4 Authored Policy, Local Disposition, and Descendant Results

The summary/read projection preserves four different meanings through the
existing input, plan, and result references:

| Information | Authoritative source | Presentation |
| --- | --- | --- |
| Authored selection | Immutable authored workflow input | Auto/default, explicit None, Branch, PR, or PR-and-merge |
| Resolved scope behavior | Pinned definition and compiler evidence | What this workflow/batch is admitted to produce, including finish/merge behavior |
| Local publication | This execution's compiled role and exact result | None, managed Branch/PR, or Skill-owned Auto, with owner and evidence |
| Descendant outcomes | Actual admitted child references and current/terminal results | Queued, pending, completed, published, merged, partial, blocked, failed, or unavailable as evidenced |

Do not overwrite the authored selection with a local mode. A batch authored PR
can have a coordinator with local None and PR-producing children. An Auto review
workflow can have a non-publishing coordinator and Skill-owned Auto resolver
children. Explicit root None, by contrast, forbids those publishing descendants.

The terminal coordinator artifact records its objective at completion: targets,
accepted child identities, policy/target provenance, skips, and errors. It does
not become a mutable aggregate artifact rewritten when children finish. Existing
live child-result projections may show later progress while the coordinator's
historical enqueue result remains immutable.

A successful enqueue is not proof of accepted publication evidence, child
completion, or a merge. If a composition awaits child completion, its own
terminal verdict includes that declared requirement. Otherwise, the summary
states that the coordinator completed dispatch and children are separately
inspectable. Partial dispatch includes every accepted child and the unresolved
errors; it is not a fabricated rollback or whole-batch success.

Skill-owned Auto consumes exact current-attempt publish and objective evidence.
A verified push does not complete a merge-required resolver. `fix_only` can
produce review_clean with pushed changes but must never report merged. Missing,
malformed, or stale evidence is not No Commit or PUBLISH_DISABLED.

Compute, artifact saving, local repository publication, merge automation, and
cleanup results remain independent. A saved result after publication failure is
usable saved work without successful publication. A retained local path is not
verified durable saving. No-publication scope cannot be bypassed by describing a
remote push as recovery; required save/cleanup follows the workspace contract.

Useful operator examples are:

> 12 child workflows queued. Each will create a PR against release/1.2. This coordinator publishes no repository changes.

> No commit was needed. The canonical Jira issue was updated successfully.

> Work was saved. Requested PR publication failed because destination access was revoked.

> Review is clean and fixes were pushed. The PR remains open because Merge when ready was disabled.

No generic “Publishing disabled” label may replace these materially different
outcomes. None is not dry run, and Auto is not an unconditional promise to merge.

### 2.5 Secret Handling

Finish summaries MUST NOT contain tokens, API keys, credential strings, or full
commands containing secret arguments. Redact strings before storage or sync.
This applies to structured failure messages, operatorSummary, step summaries,
policy provenance, and child metadata, including the established GitHub-token
scrubbing boundary. Do not expose raw runtime/credential handles merely because
they are useful for internal reconciliation.

### 2.6 Preset Summary Ownership

Presets do not own generic end-of-run narration. They emit structured facts such
as issue keys, actual PR URLs, verdicts, publication handoffs, child identities,
side-effect outcomes, and no-commit evidence. Those feed the canonical finalizer
and operator surfaces, not a competing summary.

The final operational preset step is its last required action, such as
verification or a tracker transition. Do not add an agent report step whose only
purpose is normal completion narration. Success, failure, cancellation, no-commit,
and coordinator outcomes stay on the same run_summary contract even when late
preset steps do not run.

---

## 3. Worker Implementation (Temporal Workflow)

UserWorkflow coordinates stage timing, captures failure/cancellation evidence,
preserves independent compute/save/publication facts, and invokes the trusted
artifact boundary for reports/run_summary.json. The typed terminal payload
populates existing execution source/projection fields such as
finish_outcome_code and finish_summary_json for UI indexing.

Only the finalizer derives the canonical outcome from admitted policy and
validated evidence. The last step, a helper-local default, restored old artifact,
or an auxiliary projection cannot redefine it. Summary upload/reporting retries
reconcile exact identities without rerunning successful compute or remote
publication. Required preservation failures remain actionable under the existing
workspace/cleanup owner.

## 4. Conformance

Tests cross the production publisher/Skill/fan-out, finalizer, artifact, API, and
UI-projection boundaries. They distinguish authored Auto from local Auto/None,
explicit None from coordinator None, dry run from no-publication, actual enqueue
from descendant success, verified no-op from missing evidence, and saved work
from successful publication. They preserve root-cause diagnostics, first-failure
capture, exact-attempt identities, secret redaction, historical aliases, and
verified partial results through reporting failure. A rendered label or a
summary-shaped object alone is not proof of those guarantees.

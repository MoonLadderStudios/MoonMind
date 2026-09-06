# No Commit Workflow Status

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow finalization, API, dashboard, and operator contributors  
**Authority:** no_commit/NO_COMMIT outcome semantics, local publication-disabled distinctions, and bounded historical alias interpretation. Workflow Publishing and the repository evidence owner define publication proof.  
**Owning Surface:** Workflow terminal outcome classification and its API/UI projections  
**Related Implementation:** `MoonMind.UserWorkflow`, `execution.record_terminal_state`, and existing finish-summary consumers.

Canonical for: no_commit lifecycle semantics, NO_COMMIT finish outcome, valid publishing-context completion without a repository commit, and side-effectful work that must not be described as “no changes.”

## Related Docs

- `docs/Workflows/WorkflowFinishSummarySystem.md`
- `docs/Workflows/WorkflowPublishing.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Api/ExecutionsApiContract.md`
- `docs/UI/WorkflowStatusColorSemantics.md`
- `docs/RepositoryAccessAndWorkspaceDesign.md`
- `docs/Workflows/LoreVcsIntegrationDesign.md`

## 1. Purpose

A workflow can complete meaningful work without producing a repository commit. For example, an implementation workflow may establish that the requested code already exists and complete the authoritative Jira issue. “No changes” hides that tracker effect. “No commit” describes the narrower repository result.

The outcome is determined from compiled role and verified result, not by comparing a user-facing Auto string or assuming a coordinator's local None represents the whole batch policy.

## 2. Canonical terms

| Layer | Canonical value | Meaning |
| --- | --- | --- |
| Exact lifecycle state | no_commit | Valid terminal completion without a needed repository commit |
| Finish outcome code | NO_COMMIT | Structured repository no-commit outcome |
| Publish status | skipped | Managed branch/PR creation was not needed |
| Publish reason code | no_commit | Stable reason, distinct from policy disable or failure |
| Compatibility dashboard grouping | completed | Coarse successful-outcome grouping |
| Temporal/API close status | completed | Successful terminal condition |

NO_CHANGES/no_changes are historical aliases only. New domain code, labels, and summaries use NO_COMMIT/no_commit. Coarse dashboard grouping does not change the exact lifecycle/dependency contract.

## 3. When to use no_commit

Use no_commit when the admitted repository-publishing objective reaches a valid no-op, authoritative evidence establishes no commit-worthy candidate was needed, no required publication was left undone, and required independent effects succeed or are explicitly best-effort under the composition.

Managed and Skill-owned publication use the shared `moonmind.publish.repository.v1` evidence and objective-specific terminal contract. The managed summary may project skipped/no_commit from canonical no_op_verified, but it is not a separate evidence format. A plain empty diff, successful process, or model claim is insufficient. A merge-required resolver cannot report success from a local no-op while the PR remains unresolved. A Lore Content-only revision is not No Commit merely because its generated Git projection has an empty diff.

Do not use no_commit for:

- explicit scope None or an execution with no repository-publishing role merely because it produced no commit;
- coordinator discovery/enqueue/no-target objectives, which have their own outcome evidence;
- unknown candidate eligibility, failed publication, missing/stale evidence, required side-effect failure, or cancellation;
- verified push, PR publication/adoption, or merge outcomes that have their own publication result.

PUBLISH_DISABLED records a local compiled no-publication disposition where appropriate. It does not prove that all descendants were prohibited from publishing. A coordinator can have local None under an authored PR scope and complete its enqueue objective while children continue independently.

## 4. Canonical Jira Implement example

The workflow's Auto resolves to its declared PR behavior, or the user explicitly selected PR. Implementation establishes the requested code is already present. The trusted tracker boundary completes the canonical issue when that already-implemented fact is explicit. The publication boundary verifies no repository commit was needed. The result is No Commit with the tracker effect shown separately.

```text
No commit was needed because the repository already matched the request. Jira was updated successfully.
```

An ambiguous no-diff result does not authorize the Jira transition. Required tracker failure remains failure even when no code change was needed.

## 5. Required structured result shape

A managed no-commit result uses structured projection fields, not prose parsing:

```json
{
  "state": "no_commit",
  "closeStatus": "completed",
  "temporalStatus": "completed",
  "dashboardStatus": "completed",
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
    {"kind": "jira", "status": "completed", "summary": "Issue transitioned to Done."}
  ]
}
```

The publish block here is a compiled local result summary, not the unified publication-evidence payload or a replacement for the authored input snapshot. Auto remains Auto with its resolved explanation in detail/reconstruction surfaces. Both managed and agent-owned evidence retain the canonical provider schema; projection booleans cannot substitute for exact revision, connection/client, and remote proof.

A PR reference must identify an actual PR. Existing target context is distinct from proof that this run created, updated, or merged it. Never infer publication from a URL alone or invent one for no-commit output.

## 6. Publish-stage behavior

The finalizer consumes the accepted unified repository-publication artifact and its exact-attempt/target association. It derives local summary mode, status, reasonCode, flags, target/revision, and PR identity from validated evidence. Both managed and Skill-owned no_op_verified results map to the compatible objective's NO_COMMIT outcome through this same reader.

Unknown provider comparison, inaccessible base, failed remote verification, and missing evidence cannot become no_commit. A candidate produced or published earlier in the workflow remains authoritative even if a later read-only step reports no local changes. Preserve the run-owned reference to validated `moonmind.publish.repository.v1`, not the retired `acceptedRepositoryEvidence` object or mutable raw branch/head metadata. Old formats remain frozen historical readers only.

## 7. Interaction with non-repository side effects

No Commit does not mean no Jira transition, issue/PR comment, tracker verification, artifact publication, notification, or other declared external effect. Expose bounded structured summaries separately. A required failed effect prevents success unless the definition explicitly makes it best-effort.

### 7.1 Batch coordinators

A coordinator under PR or Auto scope records targets and actual child enqueue outcomes. Its own compiled None means no local repository deliverable, not an authored tree-wide None. Appropriate objective summaries are children queued, verified no targets, partial dispatch, blocked, or failed.

```text
12 child workflows queued. Each will create a pull request against release/1.2. This coordinator publishes no repository changes.
```

Do not label that “No changes,” “Nothing happened,” or “Publishing disabled for this batch.” Enqueue success is not child completion, publication, or merge success. Link actual children and display their outcomes separately. Missing child results remain pending/unavailable, not inferred successful.

A Dependabot dry run reports would-queue results without creating children. That is not the same as None. A deliberate zero-target run requires the discovery/terminal contract's positive evidence, not an absent artifact interpreted as no-op.

### 7.2 Saved work

Save outcome, compute outcome, local publication outcome, and descendant outcomes are independently inspectable. None can produce useful saved files with PUBLISH_DISABLED as local repository disposition. Failed compute or publication can still have verified saved work without becoming successful compute/publication.

Required saving precedes destructive cleanup. None does not authorize an otherwise forbidden recovery push. A retained workspace is not equivalent to a verified artifact-backed save.

## 8. UI presentation

Use “No commit” with a reason such as “No repository changes were needed,” and include known side effects, for example “No commit · Jira updated.” Avoid “No changes,” unexplained “No publish,” or a generic Completed label that hides a valid no-commit publishing outcome.

Display the single authored selection and effective explanation separately from local compiled disposition. Examples include Auto → no repository changes needed, explicit None → saved without publication, and PR batch → coordinator queued children with child publication pending. Do not reconstruct a new draft from only a local result mode.

Colors and coarse groupings remain owned by WorkflowStatusColorSemantics. A successful save does not change failed publication to green, and a successful coordinator does not turn pending child results into success.

## 9. Backward compatibility

MM-1073 established the canonical no_commit/NO_COMMIT model; MM-1082 bounds remaining alias quarantine and repair.

```text
LEGACY_WORKFLOW_STATE_ALIASES:
  no_changes -> no_commit

LEGACY_FINISH_OUTCOME_ALIASES:
  NO_CHANGES -> NO_COMMIT
```

Only named inbound/durable-history readers may repair these aliases: Visibility mm_state reads, terminal-state historical inputs, finish summaries/memo/API serialization, and automation-run repository coercion. Direct canonical-domain callers reject them.

These maps do not translate provider, billing, model, effort, runtime, credentials, or publication policy. Historical literal Auto, old None-to-Auto behavior, and coordinator-versus-scope intent use the separate versioned authoring/history contract in Workflow Publishing.

Alias observation logs only bounded domain/alias/canonical fields, never entire summaries, prompts, search attributes, environments, or credentials.

### 9.1 Persisted inventory and repair path

| Surface | Legacy value | Repair boundary |
| --- | --- | --- |
| Visibility mm_state | no_changes | Read compatibility; canonical writes on next supported lifecycle update; closed histories remain historical |
| temporal_execution_sources.state and temporal_executions.state | no_changes | Repository/API sync before storage/serialization; repair persisted rows before enum retirement |
| automation_runs.status | no_changes | Migration 332_mm1024_no_commit_status and repository coercion |
| finish_summary_json.finishOutcome.code | NO_CHANGES | Finish-summary compatibility before indexing/serialization/persistence |
| finish_summary_json.publish.reasonCode and related JSON | no_changes | Only repository-publication absence maps to no_commit |
| Memo finishSummary/finish_summary | Nested legacy aliases | Projection sync through the named compatibility reader |

Supported old histories replay with their original command semantics. New workflow code emits canonical values directly. Removing old enum support requires proof that rows and durable JSON/history consumers no longer require it.

### 9.2 Conformance

Tests distinguish verified managed/Skill-owned no-op, technical failure, explicit None, coordinator enqueue/no-target outcomes, real publication, tracker failures, and saved work after failed compute/publication. They prove no local coordinator mode overwrites authored policy, no URL/process exit invents publication, and no alias repair changes authority. New evidence uses the unified provider schema, with Content-only and pending-projection Lore cases preserved. Exercise producer/finalizer/API/UI boundaries, not only display-string mappings.

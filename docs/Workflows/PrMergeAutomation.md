# PR Merge Automation - Child Workflow Resolver Strategy

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Owner:** MoonMind Platform  
**Updated:** 2026-09-06  
**Audience:** backend, workflow authors, API, Dashboard  
**Authority:** Parent-owned PR readiness/review scheduling, resolver-child handoffs, finish-mode lifecycle, and post-merge tracker completion. Portable Skills own resolver semantics; Workflow Publishing and repository-provider contracts own publication policy and evidence.  
**Owning Surface:** MoonMind.MergeAutomation and its UserWorkflow integration  
**Related Implementation:** `MoonMind.MergeAutomation`, `.agents/skills/pr-resolver/`, and the existing merge_automation Activities.  
**Related Docs:** `docs/Workflows/WorkflowDependencies.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/Workflows/RequiredCapabilities.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/TemporalAgentExecution.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/WorkflowPresetsSystem.md`, `docs/UI/CreatePage.md`, `docs/Workflows/LoreVcsIntegrationDesign.md`

This is a declarative target, not deployment or implementation evidence. [Workflow Publishing](WorkflowPublishing.md) owns the single authored publication policy and the distinction between authoring Auto and compiled Skill-owned `auto`. All new repository evidence uses the unified provider-neutral contract in [Lore VCS Integration Design section 3.13](LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence); this gate consumes it, not a separate Auto schema.

## 1. Purpose

PR merge automation is parent-owned subordinate orchestration. An implementation workflow publishes a PR, waits for external readiness, invokes the resolved `pr-resolver` Skill through ordinary execution, and remains nonterminal until the requested finish and required post-merge effects complete. An existing-PR workflow can enter the same gate without creating another PR.

Downstream workflows depend on the original parent workflowId, not a second top-level workflow discovered after publication. Required resolver capabilities are hard readiness requirements under the existing Required Capabilities contract.

## 2. Design Decision

`MoonMind.UserWorkflow` owns its `MoonMind.MergeAutomation` child and awaits the result. Merge automation owns durable scheduling, review-request effects, bounded waits, and resolver-child supervision. The resolved portable Skill remains the semantic owner of PR diagnosis, remediation, merge gating, and merge effects.

One authored publishing selection governs the whole scope. A managed PR-and-merge policy can derive PR publication for implementation, no direct repository publication for the gate/coordinator, and Skill-owned `auto` for the resolver. These are internal roles, not user overrides. A coordinator's compiled `none` never becomes a descendant prohibition or the value inherited by a resolver.

## 3. Goals

Preserve one parent dependency target, durable awaited completion, state-based rather than fixed-delay gating, ordinary Skill execution, exact-head review/evidence, observability, cancellation, artifacts, and replay compatibility. Reuse existing publication, execution, credential, and workspace owners instead of adding a parallel merger or policy service.

## 4. Non-Goals

No separate top-level follow-up dependency model, native replacement for `pr-resolver`, mid-flight broadening of admitted merge policy, or generalization into arbitrary non-PR output. Existing-PR adoption is supported, but explicit user None does not authorize pushing or merging merely because the adopting coordinator has no deliverable of its own.

The GitHub PR resolver flow described here is not authority to merge a Lore-backed repository through its generated GitHub PR. Provider-aware merge automation routes Lore work through the exact-revision coordinator contract in LoreVcsIntegrationDesign. A PR projection is not the repository mutation target, and an unqualified Git-only Skill cannot acquire Lore authority through this gate.

## 5. Summary of the Strategy

The compiler resolves a single authored policy and meaningful finish/review options, pins their definition evidence, and admits the relevant effects. Managed PR publication or trusted existing-PR resolution produces durable target context. The parent starts MergeAutomation and remains `awaiting_external`.

The gate waits for configured external review/check/Jira state, then starts an ordinary UserWorkflow resolver child. The child executes the pinned resolved Skill bundle in AgentRun. Its machine-readable result either completes the admitted objective, requests review for the exact head, re-enters a durable gate, or reports a blocker/failure.

After verified merge/already-merged, required Jira/GitHub completion runs through trusted issue activities. Merge finish succeeds only after those required effects succeed or no-op. Fix-only finish succeeds at a verified clean gate without merging or post-merge issue completion. Blocked/failed/expired fail the parent; canceled cancels it.

## 6. Why This Uses Child Workflows

### 6.1 Why not a separate top-level follow-up workflow

A separate follow-up creates another dependency target and makes original-parent success ambiguous. The gate is subordinate work owned and awaited by the original workflow, not a separately authored prerequisite.

### 6.2 Why not all inside one giant UserWorkflow

Implementation/publication, external waits, and repeated resolver attempts have distinct responsibilities. The existing child boundary keeps them durable and inspectable without expanding one workflow into a second resolver implementation.

### 6.3 Why the resolver itself is a child UserWorkflow

The child uses ordinary workspace/runtime preparation, logging, artifacts, admission, and Skill snapshots. It materializes the exact `pr-resolver` bundle and executes it through AgentRun. The Skill and packaged helpers own snapshots, comment retrieval/classification, remediation selection, retries, final merge checks, and terminal evidence.

The former `MoonMind.PRResolver` type is historical replay support only where required. New work must not select it or infer a native host from the Skill name or publication mode.

## 7. Workflow Topology

```text
UserWorkflow: one authored PR-and-merge intent
  |- implementation / verification / managed PR publication
  |- MergeAutomation: derived coordinator role
  |    |- head-bound external gate / review requests / tracker checks
  |    |- UserWorkflow: resolver attempt 1, compiled Skill-owned auto
  |    |    `- AgentRun: resolved pr-resolver Skill
  |    `- UserWorkflow: resolver attempt 2 when required, same scope intent
  |         `- AgentRun: resolved pr-resolver Skill
  `- terminal success only after requested finish and required effects
```

Existing-PR adoption replaces the initial implementation/publish phase with a trusted target-resolution step. Its coordinator-local None does not change the inherited allowed effects.

## 8. Workflow Type

`MoonMind.MergeAutomation` is the existing internal durable owner for this distinct long-lived behavior. It is not an additional user-selectable runtime, preset mode, or dependency type. Definition and rollout evidence belong to their existing implementation owners rather than a second migration framework.

## 9. Parent Workflow Behavior

### 9.1 Parent input contract

The new authored publication selection is `default`, `none`, `branch`, `pr`, or `pr_with_merge_automation` under the existing authored publish object. Auto is `default` or omission. The compiler preserves this snapshot separately from worker-bound `publishMode` and `mergeAutomation` configuration.

Explicit PR-and-merge, or an Auto default that declares it, compiles to managed `publishMode: pr` plus enabled automation. Existing-PR review/resolution defaults compile the coordinator's own publication to None and separately admit its declared descendant effects. Neither path is legal under an explicit scope-wide None selection when publishing resolver work is required.

A representative **compiled**, not independently authored, configuration is:

```json
{
  "publishMode": "pr",
  "mergeAutomation": {
    "enabled": true,
    "strategy": "child_workflow_resolver_v1",
    "finishMode": "merge",
    "resolver": {"skill": "pr-resolver", "mergeMethod": "squash"},
    "gate": {
      "github": {
        "waitForExternalReviewSignal": true,
        "requireStatusChecksReportedOnHead": true,
        "requireNoRunningChecks": true,
        "reviewProviders": []
      },
      "jira": {"enabled": false, "issueKey": null, "allowedStatuses": []}
    },
    "timeouts": {"fallbackPollSeconds": 120, "expireAfterSeconds": 86400}
  }
}
```

Runtime input never contains unresolved authoring `default`. Preset definitions describe requirements/defaults; they do not introduce a second publish selector or overwrite explicit intent. The gate cannot consult the retired workspace publish fallback to modify this frozen selection.

### 9.1.1 Entry points: publishing a PR versus adopting one

**Publish:** the implementation path creates a PR through the managed publisher and records exact target and candidate evidence.

**Adopt:** a trusted `github.resolve_pull_request_target` operation resolves one eligible existing open PR and records its URL, repository, head/base refs, and exact head SHA. No new PR is created. `pr-review-resolve` is the existing preset for the review-loop form of this path.

Task-queue routing follows the admitted automation requirement and qualified existing/new PR target, not `publishMode == pr` alone. Routing solely on that literal would strand an adopting coordinator whose local mode is None. A non-publishing workflow without admitted automation starts no gate.

Automation configuration belongs to the compiled workflow-level policy. Individual steps contain their declared roles and target handoffs, not independent editable automation policies. The gate owns scheduling/review requests; resolver Skills own their commit/push/merge semantics.

### 9.1.2 Finish mode

`mergeAutomation.finishMode` determines only the final effect once the same required clean gate is satisfied:

| Value | Behavior |
| --- | --- |
| `merge` | Resolver has admitted merge authority and returns merged/already_merged on verified success. |
| `fix_only` | Resolver still remediates, pushes, verifies, and checks gates, but returns review_clean without merging. |

Only omission takes the historical merge default. Invalid strings, casing, or types fail with `UNSUPPORTED_MERGE_AUTOMATION_FINISH_MODE` before a resolver starts. Never broaden an unknown value into merge.

Fix-only is not None and cannot make blockers, deferred comments, or pending checks successful. The child derives finish authority from the owning gate, not an independently editable Skill override. Direct standalone resolver task options use the same semantic contract outside this gate. No post-merge Jira/GitHub completion is attempted for fix-only.

### 9.2 Parent publish output

Durable PublishContext includes repository, prNumber, prUrl, baseRef, headRef, exact headSha, publishedAt or the qualified adoption observation, optional jiraIssueKey, and artifact provenance. Keep large evidence artifact-backed with a compact safe projection.

This GitHub-oriented scheduling context is a projection of validated target/publication facts, not an alternate repository-publication schema. New managed and agent-owned publishers emit `moonmind.publish.repository.v1`; the accepted artifact reference and exact-attempt association remain authoritative. Adoption records target evidence without inventing an earlier publication by this run. Provider-discriminated targets and revisions are not replaced by these display fields.

A PR URL alone is insufficient. Base and head are distinct; a resolver's head cannot become the implementation's original publication base. Target context and the inherited scope intent remain linked but separate.

### 9.3 Parent state behavior

The parent records the automation child ID and waits in `awaiting_external`. It reaches success only from the admitted terminal outcome. Additional stage markers use the normal search-attribute update contract, not an assumed new root lifecycle state.

## 10. MergeAutomation Input and Output

### 10.1 Input

Inputs include parent workflow/run identity, publishContextRef, frozen mergeAutomationConfig, scoped publication provenance, and the resolver launch template. Runtime/Profile identity comes from the parent's admitted selection. Repository/head/base is the validated PR target. The child receives newly admitted execution ownership, not reused credentials or the parent's whole execution plan.

Jira-backed PR work carries canonical jiraIssueKey and normally enables required postMergeJira completion. An explicit postMergeJira.issueKey is independently validated. GitHub-issue-backed work carries the canonical repository/issue number for required postMergeGithub completion. Neither operation belongs inside `pr-resolver`.

For Omnigent, compact `parentOmnigentExecutionPlan` is authority for preparing a fresh child plan, not itself the child plan.

### 10.2 Post-merge Jira completion

`merge_automation.complete_post_merge_jira` uses the trusted Jira boundary only after verified merge/already-merged. Target precedence is explicit configured issue key, normalized canonical jiraIssueKey, validated captured workflow/publish context, then strict exact-key PR-metadata fallback. No fuzzy summary search or bulk transition of every mentioned key.

An explicit transition ID must be available. An explicit name matches exactly, case-insensitively. Automatic selection requires exactly one available transition to Jira's done category. Required fields must be supplied by configured defaults; otherwise block. Already-done is a successful no-op. Required failed/blocked completion prevents terminal automation success with a Jira-sourced reason.

### 10.2.1 Post-merge GitHub completion

The trusted GitHub issue activity applies configured Done actions after verified merge, including closing, adding `status: done`, removing `status: code-review`, and confirming resulting state. PR closing keywords do not prove labels were applied. Repeated updates against an already closed issue are idempotent. Required failure remains a GitHub-sourced blocker.

### 10.2.2 Already-implemented no-change completion

For Jira PR-oriented work whose PR output is optional, explicit structured confirmation that the canonical issue is already implemented can invoke the same trusted completion boundary without manufacturing a PR. Canonical issue context, or the supported single strictly validated issue reference, is required. An ambiguous no-diff result causes no tracker mutation and explains that limitation.

### 10.3 Output

Summaries record selected issue, source of its identity, transition/actions, verified no-op or success, failure reason, and artifact refs without large tracker payloads or credentials. The full result also preserves target, cycles, resolver child IDs, last head, blockers, and observed publication disposition.

### 10.4 Terminal status summary

Allowed statuses are merged, already_merged, review_clean, blocked, failed, expired, and canceled. Review-clean is valid only for admitted fix-only and is not a merge. Artifact references preserve exact-attempt evidence after projection lag or host removal.

## 11. MergeAutomation Lifecycle

### 11.1 States

Use initializing, awaiting_external, executing, finalizing, completed, failed, and canceled. No extra root state is needed.

### 11.2 Durable loop

Load admitted context, evaluate external scheduling readiness, wait by signal/bounded timer when necessary, start one deterministically identified resolver child, await it, validate its disposition/evidence, then complete required effects or return to the gate. Child acceptance/process exit is not completion of the PR objective.

## 11.3 Automated review loop

### 11.3.1 Purpose

Review evidence must cover the actual head being merged. A remediation push invalidates earlier head-bound review. `fix-comments` remains one bounded remediation pass and never requests or waits for review. `pr-resolver` decides the semantic transition; MergeAutomation owns the review-request side effect and durable wait.

### 11.3.2 Configuration

```json
{
  "reviewLoop": {
    "enabled": true,
    "provider": "codex",
    "requestMode": "pr_comment",
    "requireFreshReviewForEveryHead": true,
    "requestAfterRemediation": true,
    "maxCycles": 5,
    "maxConsecutiveNoProgressCycles": 2
  }
}
```

Provider is neutral metadata. Trusted `pr_resolver_core.review_providers` defines the command and accepted identities. An explicit command can only restate the registered command exactly. Children cannot supply arbitrary comment text or another provider. Enabled loops pass reviewProvider and requireFreshReview into the pinned Skill's inputs.

### 11.3.3 Request side effect

`merge_automation.request_automated_review` is the sole review-request operation. The request binds owning automation workflow, repository/PR, expectedHeadSha, provider, and a stable key derived from those identities.

The Activity claims that key and original attempt window, rereads the PR to verify open/current head, returns any recorded request on retry, reconciles matching comments by the configured identity after the original attempt began, then posts only when absence is established. Persist comment ID/time/actor/head/key. A lost GitHub POST response must not trigger blind reposting; an unprovable result remains blocked/failed. A settled requested row is never downgraded by later reporting failure.

### 11.3.4 Binding the result to the request

Without an active request, the automated-review gate may allow the first resolver to decide whether review is needed. With an active request, only that request's result opens it: same current head, trusted provider identity, completion after requestedAt, matching review commit where supplied, and reaction on the exact request comment or the qualified unchanged-head after-request fallback. Historical results are not fallback evidence.

A changed head invalidates the pending request and requires the governed head-update/re-entry path. Record per-cycle provider/head, request key/comment/time, completion identity/kind/time, and outcome. Never reuse another cycle's evidence merely because it is recent.

An active request owns the unchanged head until review completion. CI failures,
merge conflicts, and older actionable comments do not bypass that wait. Review
evidence is collected independently of these repairable conditions; missing or
unknown completion evidence keeps the gate closed. The portable Skill gives
the same wait precedence over remediation, after terminal evidence validation
and deferred-comment blockers. A head changed externally invalidates
the request and re-enters the gate for the new revision.

Completion includes the provider's submitted review, its request-bound clean
comment (for Codex, `Codex Review: Didn't find any major issues. 🚀`), or its
qualified clean-review reaction. Ordinary clean comments and PR-level reactions
must be newer than the request on its unchanged head. Pending, unknown, blank,
or dismissed review states, eyes reactions, quoted clean messages, and stale
responses are not completion. The Skill reads every page of review/reaction
evidence and refreshes the full comment inventory after observing completion,
then revalidates the remote head. Merge operations require that verified head
to still match. When a request has multiple provider response comments, the
latest authoritative response takes precedence over an earlier failure or clean result.
Once that head has a completed review and no remaining blockers, it finishes
according to finishMode without requesting another review for the same head.

### 11.3.5 No-progress and termination rules

The Skill emits a signature of head plus sorted outstanding actionable/deferred comment IDs. Repeated signatures, unchanged actionable comments, deferred/unfixable comments, exhausted cycle budget, unprovable review request, ownership/expected-head conflict, and expiry stop through explicit reasons such as review_loop_no_progress, deferred_comments, review_cycle_budget_exhausted, automated_review_request_failed, or expired.

A no-op fix pass is successful only when the latest required review covers the current head and no actionable comments remain. Merge finish continues until merged/already-merged plus required tracker effects. Fix-only finishes at verified review-clean without merge. Neither changes its finish mode at runtime to make a gate green.

## 12. Merge Gate Evaluation

### 12.1 Gate inputs

External scheduling reads cover PR state/current head, reported/running checks, configured review completion, and optional Jira state. These compact observations determine whether to launch the resolver, not permission to merge.

Completed failing checks and merge conflicts are resolver-actionable. Once required check reporting is complete and no relevant checks are running, failing results can launch remediation rather than leave the gate waiting indefinitely.

### 12.2 Gate semantics

Readiness is head-sensitive. A new push invalidates prior review/check completion for the affected contract. Only the resolved Skill performs the final fresh semantic merge checks.

### 12.3 Callback-first, polling fallback

GitHub/Jira signals are preferred; bounded timer reconciliation and Continue-As-New preserve durable progress. No fixed-delay follow-up or unbounded spin loop substitutes for evidence.

### 12.4 Gate output contract

Gate results contain status, current head, safe typed blockers, and readyToLaunchResolver. They do not claim merge authorization or become arbitrary input for a different PR.

## 13. Resolver Child Workflow Strategy

### 13.1 Resolver child type

The gate starts an ordinary child UserWorkflow selecting `task.tool = {type: skill, name: pr-resolver}`. Its **compiled publication mode is `auto` with owner agent**, not None. The inherited scope already admits the allowed existing-PR effects. Only the gate/coordinator's own lack of repository deliverable derives local None.

This distinction prevents both duplicate managed publishing and false “publishing disabled” semantics. No parent None-to-Auto coercion is used to authorize the resolver. The Skill's metadata, exact target, and evidence contract are validated by the shared compiler.

### 13.2 Resolver child payload

A representative **compiler-produced** child fragment is:

```json
{
  "workflowType": "MoonMind.UserWorkflow",
  "initialParameters": {
    "publishMode": "auto",
    "requiredCapabilities": ["git", "gh"],
    "timeoutPolicy": {"timeout_seconds": 9000},
    "task": {
      "tool": {"type": "skill", "name": "pr-resolver"},
      "inputs": {"repo": "owner/repo", "pr": "123", "mergeMethod": "squash", "finishMode": "merge"},
      "timeoutPolicy": {"timeout_seconds": 9000}
    }
  }
}
```

This fragment is not an independently authored override and omits the complete repository target, scope provenance, and runtime plan for readability. Repo/PR/head/base and finish values are derived from trusted context. The child timeout covers the Skill's 7200-second default finalize budget plus preparation/artifact time; the default 9000 seconds is carried at workflow and plan-node/task levels.

For Omnigent, a bounded preparation Activity validates parent authority, preserves Runtime/Profile configuration, derives the exact resolver target/workspace and inherited policy, resolves the appropriate pinned Skill closure, and persists a new plan owned by the deterministic child ID. The child carries that plan and resolvedSkillsetRef. A new child-owned snapshot cannot silently substitute changed semantic definitions for the scope's admitted requirements.

The parent plan is never reused as the child's plan. Historical inputs without compact parent binding resolve it from canonical parent execution evidence under the versioned decoder. Missing/ambiguous plan, profile, task, workspace, or Skill authority fails before host creation or credential acquisition. A flat profile ID does not select a legacy alternate host.

### 13.3 Resolver child result contract extension

The resolver artifact, normally `var/pr_resolver/result.json`, includes mergeAutomationDisposition: merged, already_merged, review_clean, reenter_gate, request_review, manual_review, or failed. Missing/malformed required result evidence is not generic child success.

Review-clean requires admitted fix_only, an exact remotely verified branch revision in unified repository evidence, a permitted non-merge publication action, and the resolver's own result plus a live PR observation proving that the PR remains unmerged. Contradictory merge evidence fails `UNAUTHORIZED_MERGE_EVIDENCE`; unverified remote revision fails the shared parser. The Skill's live check emits blocked `unmerged_pr_verification_unavailable` when merged or unreadable state prevents no-merge proof. Do not require the retired Auto schema's `merged` boolean in `moonmind.publish.repository.v1`; semantic merge/no-merge fields belong to the resolver result, while publication mechanics use the shared schema. Review-clean and merged may both exit zero, so structured evidence distinguishes them.

A reenter_gate result is a durable handoff, not proof the PR merged. It carries completionDisposition gated_continuation and normalized gatedContinuation. The gate waits until the Skill-authored notBefore; old handoffs without timing use fallbackPollSeconds. The exact review-grace deadline is not recomputed or extended by the gate. Same-session continuation support is irrelevant to this parent-owned handoff.

Only the synthetic PR_RESOLVER_REENTER_GATE terminal-contract failure can be cleared by an authorized handoff. Provider, auth, rate-limit, infrastructure, timeout, cancellation, stale-evidence, and malformed-evidence failures remain their own failures.

A request_review `gated-continuation/v2` names only the configured provider, exact head, Step Execution reference, and progress signature. The parent validates owner workflow/run/type, actual child workflow/run, exact executionRef in the continuation/terminal envelope, reason, timing, provider, and head against authoritative results. The accepted publication artifact is bound to that same current attempt through trusted artifact provenance, not a reintroduced legacy evidence field. Safe evidence exposes accepted/rejected handoffs, timing source, wait/cycle counters, and legacy fallback use.

Adapters preserve qualified gate-owned continuation, including supported historical next_step values such as run_fix_comments_skill, run_fix_ci_skill, run_fix_merge_conflicts_skill, retry_finalize_after_backoff, or wait_for_ci_and_retry_finalize. They do not infer a gate owner from a nonzero process exit alone. Long transient waits emit bounded progress output so healthy waiting is not confused with a stuck process.

### 13.4 Ungated resolver runs must not report continuation as success

The mergeGate owner names the actual MergeAutomation Temporal parent, not the root UserWorkflow. A standalone resolver has no such gate. An ungated continuation disposition cannot report successful PR resolution; it fails/blocks with a direction to use the supported automation path or finish manually. Terminal verified merge/already-merged and qualified fix-only outcomes retain their objective-specific semantics.

## 14. Post-Resolver Re-Gating

A resolver push requires fresh external evidence for the new head when configured. The Skill reports the appropriate handoff and MergeAutomation returns to awaiting_external before another resolver attempt.

Lost or incomplete result delivery is reconciled against the exact admitted PR and authoritative artifacts before repeating effects. Independently verified already-merged state can satisfy the merge fact, subject to the remaining required evidence and tracker gates. An observed head advancement can be progress for a qualified retry/re-entry, not blanket proof of success or permission to erase an auth/cancel/manual-review failure.

Valid manual_review or failed dispositions remain terminal failures even if the head changed. A new head alone cannot upgrade failed compute, validate stale publish evidence, or authorize a duplicate merge.

## 15. Resolver Skill Authority

The external gate schedules the Skill; it does not decide that the PR is authorized to merge. The resolved bundle is the sole semantic implementation for fresh PR snapshots, comments, completeness, blockers, remediation, and final merge. Cross-implementation comparison tests cannot justify two competing resolvers.

Before any resolver child has launched, the gate may adopt the latest head through a fresh authorized readiness observation. After launch, changes use the declared resolver disposition and re-entry/reconciliation contract. Scope, repository/PR identity, finish authority, and policy remain pinned. No arbitrary latest-head substitution changes targets.

## 16. Dependency Semantics

The original parent workflowId remains the dependency target, and it does not complete until its requested automation outcome is satisfied. A dependency edge itself carries no publication-policy inheritance.

Review-clean satisfies a fix-only parent's stated objective but does not put its code on the PR base. A dependent batch that needs predecessor code must require verified merge or another declared candidate/checkpoint handoff. Changing a batch to PR-only or fix-only cannot silently remove that requirement.

## 17. Terminal Outcome Rules

### 17.1 Parent success

Merge finish succeeds on merged/already-merged with required post-merge effects. Fix-only succeeds only on validated review-clean without a merge or post-merge mutation. Preserve exact-attempt remote facts separately from compute/save/reporting outcomes.

### 17.2 Parent failure

Blocked, failed, and expired fail the parent; canceled cancels it. Only the dependency contract's successful terminal state releases a normal prerequisite gate. A push or PR URL is not enough.

### 17.3 Future extension

Separate implementation-complete versus full-objective-complete lifecycle states are not introduced here. The existing state and typed result contracts express the distinction without new root states.

## 18. Cancellation Semantics

Parent cancellation propagates to MergeAutomation and its in-flight resolver child through existing child-workflow controls. Cleanup is truthful and bounded. Cancellation acceptance is not proof of process teardown or rollback of an already performed push/merge. Preserve required saved work and remote evidence under admitted authority. Explicit None never acquires a recovery-push exception.

## 19. Continue-As-New

Preserve parent workflow/run lineage, publication-scope intent and definition evidence, publish context, PR identity, latest tracked head, finish/gate policy, issue targets, active review request/cycles, blockers, resolver attempts, and original expiry deadline. Rollover does not refresh defaults, widen finish authority, or reset review-request idempotency.

Historical None-labelled resolver payloads and old publication-evidence schemas remain interpreted only under their original recorded contracts for supported replay. New child compilation explicitly uses Skill-owned Auto and the unified evidence schema. A new default must not rewrite old history or cause incompatible workers to reinterpret policy. Fresh public resolver authoring uses default/omission, not a claimed legacy auto ingress.

## 20. Visibility and Artifacts

### 20.1 Parent workflow detail

Show automation status, PR link, blockers, head, cycle, resolver attempt links, and requested finish. The authored publication selection remains visible separately from coordinator and resolver modes. A local None is not displayed as a root “do not publish” selection when the scope permits resolver effects.

Resolver titles use the deterministic one-based attempt ordinal, such as Resolve PR #123 (Attempt 1), matching the child ID cycle rather than creating another attempt identity.

### 20.2 Child artifacts

Preserve `reports/merge_automation_summary.json`, gate snapshots, resolver-attempt artifacts, and review-cycle artifacts under `artifacts/merge_automation/`. Evidence is bounded, safe, and available after host removal. The publication result path can remain `artifacts/publish_result.json` while its new payload uses the one provider-neutral schema.

### 20.3 Root terminal summary

`reports/run_summary.json` includes automation enabled/status, target, child IDs, cycles, finish behavior, and required tracker outcomes. It does not infer success from projection freshness or silently omit blocked child work.

## 21. UI Contract

There is one publishing control. PR with merge automation is the single compound selection; Auto can resolve that declared default with a concrete explanation. Advanced review/wait/Jira settings specialize that admitted behavior without a second independent enable flag or per-step publish override.

This is not a new dependency, scheduling, runtime, or profile wizard. Unsupported selections are corrected before launch. Explicit None cannot enter a publishing resolver merely because the coordinator is locally non-publishing.

### 21.1 pr-review-resolve preset

Fix and Review Loop targets an existing PR through the workflow's repository context. It collects one PR locator, not another generic repository selector. Trusted target resolution supplies head/base/URL and rejects ambiguity or unsupported locality.

Auto uses the declared existing-PR review/fix protocol. The coordinator compiles to local None; resolver children compile to Skill-owned Auto. The review loop defaults to provider codex. The meaningful Finish with pr-resolver / Merge when ready option is off by default and maps to fix_only; on maps to merge. Review-cycle budget and expiry remain progressively disclosed. The preset's merge method stays pinned to squash rather than introducing a duplicate control.

The form explanation states that fixes are pushed in both cases and that merge is optional. Neither setting is equivalent to user None. Both target paths require full normal admission and terminal evidence, not hidden enablement or a claimed implementation based on this document alone.

## 22. Rejected Alternatives

Fixed-delay follow-up, a separate dependency target, a second native resolver, direct semantic execution inside the gate, and two publish selectors are rejected. The existing child substrate and one compiled scope preserve durability and portability without leaking internal roles into authoring.

## 23. Acceptance Criteria

Conformance covers actual parent/compiler/gate/Skill/result boundaries:

1. New-PR and existing-PR entry points use the same admitted gate and correct worker routing.
2. One authored selection survives expansion, child creation, UI/detail reconstruction, and rerun.
3. The parent waits for requested finish and required tracker effects.
4. Resolver children explicitly compile to Skill-owned Auto, with no managed duplicate publisher and no new None-to-Auto coercion.
5. Explicit None or incompatible finish/target choices fail before prohibited effects.
6. Head-bound review requests reconcile lost acknowledgements without blind duplicate posting and accept only qualifying request-specific results.
7. New pushes re-enter the correct gate; no-progress, deferred comments, expiry, and unprovable requests remain explicit terminal outcomes.
8. Merge and fix-only use the same gates, with disposition-specific no-merge evidence and no post-merge effects for fix-only.
9. Ungated continuation cannot become false-green success. Malformed/stale/auth/cancel failures are not cleared by handoff metadata or a changed head.
10. Omnigent children get new child-owned plans preserving parent Runtime/Profile and admitted context without reusing parent plan ownership.
11. Dependencies require the intended code handoff, not merely an open PR or review-clean status.
12. Cancellation, Continue-As-New, historical payloads, saved work, and projection lag preserve policy and verified facts without repeated effects.
13. All new publication consumers accept only the unified provider schema with actual connection/client/attempt/remote proof. Retired Auto booleans/accepted-evidence objects are not reintroduced as live alternatives, and GitHub projection state never authorizes a Lore merge.

A documentation or schema update alone does not demonstrate runtime or protected-live conformance.

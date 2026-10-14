# PR Merge Automation - Child Workflow Resolver Strategy

**Owner:** MoonMind Platform  
**Audience:** backend, workflow authors, API, Dashboard  
**Covers:** Parent-owned PR readiness/review scheduling, resolver-child handoffs, finish-mode lifecycle, and post-merge tracker completion. Portable Skills own resolver semantics; Workflow Publishing and repository-provider contracts own publication policy and evidence.  
**Related Implementation:** `MoonMind.MergeAutomation` (`moonmind/workflows/temporal/workflows/merge_automation.py`), `.agents/skills/pr-resolver/`, and the `merge_automation` Activities.  
**Related Docs:** `docs/Workflows/WorkflowDependencies.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/Workflows/RequiredCapabilities.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/TemporalAgentExecution.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/WorkflowPresetsSystem.md`, `docs/UI/CreatePage.md`, `docs/Workflows/LoreVcsIntegrationDesign.md`

[Workflow Publishing](WorkflowPublishing.md) owns the single authored publication policy and the distinction between authoring Auto and compiled Skill-owned `auto`. Repository evidence uses the provider-neutral contract in [Lore VCS Integration Design section 3.13](LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence); this gate consumes it rather than a separate schema.

## 1. Purpose and Design

PR merge automation is parent-owned subordinate orchestration. An implementation workflow publishes a PR, waits for external readiness, invokes the resolved `pr-resolver` Skill through ordinary execution, and stays nonterminal until the requested finish and required post-merge effects complete. An existing-PR workflow can enter the same gate without creating another PR, and its review-only finish requests fresh review without invoking a resolver or publishing.

`MoonMind.UserWorkflow` owns its `MoonMind.MergeAutomation` child and awaits the result. Merge automation owns durable scheduling, review-request effects, bounded waits, and resolver-child supervision; the resolved portable Skill owns PR diagnosis, remediation, merge gating, and merge effects. Downstream workflows depend on the original parent workflowId, and for resolver modes the required resolver capabilities are hard readiness requirements under [Required Capabilities](RequiredCapabilities.md).

One authored publishing selection governs the scope. A PR-and-merge policy derives PR publication for implementation, no direct publication for the gate/coordinator, and Skill-owned `auto` for the resolver. These are internal roles, not user overrides, and a coordinator's compiled `none` never becomes a descendant prohibition.

The design reuses existing publication, execution, credential, and workspace owners rather than adding a parallel merger or policy service. It does not provide a separate top-level follow-up dependency, a native replacement for `pr-resolver`, mid-flight broadening of merge policy, or automation for arbitrary non-PR output. Explicit user None does not authorize pushing or merging just because the adopting coordinator has no deliverable of its own.

This GitHub resolver flow never merges a Lore-backed repository through its generated GitHub PR: provider-aware merge automation routes Lore work through the exact-revision coordinator contract in Lore VCS Integration Design, a PR projection is not the mutation target, and a Git-only Skill cannot acquire Lore authority through this gate.

## 2. Strategy Summary

The compiler resolves the authored policy and finish/review options, pins definition evidence, and admits the relevant effects. Managed PR publication or trusted existing-PR resolution produces durable target context; the parent starts MergeAutomation and stays `awaiting_external`. At the handoff, the canonical GitHub PR URL must name the workflow's authored repository, so a result URL cannot supply repository authority or lend its PR number to a different repository.

For merge and fix-only, the gate waits for configured review/check/Jira state, then starts an ordinary UserWorkflow resolver child that runs the pinned Skill bundle in AgentRun. Its machine-readable result completes the objective, requests review for the exact head, re-enters the gate, or reports a blocker. Review-only uses the owned review-request and evidence operations directly and completes on fresh current-head review.

After verified merge or already-merged, required Jira/GitHub completion runs through trusted issue Activities, and merge finish succeeds only after those succeed or no-op. Fix-only succeeds at a verified clean gate without merging or post-merge effects. Review-only succeeds with review_complete, including when the review has findings. Blocked, failed, and expired fail the parent; canceled cancels it.

A resolver may repair and merge a newer head than the original candidate. On either merged disposition the gate re-reads the tracked PR through the repository readiness Activity before post-merge effects, because the resolver's success claim or a stale returned head cannot substitute for remote observation. Issue finalization independently checks repository, PR, head, branch, issue association, merge, and completion target.

## 3. Why Child Workflows

A separate top-level follow-up would create a second dependency target and make the original parent's success ambiguous. Folding everything into one UserWorkflow would mix implementation/publication, external waits, and repeated resolver attempts into one history and duplicate resolver logic. The child boundary keeps each durable and inspectable.

The resolver is itself a child UserWorkflow so it gets ordinary workspace/runtime preparation, logging, artifacts, admission, and Skill snapshots. It materializes the exact `pr-resolver` bundle and runs it through AgentRun; the Skill and its helpers own snapshots, comment classification, remediation selection, retries, final merge checks, and terminal evidence. The `MoonMind.PRResolver` workflow type remains only for replaying recorded histories; new work does not select it or infer a native host from the Skill name or publication mode.

```text
UserWorkflow: one authored PR-and-merge intent
  |- implementation / verification / managed PR publication
  |- MergeAutomation: derived coordinator role
  |    |- head-bound external gate / review requests / tracker checks
  |    |- UserWorkflow: resolver attempt 1, compiled Skill-owned auto
  |    |    `- AgentRun: resolved pr-resolver Skill
  |    `- UserWorkflow: resolver attempt 2 when required, same scope intent
  `- terminal success only after requested finish and required effects
```

Existing-PR adoption replaces the implementation/publish phase with trusted target resolution; its coordinator-local None does not change inherited allowed effects.

### 3.1 Review-only authority

For review-only adoption the scope permits no repository publication, and the gate requests and awaits fresh review without resolver children or remediation. The single-tool graph is admitted as a native collaboration consumer regardless of whether the selected agent can consume repository credentials. The compiler verifies it against the frozen task-input snapshot and grants only read and review_request for the selected connection; that native authority cannot be reused by an agent or donated to children.

Readiness and review-request Activities consume the parent plan's admitted collaboration snapshot through the shared bound credential service. Read plus review_request yields GitHub App pull-requests write and Issues read (for request comments and reaction polling) without contents or branch write, and review-only skips unrelated CI APIs. Trusted native Activities read artifacts as their real workflow/run through the artifact admission helper. Missing or revoked authority never falls back to an ambient account, and restored completed reviews still need a live observation that the admitted head remains open.

A forbidden reaction read may still be satisfied by a qualified fresh review or clean-review comment. Without either, the gate returns policy_denied with a sanitized permission diagnostic rather than waiting until expiry; rate limits and transient errors keep normal retry handling.

## 4. Parent Workflow Behavior

`MoonMind.MergeAutomation` is an internal durable owner, not a user-selectable runtime, preset mode, or dependency type.

### 4.1 Input and entry points

The authored selection (`default`, `none`, `branch`, `pr`, `pr_with_merge_automation`) is preserved separately from worker-bound `publishMode` and `mergeAutomation`. Explicit PR-and-merge, or an Auto default declaring it, compiles to `publishMode: pr` plus enabled automation; existing-PR defaults compile the coordinator's own publication to None and separately admit descendant effects. Neither is legal under explicit scope-wide None when publishing resolver work is required. A representative compiled (not authored) configuration:

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

Runtime input never contains unresolved `default`, and presets describe defaults without adding a second selector.

**Publish:** the implementation path creates a PR through the managed publisher and records target and candidate evidence. **Adopt:** trusted `github.resolve_pull_request_target` resolves one eligible open PR and records its URL, repository, head/base refs, and exact head SHA; `pr-review-resolve` is the preset for this path. Task-queue routing follows the admitted automation requirement and qualified PR target, not `publishMode == pr`, because routing on that literal would strand an adopting coordinator whose local mode is None. Automation configuration lives in the compiled workflow-level policy, not per-step.

### 4.2 Finish mode

| `finishMode` | Behavior |
| --- | --- |
| `merge` | Resolver has admitted merge authority and returns merged/already_merged on verified success. |
| `fix_only` | Resolver remediates, pushes, verifies, and checks gates, but returns review_clean without merging. |
| `review_only` | Gate requests fresh review of the current head and returns review_complete when the reviewer completes, including with findings. No resolver runs. |

Only omission takes the merge default. Invalid strings, casing, or types fail with `UNSUPPORTED_MERGE_AUTOMATION_FINISH_MODE` before a resolver starts, so an unknown value is never broadened into merge. Fix-only is not None and cannot make blockers, deferred comments, or pending checks successful; the child derives finish authority from the owning gate rather than a Skill override.

Review-only compiles to publication None and admits only repository read and `review_request`. It requires a supported configured reviewer, rejecting disabled or unsupported review before effects, and admits no writes, destination, work branch, resolver, merge, or post-merge effects. It is a gate finish mode, not a portable `pr-resolver` mode. Its StartInput requires typed `parentExecutionPlan` bound to the admitted parent; caller-supplied tokens, ambient credentials, and arbitrary connections cannot supply or broaden that authority.

### 4.3 Publish context and parent state

PublishContext holds repository, prNumber, prUrl, baseRef, headRef, exact headSha, publishedAt or the adoption observation, optional jiraIssueKey, and artifact provenance, with large evidence artifact-backed. It is a projection of validated target facts, not an alternate publication schema: the accepted `moonmind.publish.repository.v1` artifact remains authoritative, and adoption records target evidence without inventing an earlier publication. A PR URL alone is insufficient, and a resolver's head never becomes the original publication base.

The parent records the automation child ID and waits in `awaiting_external`, reaching success only from the admitted terminal outcome. Stage markers use normal search attributes rather than a new root state.

## 5. MergeAutomation Input and Output

Inputs include parent workflow/run identity, publishContextRef, frozen mergeAutomationConfig, scoped publication provenance, and, for resolver modes, the resolver launch template. Runtime/Profile identity comes from the parent's admitted selection, and a resolver child receives newly admitted execution ownership rather than the parent's credentials or plan. For Omnigent, compact `parentOmnigentExecutionPlan` is the authority for preparing a fresh child plan, not the child plan itself.

Jira-backed work carries canonical jiraIssueKey and normally enables post-merge Jira completion; an explicit `postMergeJira.issueKey` is validated independently. GitHub-issue-backed work carries the repository/issue number for post-merge GitHub completion. Neither operation lives inside `pr-resolver`.

### 5.1 Post-merge tracker completion

`merge_automation.complete_post_merge_jira` runs through the trusted Jira boundary only after verified merge/already-merged. Target precedence is explicit configured key, canonical jiraIssueKey, validated workflow/publish context, then a strict exact-key PR-metadata fallback — never fuzzy summary search or transitioning every mentioned key. An explicit transition ID must be available; an explicit name matches exactly (case-insensitive); automatic selection requires exactly one transition to Jira's done category. Missing required fields block. Already-done is a successful no-op, and a required failure prevents automation success with a Jira-sourced reason.

The trusted GitHub issue Activity applies configured Done actions — closing, adding `status: done`, removing `status: code-review` — and confirms the result, since PR closing keywords do not prove labels were applied. Repeated updates against a closed issue are idempotent.

For Jira PR work whose PR output is optional, explicit structured confirmation that the issue is already implemented can invoke the same completion boundary without a PR. An ambiguous no-diff result causes no tracker mutation and explains why.

### 5.2 Output

Summaries record the selected issue and identity source, transitions/actions, outcome, failure reason, and artifact refs, without large tracker payloads or credentials. The full result also keeps target, cycles, resolver child IDs, last head, blockers, and publication disposition. Terminal statuses are merged, already_merged, review_clean (admitted fix-only only), review_complete (admitted review-only only; proves fresh current-head review, not a clean gate or merge), blocked, failed, expired, and canceled.

## 6. Lifecycle

States: initializing, awaiting_external, executing, finalizing, completed, failed, canceled.

For merge and fix-only, the loop loads admitted context, evaluates readiness, waits by signal or bounded timer, starts one deterministically identified resolver child, validates its disposition and evidence, then completes required effects or returns to the gate. Child acceptance or process exit is not completion of the PR objective.

A completed CI failure admits the resolver even while other checks are queued or running, so a failed build does not wait for its downstream CI Gate before `pr-resolver` can choose `fix-ci`. Incomplete checks without a confirmed failure keep waiting, and admission does not certify merge readiness; head, policy, evidence, Jira, and active-review barriers still apply. Readiness records `actionableCiFailuresVersion: "v1"` and a `readinessObservationId`, and the workflow records a per-observation patch decision so recorded observations replay with their original wait decision while the next fresh poll adopts the failure-aware rule — without repeating publication or merge effects or requiring an operator signal.

### 6.1 Review-only lifecycle

The gate pins the admitted open PR's current head, requests review through `merge_automation.request_automated_review`, and awaits request-bound completion. Earlier review does not count. CI, conflicts, findings, and Jira readiness neither trigger repair nor delay this objective. A changed head blocks with stale_revision without retargeting. The head is revalidated before returning review_complete. Restored request evidence must match this gate's provider, pinned head, request key, comment ID, timestamp, and cycle, and completion must name that head with a qualifying ID, kind, and timestamp after the request.

## 7. Automated Review Loop

Review evidence must cover the admitted head, and merge modes require it for the head being merged, so a remediation push invalidates earlier review. `fix-comments` is one bounded remediation pass and never requests or waits for review. For resolver modes, `pr-resolver` decides the semantic transition while MergeAutomation owns the review request and durable wait.

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

Trusted `pr_resolver_core.review_providers` defines each provider's command and accepted identities; an explicit command can only restate it, and children cannot supply comment text or another provider. Enabled loops pass reviewProvider and requireFreshReview to the Skill; disabled loops pass `none`/false and the prompt says the gate owns review. The gate rejects a child's review request when no loop runs, because a child that inferred a review requirement from prose would otherwise wait every pass for a review nobody requests.

### 7.1 Request side effect

`merge_automation.request_automated_review` is the only review-request operation. A request binds the automation workflow, repository/PR, expectedHeadSha, provider, and a stable key derived from them. The Activity claims the key, rereads the PR to confirm it is open at that head, returns any recorded request on retry, reconciles matching comments posted after the original attempt began, and posts only when absence is established. Comment ID, time, actor, head, and key are persisted. A lost POST response never triggers a blind repost, and a settled request is never downgraded by a later reporting failure.

### 7.2 Binding results to requests

Without an active request, merge and fix-only let the first resolver decide whether review is needed; review-only always requests. With an active request, only that request's result opens the gate: same head, trusted provider identity, completion after requestedAt, matching review commit where supplied, and a reaction on the exact request comment or a qualified unchanged-head fallback. Older results are never reused because they are recent. A changed head invalidates the request: resolver modes re-enter the gate for the new revision, review-only blocks. Per cycle, MoonMind records provider/head, request key/comment/time, completion identity/kind/time, and outcome.

An active request owns the unchanged head until completion; CI failures, conflicts, and older comments do not bypass the wait, and missing completion evidence keeps the gate closed. The portable Skill applies the same precedence after terminal evidence validation and deferred-comment blockers.

Completion is the provider's submitted review, its request-bound clean comment, or its qualified clean-review reaction. A clean comment opens with the provider's clean-result sentence (for Codex, `Codex Review: Didn't find any major issues.`); the rest of that line is variable flair, and any further text must be P2/medium-or-below findings, which still need a disposition. When the comment names its reviewed commit, that commit must be the current head. Pending, unknown, or dismissed states, eyes reactions, quoted clean messages, responses with P0/P1 findings, and stale responses are not completion. The Skill reads every page, refreshes the comment inventory after completion, and revalidates the head; merges require that head to still match. With multiple provider responses, the latest authoritative one wins.

A provider comment refusing the request on its unchanged head (for Codex, a usage-limit notice such as `You have reached your Codex usage limits for code reviews.`) is authoritative but not completion. The gate and Skill share `pr_resolver_core.review_providers` to classify replies as either the registered clean result or a refusal carrying a registered failure marker; status tables, task replies, and findings are non-authoritative. When the latest authoritative response is a refusal and nothing completes the request, the gate blocks with non-retryable `automated_review_request_failed`, and the Skill's snapshot reports `requestFailed`/`requestFailure` and classifies it as `manual_review` rather than waiting.

A newer request supersedes the refusal but consumes the same review-cycle budget before adoption, so an external request cannot bypass an exhausted budget. Superseded records stay visible, and restored request/cycle identities must agree on provider, head, key, comment, and creation time; contradictory known times fail closed.

Unavailable evidence is distinct from absence: a refusal becomes terminal only when every completion route was read successfully and none qualified. Retryable quota, service, and transport failures keep waiting; confirmed access denial keeps the policy blocker. Completion and refusal are both rechecked against the current open PR and a refreshed request inventory, and a superseding request leaves the gate pending for a fresh observation.

The portable snapshot reclassifies the refreshed inventory after completion or refusal and fails closed after three refreshes if it keeps changing. A proven refusal settles only the selected cycle as `failed`, recording comment ID, time, and class in `requestFailure`; failed reads, policy denial, and mismatched identity never masquerade as a refusal, and failed, superseded, or stale cycles cannot be reopened.

Once the head has a completed review, review-only finishes even with findings; merge and fix-only also need no remaining blockers. Completion never triggers another request for the same head.

### 7.3 No-progress and termination

The Skill emits a progress signature of head plus sorted outstanding actionable/deferred comment IDs. Repeated signatures, unchanged actionable comments, deferred or unfixable comments, an exhausted cycle budget, an unprovable or refused review request, ownership/head conflicts, and expiry stop with explicit reasons such as review_loop_no_progress, deferred_comments, review_cycle_budget_exhausted, automated_review_request_failed, or expired.

Readiness carries ordered, deduplicated request receipts from the retained active request through the latest one; each unseen receipt consumes a cycle, including requests superseded between polls, and completion is attributed only to the latest adopted request. Contradictory identities remain unavailable evidence, and older observations without receipts keep their recorded interpretation through an observation-scoped patch.

A no-op fix pass succeeds only when the latest required review covers the current head and no actionable comments remain. Neither finish mode changes at runtime to make a gate green.

## 8. Merge Gate Evaluation

For merge and fix-only, readiness reads PR state and head, reported and running checks, configured review completion, and optional Jira state. These decide whether to launch the resolver, not whether merging is authorized; only the resolved Skill performs the final semantic merge checks. Review-only uses PR/head and request-bound review evidence only. Readiness is head-sensitive: a new push invalidates prior review and check completion.

Confirmed failing checks and merge conflicts are resolver-actionable. A head with no reported check runs or gating statuses has no CI evidence yet — whether CI is still queuing after a push or never runs for the base — so the gate keeps waiting rather than launching a resolver that would read `ci_signal_degraded` and hand straight back. Only advisory status on a base with no required checks counts as clean without gating checks. Gate and Skill share this rule (`pr_resolver_core.github_checks.head_ci_reported`). Repeated confirmed absence on the same head consumes `maxConsecutiveNoProgressCycles`, even with the review loop off, and exhaustion returns `review_loop_no_progress` with a CI-trigger diagnostic; a changed head or newly reported CI resets it. A merge conflict does not skip check observation or required review.

GitHub/Jira signals are preferred; bounded timer reconciliation and Continue-As-New preserve progress, and no fixed-delay follow-up or unbounded spin substitutes for evidence. Gate results contain status, current head, typed blockers, and readyToLaunchResolver; they never claim merge authorization or serve as input for a different PR.

## 9. Resolver Child Workflow

For merge and fix-only, the gate starts an ordinary child UserWorkflow with `task.tool = {type: skill, name: pr-resolver}` whose **compiled publication mode is `auto` with owner agent**, not None. This avoids both a duplicate managed publisher and false "publishing disabled" semantics: the inherited scope already admits the existing-PR effects, and only the gate's own lack of a deliverable derives local None. Review-only never starts this child. A representative compiler-produced fragment:

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

Target and finish values come from trusted context. The 9000-second default covers the Skill's 7200-second finalize budget plus preparation and artifact time, and is carried at workflow and task levels.

For Omnigent, a bounded preparation Activity validates parent authority, preserves Runtime/Profile configuration, derives the resolver target, workspace, and inherited policy, resolves the pinned Skill closure, and persists a new plan owned by the deterministic child ID; the child carries that plan and resolvedSkillsetRef. The parent plan is never reused as the child's. Missing or ambiguous plan, profile, task, workspace, or Skill authority fails before host creation or credential acquisition.

### 9.1 Result contract

The resolver artifact, normally `var/pr_resolver/result.json`, includes mergeAutomationDisposition: merged, already_merged, review_clean, reenter_gate, request_review, manual_review, or failed. Missing or malformed required evidence is not generic child success, which matters because review-clean and merged can both exit zero.

Review-clean requires admitted fix_only, an exact remotely verified branch revision in the unified evidence, a permitted non-merge action, and the resolver's own result plus a live observation that the PR remains unmerged. Contradictory merge evidence fails `UNAUTHORIZED_MERGE_EVIDENCE`, and the Skill emits blocked `unmerged_pr_verification_unavailable` when merged or unreadable state prevents no-merge proof. Merge/no-merge semantics belong to the resolver result, while publication mechanics use the shared schema. Review and reaction collection reads every page with the GitHub CLI; a failed or malformed page is a collection failure, never an empty inventory.

A `manual_review` with reason `merge_gate_requires_human_approval` is a gate decision the parent owns, not a runtime failure: the resolver cleared every blocker it owns but cannot supply a required approving review, so the adapter passes the verdict through and the parent reports it. Failing the child would replace a complete result with a generic error. Other `manual_review` reasons remain agent failures. When a resolver child fails before returning a result, the terminal summary keeps its bounded, redacted error and diagnostic reference without treating the child as successful or relaunching it unchanged.

`request_review` and `reenter_gate` consume `maxConsecutiveNoProgressCycles` when their progress signature repeats — `reenter_gate` even with the review loop disabled — so a resolver repeatedly reporting the same blocker (such as `ci_signal_degraded` on a base CI never runs for) stops instead of launching identical runs. Handoffs without a signature use head and reason. Changed work resets the counter; elapsed waits do not count as progress. Exhaustion preserves evidence and reports an actionable blocker.

A reenter_gate result is a durable handoff, not proof of merge. It carries completionDisposition gated_continuation and a normalized gatedContinuation; the gate waits until the Skill-authored notBefore (or fallbackPollSeconds without timing) and does not extend the review-grace deadline. Such a child shows as **Handed off** in the workflow list and detail while its Temporal execution remains completed; failed or canceled runs never get that label. Only the synthetic PR_RESOLVER_REENTER_GATE terminal-contract failure can be cleared by an authorized handoff; provider, auth, rate-limit, infrastructure, timeout, cancellation, and stale or malformed evidence failures remain failures.

A request_review `gated-continuation/v2` names only the provider, exact head, Step Execution reference, and progress signature. The parent validates owner workflow/run/type, actual child workflow/run, executionRef, reason, timing, provider, and head against authoritative results, with the publication artifact bound to the same attempt through artifact provenance. Adapters preserve qualified gate-owned continuation, including recorded next_step values such as run_fix_comments_skill, run_fix_ci_skill, run_fix_merge_conflicts_skill, retry_finalize_after_backoff, and wait_for_ci_and_retry_finalize, but never infer a gate owner from a nonzero exit alone. Long waits emit bounded progress so healthy waiting is not mistaken for a stuck process.

### 9.2 Ungated resolver runs

The mergeGate owner names the actual MergeAutomation parent, not the root UserWorkflow. A standalone resolver has no gate, so an ungated continuation disposition cannot report successful PR resolution; it fails or blocks with direction to use automation or finish manually. Verified merge/already-merged and qualified fix-only outcomes keep their meaning.

## 10. Re-Gating and Head Changes

A resolver push requires fresh external evidence for the new head when configured; the Skill reports the handoff and MergeAutomation returns to awaiting_external before another attempt. Lost or incomplete results are reconciled against the admitted PR and artifacts before repeating effects. Independently verified already-merged state can satisfy the merge fact subject to remaining evidence and tracker gates. Head advancement can count as progress for a qualified re-entry, but a valid manual_review or failed disposition stays terminal even if the head changed, and a new head cannot upgrade failed compute, validate stale evidence, or authorize a duplicate merge.

For resolver modes, the gate schedules the Skill but does not decide the PR may merge; the resolved bundle is the sole semantic implementation. Before any resolver launches, the gate may adopt the latest head through a fresh authorized readiness observation; after launch, changes go through the resolver disposition and re-entry contract. Scope, repository/PR identity, finish authority, and policy stay pinned. Review-only keeps its admitted head and blocks if it changes.

## 11. Dependencies, Outcomes, Cancellation, and Continue-As-New

The original parent workflowId is the dependency target and does not complete until its requested automation outcome is satisfied; a dependency edge carries no publication policy. Review-clean satisfies a fix-only objective but does not put code on the base, and review-complete proves neither a clean PR nor code availability, so a dependent batch needing predecessor code requires verified merge or another declared handoff.

Merge finish succeeds on merged/already-merged with required post-merge effects; fix-only on validated review-clean without merge or post-merge mutation; review-only on current-head, request-bound review_complete without resolver or publication effects. Blocked, failed, and expired fail the parent; canceled cancels it. A push or PR URL never releases a prerequisite.

Parent cancellation propagates to MergeAutomation and its in-flight resolver through child-workflow controls. Accepted cancellation is not proof of teardown or rollback of a performed push or merge, and required saved work and remote evidence are preserved under admitted authority; explicit None never gets a recovery-push exception.

Continue-As-New preserves parent lineage, scope intent and definition evidence, publish context, PR identity, latest head, finish and gate policy, issue targets, active review request and cycles, blockers, resolver attempts, and the original expiry. Rollover never refreshes defaults, widens finish authority, or resets review-request idempotency. Recorded None-labelled resolver payloads and older evidence schemas are interpreted only under their original contracts for replay; new resolver children compile to Skill-owned Auto with the unified schema.

## 12. Visibility and Artifacts

Parent detail shows automation status, PR link, blockers, head, cycle, resolver attempt links, and requested finish, with the authored selection shown separately from coordinator and resolver modes; a local None is not displayed as a root "do not publish" when the scope permits resolver effects. Resolver titles use the deterministic one-based attempt ordinal matching the child ID, such as `Resolve PR #<number> (Attempt 1)`.

`reports/merge_automation_summary.json`, gate snapshots, resolver-attempt artifacts, and review-cycle artifacts are kept under `artifacts/merge_automation/`, bounded, safe, and available after host removal. `reports/run_summary.json` includes automation status, target, child IDs, cycles, finish behavior, and tracker outcomes, and does not infer success from projection freshness or omit blocked child work.

## 13. UI Contract

There is one publishing control. PR with merge automation is a single compound selection, and Auto may resolve to it with a concrete explanation. Advanced review/wait/Jira settings specialize that behavior without a second enable flag or per-step override, and unsupported selections are corrected before launch.

The `pr-review-resolve` preset (Fix and Review Loop) targets an existing PR through the workflow's repository context with one PR locator; trusted resolution supplies head, base, and URL and rejects ambiguity. Auto uses the existing-PR protocol: local None for the coordinator, Skill-owned Auto for resolver children. The review loop defaults to provider codex. "Merge when ready" is off by default (`fix_only`); on maps to `merge`. Advanced options expose merge method (default squash), resolver repair budget (default 5), review-cycle budget, and expiry, and explicit harness, model, effort, and Provider Profile selections survive compilation. `review_only` (default false) selects review-only with publication None and a required fresh reviewer; the form explains it requests review and reports completion without changing source, publishing, merging, or completing trackers. With review_only false, the form explains that fixes are pushed in both finish choices and merge is optional.

The `batch-pr-resolver` Skill adopts this preset with `review_provider=none` and merge finish. Its coordinator owns durable CI/provider waits: a resolver pass returns execution-bound `reenter_gate` evidence and releases its agent slot while Temporal waits. Standalone portable runs keep their bounded foreground retry loop.

## 14. Rejected Alternatives

Fixed-delay follow-up, a separate dependency target, a second native resolver, direct semantic execution inside the gate, and two publish selectors are all rejected. The child-workflow substrate and one compiled scope provide durability and portability without leaking internal roles into authoring.

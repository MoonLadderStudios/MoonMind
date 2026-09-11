# GitHub Issue Status State Machine

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed (legacy cutover §12 implemented; remainder desired behavior)  
**Owners:** MoonMind Platform + Workflow Runtime + GitHub Integration  
**Updated:** 2026-09-09  
**Audience:** Workflow, preset, GitHub adapter, recovery, and dashboard contributors and operators  
**Authority:** GitHub issue lifecycle labels, selection eligibility, attempt handoffs, and cross-deployment recovery behavior. Existing execution, checkpoint, publishing, and merge contracts retain their respective authority.  
**Owning Surface:** Trusted GitHub issue operations and workflow terminal/reconciliation boundaries  
**Related Implementation:** `moonmind/workflows/temporal/github_issue_lifecycle.py`, `moonmind/workflows/temporal/github_issue_legacy_cutover.py`, `moonmind/workflows/temporal/activities/github_issue_legacy_cutover_activities.py`, `moonmind/workflows/temporal/github_issue_search.py`, `moonmind/workflows/temporal/story_output_tools.py`, `moonmind/workflows/adapters/github_service.py`, and `api_service/data/presets/github-issue-*.yaml`. Legacy reconciliation behavior is specified in [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md).

**Related Docs:** [Workflow Presets System](WorkflowPresetsSystem.md), [Workflow Publishing](WorkflowPublishing.md), [Checkpoint Branch System](CheckpointBranchSystem.md), [Workflow Remediation](WorkflowRemediation.md), [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md), and [PR Merge Automation](PrMergeAutomation.md).

This document defines desired behavior, not implemented or deployment-tested capability. Implementation tracking and rollout notes belong in issues or temporary execution artifacts, not this design.

## 1. Purpose and design decision

A GitHub issue represents an objective that can outlive several workflow attempts. A failed attempt must not leave that objective permanently in progress, discard a useful partial PR, or disappear without a visible next action.

MoonMind uses a small label-based state machine, one identifiable comment per attempt, and ordinary GitHub PR/branch references. There is no coordination branch, coordination JSON file, distributed lease service, or shared deployment database requirement.

The supported topology includes three independent MoonMind deployments on different devices. GitHub is their only shared coordination surface. No deployment assumes access to another deployment's Temporal service, PostgreSQL database, artifacts, filesystem, or live agent sessions.

**Available work has no MoonMind status label. There is no required `status: todo` or `status: ready`.** Availability means eligible for assessment, not approved for unrestricted execution and not proof that no previous work exists.

**Coordination is explicitly best-effort.** Labels and comments support discoverable ownership announcements, failure handoffs, conflict detection, and conservative recovery. They do not provide an exclusive distributed lock or fence an obsolete writer. Simultaneous duplicate starts remain possible. Silent owners are not automatically replaced. Strict single-writer enforcement would require a separately approved coordination capability and is not an implicit promise of this state machine.

## 2. State vocabulary

The four canonical open-issue status labels are:

| State | GitHub representation | Meaning | Search and Implement admission |
| --- | --- | --- | --- |
| Available | Open issue with no MoonMind status label | No active or exceptional lifecycle state is declared | Potentially eligible after direct reads, prior-work inspection, and other gates |
| In progress | `status: in-progress` | An attempt is preparing, assessing, implementing, verifying, or repairing work | Excluded from new independent implementation |
| Recovery needed | `status: recovery-needed` | A stopped attempt left a safe, explicit continuation handoff | Eligible for continuation, subject to evidence, budget, and cooldown |
| Code review | `status: code-review` | A verified implementation has been published for the admitted review/merge journey | Excluded from ordinary implementation search |
| Needs attention | `status: needs-attention` | An unresolved decision, stop request, conflict, unsafe recovery, or exhausted budget requires intervention | Excluded from automatic implementation |
| Closed | GitHub issue state is closed | GitHub records a terminal disposition | Excluded regardless of labels |

Ordinary labels such as bug, feature, priority, or component are independent of this state machine. Existing dependency and blocker rules continue to apply. An issue without a status label can still be blocked by its prerequisites or outside the user's requested scope.

The existing configured `status: done` label may accompany verified completed closure. It is terminal presentation, not another open-work admission state. A closed issue with a not-planned disposition is not reported as successfully implemented. An open issue carrying `status: done` is inconsistent and is not silently treated as available.

### 2.1 State interpretation

A settled open issue has either no canonical status label or one canonical status label. Label mutations are not transactional, so intermediate combinations can occur. Every combination of multiple canonical status labels blocks new automatic admission until reconciled from evidence.

`status: needs-attention` always blocks admission. It may deliberately coexist with `status: in-progress` while the old writer's stop status is unknown. This means attention is required, not that another deployment may take over.

An unrecognized workflow-status value is not equivalent to no status. It requires classification rather than silent admission. Superseded labels are not newly emitted, and historical or manually applied labels are not bulk-cleared without evidence. Unknown ownership remains unknown even when the label looks familiar.

## 3. Transition contract

Transitions require authenticated GitHub reads, validated attempt evidence, and the admitted operation policy. A workflow's terminal status and the issue's next-work state are distinct facts.

| From | Evidence or event | To | Required outcome |
| --- | --- | --- | --- |
| Available | Candidate passes admission and announces its attempt | In progress | Preserve the exact selected issue and inspect prior work before editing |
| Recovery needed | Prior writer is stopped, handoff is usable, and continuation is admitted | In progress | Bind the new attempt to its predecessor and preserved work |
| In progress | Internal retry, remediation iteration, or legitimate execution wait | In progress | Keep the same attempt active rather than release between steps |
| In progress | Implementation satisfies the controlling gates and PR publication is verified | Code review | Link the exact PR/head and record who owns any remaining review journey |
| In progress | Attempt ends with unfinished, portable work and automatic continuation is safe | Recovery needed | Stop writers, preserve work, and publish the continuation handoff |
| In progress | Attempt ends with no work to preserve and fresh retry is safe | Available | Retain failure history and retry restrictions before removing the active status |
| In progress | Stop is uncertain, evidence is missing, recovery is unsafe, or budget is exhausted | Needs attention | Preserve blocking information and explain the required intervention |
| In progress or Code review | Objective is verified satisfied on its intended destination | Closed | Apply the existing authorized completion policy, including no-change completion when qualified |
| Code review | Existing owner or explicitly admitted PR repair starts editing | In progress | Continue the same PR under the existing review/repair contract |
| Code review | Review owner ends before the remaining authorized work is complete | Recovery needed or Needs attention | Record whether the next action is PR repair, verification, or review/merge continuation |
| Any open state | Intentional cancellation or operator hold | Needs attention | Do not automatically schedule a replacement or reset the issue to available |
| Needs attention | Authorized resolution establishes a safe next action | Available, Recovery needed, Code review, or Closed | Record the resolution and preserved-work disposition before releasing the hold |
| Closed | Human or authorized policy reopens the issue | Reassess | Do not infer fresh work or completion from old labels |

All other transitions require an explicit decision under the existing authority contracts. In particular, no timeout alone authorizes a transition from In progress to Available or Recovery needed.

A workflow whose admitted finish target is a completed PR handoff may end successfully in Code review without an active implementation owner. A PR-and-merge parent can remain awaiting review under its existing contract. Lack of new code during that wait is not by itself a stalled attempt.

A failure after successful implementation or merge does not cause reimplementation. The remaining task can be status synchronization or final reporting. The source workflow remains failed when appropriate, while the issue reflects independently verified completion evidence.

## 4. Attempt comments and evidence

### 4.1 One comment per attempt

Each attempt has a globally unique attempt ID, a stable installation-specific deployment ID, and its exact workflow/run identity. A GitHub account name cannot substitute for deployment identity because all devices may use the same account.

The trusted integration creates one human-readable, machine-identifiable issue comment for that attempt. It updates its own comment during execution and leaves it as a terminal handoff afterward. Separate attempts do not overwrite one shared summary comment or rewrite each other's history.

The comment carries only the information required to interpret and continue the work:

| Information | Contract |
| --- | --- |
| Attempt identity | Attempt ID, deployment ID, workflow/run identity, and format version |
| Lineage | Predecessor attempt/comment when continuing previous work |
| Activity | Preparing, active, awaiting review, releasing, released, or attention, with last reported activity |
| Stop evidence | Whether all associated writers have stopped and publication requests have a known outcome |
| Preserved work | PR identity, exact verified head/base, and any GitHub-accessible saved branch/commit |
| Result | Failure or completion category, met/unmet requirements, and a concise verification summary |
| Next action | Fresh retry, continue implementation, verify, continue review, finalize status, or obtain operator attention |
| Retry eligibility | Applicable retry history, remaining allowance, cooldown, and any operator hold |

This is an attempt handoff, not another workflow database. Large logs, prompts, secrets, provider credentials, and runtime session material do not belong in comments. Private dashboard URLs and local artifact references are optional diagnostics, never the sole cross-device recovery input.

A marker and body do not authenticate themselves. The integration validates comment provenance, issue identity, schema, and relevant GitHub objects. Issue text and arbitrary comments remain untrusted input. Shared credentials do not establish an adversarial security boundary between deployments.

### 4.2 Write and release behavior

Progress updates are coalesced and rate-bounded rather than emitted on every runtime poll. Activity timestamps support observation, not expiring ownership.

Comment creation and updates are serialized per attempt within the owning deployment. A retried create first checks for the same attempt marker. A lost HTTP response is an unknown result, not proof that the operation failed. Duplicate comments with the same attempt identity are one logical attempt, not extra retry allowance. Conflicting copies require reconciliation, not choosing whichever timestamp is newest.

A terminal handoff records a proposed disposition before removing blocking labels. It becomes released only after the writer is stopped, preservation is verified or explicitly absent, pending shared mutations are settled, and the intended label transition has been observed.

Attempt comments are append-only. Deployments and operators do not delete attempt comments. When redaction is unavoidable, the redacting party first posts a GitHub-visible tombstone retaining the attempt ID, deployment ID, format version, retry/no-progress disposition, and successor lineage pointer, so admission can still detect prior history and request attention rather than infer a clean slate. Deletion without such a tombstone is detectable only through a lineage gap (a successor referencing a missing predecessor), a status label without its required handoff, or another observable marker; deletion of the sole history on an issue that has already returned to Available leaves no such marker and is indistinguishable from an issue that was never attempted (see §§5.3 and 10).

An interrupted release is recoverable from the comment and GitHub evidence. Another deployment may finish that bookkeeping only when the recorded terminal stop and mutation outcomes are conclusive. Otherwise it requests attention.

A released attempt performs no further issue-state or PR writes. A resumed old process rereads GitHub before effects and stops when its attempt is released, superseded, held, or in conflict. This rejects observable stale work, but it is not a fence against every delayed external request.

## 5. GitHub Issue Search and Implement

The preset has one admission policy for both fresh and recovery work. Explicit issue workflows, scheduled searches, manual submissions, and retries do not gain a bypass around that policy.

Search results and cached local records are candidate discovery aids. Direct issue, comment, and PR reads control admission. The absence of an in-progress label alone is insufficient.

### 5.1 Candidate eligibility

An issue is eligible only when it is open, within the authorized repository/query scope, free of applicable blockers, in Available or Recovery needed state, and not contradicted by unresolved active attempts, operator holds, unknown prior work, or exhausted retry restrictions.

Recovery candidates remain within the user's requested search and priority scope. Eligible continuations receive bounded preference without allowing one repeatedly failing issue to monopolize every run. Code-review issues are routed to the existing PR follow-up journey when explicitly requested, not rediscovered as untouched implementation work.

A bounded reconciliation scan also examines managed in-progress and attention issues. It is not limited to the selector's currently eligible results, so stranded labels remain visible. Exhausted pagination or read budgets produce an explicit incomplete-evidence result. They do not mean no competing attempt exists.

### 5.2 Advisory claim

Before expensive assessment or editing, an admitted candidate receives an attempt announcement and `status: in-progress`. Admission rereads the issue and attempt comments before launching work. It preserves the chosen issue identity so recovery cannot rerun the original search and silently switch to a different issue.

A detected competing preparing or active attempt blocks shared mutations. Contenders stop or quiesce their own writers, preserve independent output where authorized, and surface the conflict. A deployment never clears the shared in-progress label merely because its own contender stopped. Automatic selection can move on to another candidate once its abandoned attempt is conclusively settled.

There is no `status: claiming` label or per-device status-label family. Extra labels, fixed settling delays, and rereads are not represented as exclusive claim primitives.

### 5.3 Shared retry history

Fresh workflow IDs, device changes, and label removal do not reset the issue's automatic retry allowance. Admission evaluates linked attempt history and the applicable bounded policy. A continuation retains prior failures and no-progress evidence. Internal step retries are not separate issue attempts.

An operator-authorized retry reset is recorded explicitly. Conflicting lineage or policy evidence fails to attention rather than inventing a fresh budget. Exact global retry-count enforcement is not claimed under simultaneous duplicate starts. It is likewise not claimed when attempt evidence was deleted without a tombstone: an Available issue with no observable attempt comments is indistinguishable from one that was never attempted, so admission treats it as no observable history and does not claim the shared retry/cooldown budget was verified. Strict-budget operators must rely on tombstones and explicit reset records, not on the absence of comments.

## 6. Failure handling and recovery

### 6.1 Confirmed terminal failure

Terminal finalization belongs to the durable execution boundary, not an optional last success-only preset step. It covers exhausted retries, failures, cancellations, and blocked outcomes while preserving the original outcome separately from cleanup errors.

The owning deployment quiesces all associated writers, evaluates outstanding mutations, preserves available work under the existing checkpoint/publication policy, and records a terminal handoff. Only then can it release in-progress status to the appropriate next state.

A failed child or internal remediation iteration does not release an issue while the controlling attempt remains active. A required verification failure does not become code review solely because a PR exists. Conversely, a reporting failure does not invalidate verified implementation evidence.

### 6.2 Unresponsive deployment

A missing local workflow record on device B says nothing about device A's execution. Old activity timestamps, a sleeping laptop, network loss, and a terminated workflow are not interchangeable evidence.

Staleness triggers observation and attention, never automatic lease expiry or ownership transfer. Another deployment can add an attention signal while retaining in-progress. A release requires conclusive owner-reported stop/mutation evidence or an authorized operator resolution after stopping or disabling the prior writer.

Reconciliation runs through existing MoonMind/Temporal facilities with bounded work and operational defaults. It adds no permanently running coordination container. It can repair incomplete confirmed handoffs, classify inconsistencies, and expose orphaned attempts without access to another deployment's services.

### 6.3 GitHub unavailability

Read errors, rate limits, incomplete pagination, and ambiguous responses stop new admission and shared mutations. MoonMind does not fall back to a local-only claim or interpret unreadable comments as no owner.

Local durable execution retains pending synchronization evidence for retry. After connectivity returns, it rereads current GitHub state before acting. The failure is visible locally while GitHub is unavailable and is reflected in GitHub once access is restored. Visibility and reconciliation are eventual and require at least one healthy authorized deployment.

## 7. Preserved work and PR continuation

The normal continuation target is the existing trusted PR, not a new implementation from the base branch. Saved code and current GitHub state are reassessed against the issue's requirements. A percentage-complete claim or PR existence is not completion evidence.

| Evidence | Required routing |
| --- | --- |
| One trusted, open, writable implementation PR | Continue from its validated current head and update the same PR |
| Implementation complete, only verification or handoff incomplete | Perform only the missing gate or finalization work |
| Saved work extends beyond the PR head | Compare ancestry and current revisions before incorporating it |
| GitHub-accessible saved branch/commit but no PR | Continue preserved work and create the normal PR only under the admitted publication policy |
| PR already merged | Reassess the intended destination and complete the issue or implement only unmet requirements |
| Closed-unmerged PR, multiple competing PRs, unclear ownership, or incompatible source | Require a decision rather than silently reopen, overwrite, or choose one |
| Work exists only in another device's private storage | Require owner recovery or an authorized portable handoff, not a fresh start that silently discards it |

A continuation records the exact repository, PR, branch, base, and head it adopted. The intended branch identity is preserved before PR creation so a lost create response can be reconciled by exact branch/PR identity. A mention of the issue number alone does not establish a canonical PR.

Local filesystem and private MinIO checkpoints are not cross-device recovery points. GitHub-accessible code and a sufficient sanitized handoff are required for another device to continue independently. Exact failed-step recovery additionally requires all compatible checkpoint inputs. Starting a new attempt from preserved code is described as continuation, not falsely reported as exact resume.

The state machine does not widen publication authority. Explicit None does not permit a recovery push, PR creation, or merge. When the admitted save method is local-only, the issue remains visible for owner recovery rather than promising portable recovery. Existing checkpoint and artifact owners remain authoritative.

A partial PR is not merged just to transfer work between devices. Merge and completion follow the existing publishing and review contracts. An intentionally accepted partial increment requires explicit authorization and must not carry misleading issue-closing semantics. Otherwise the default is to finish the existing PR.

## 8. Mutation safety and consistency limits

### 8.1 Label synchronization

Trusted operations add and remove only labels owned by this lifecycle. They do not replace the entire issue label set or remove unrelated labels. GitHub exposes targeted label operations separately from whole-set replacement [1].

Transitions add the destination status before removing an old blocking status. Moving to Available has no destination label, so a trustworthy stopped/no-preserved-work handoff and retry disposition precede removal. Intermediate label combinations remain ineligible.

Every retry reads current issue and attempt evidence. It abandons obsolete intended transitions when a newer attempt or operator decision is observable. A delayed old finalizer must not knowingly remove another attempt's active state. Reconciliation derives the next action from current evidence, not a stale local command queue.

This is eventual reconciliation, not a transactional compare-and-swap. GitHub's documented label/comment operations do not provide conditional ownership acquisition, and conditional mutation requests are unsupported unless documented for the specific endpoint [1], [2], [3]. Two devices can both announce work. A delayed label mutation can still race a later read. Selectors therefore honor unresolved attempt evidence even when a label is missing.

### 8.2 PR and merge effects

Before shared code or PR effects, the attempt rereads current state and stops on an observed competitor, operator hold, unexpected branch head, or ambiguous publication result. Workspaces remain isolated. Non-fast-forward overwrites and automatic force pushes are not an accepted way to reconcile competing work.

PR assessment, repair, verification, and merge decisions reuse the resolved portable Skills and the existing trusted publication boundary. This state machine does not introduce a second PR resolver or merger. Issue labels do not grant credentials or merge permission.

GitHub's merge API can check an expected PR-head SHA [4]. That checks the target revision, not MoonMind ownership. A read immediately before pushing or merging cannot eliminate every pause-between-check-and-write race. An unknown push or merge outcome therefore blocks automatic release/takeover until resolved. A PR currently appearing unmerged does not prove an earlier request can no longer complete.

No claim of exactly-once execution, perfectly bounded global retries, or fenced publication follows from this design. When strict exclusivity is required for a particular operation, it needs a separately qualified mechanism or operator-controlled serialization rather than being presented as a property of labels.

## 9. Visibility, operator actions, and boundaries

Every terminal unsuccessful attempt leaves a durable next action in its comment, even when the issue returns to Available. Recovery needed identifies preserved progress. Needs attention identifies an unresolved intervention. Neither is silently dismissed by another workflow's launch.

Workflow Detail and issue-centric views show the issue, attempt lineage, originating deployment, current state, preserved PR/revision, remaining requirements, retry eligibility, and synchronization errors. An orphaned remote attempt is reported as unresponsive, not inaccurately described as a locally confirmed failure.

An operator can continue existing work, hold automatic processing, acknowledge an incident without releasing the hold, resolve a competing-attempt conflict, or authorize retry after the prior writer is stopped. Acknowledgment alone never releases ownership or discards preserved progress. Intentional abandonment of work has an explicit disposition and audit trail.

One shared lifecycle policy serves search, explicit implementation, terminal finalization, and reconciliation. GitHub I/O runs at trusted Activity/service boundaries. Local Temporal execution remains authoritative for local runtime facts, while shared admission consumes GitHub-visible evidence. Dashboards are projections, not alternative ownership authorities.

All participating deployments must recognize the canonical labels and attempt format before automatic cross-device continuation is relied upon. An older selector that ignores recovery/review/attention states can violate this contract. Unknown formats fail closed, but a version field cannot constrain an old client that ignores it. Coexistence with nonparticipating writers is not silently described as coordinated.

## 10. Conformance requirements

Tests exercise three isolated deployments with no shared database, Temporal service, or artifact store. They assert the stated best-effort contract rather than inventing an exclusive-lock guarantee.

| Scenario | Required evidence |
| --- | --- |
| Unlabelled open issue with ordinary classification labels | Eligible only after the usual scope, blocker, and prior-work checks |
| No status label but unresolved active attempt comment | No fresh implementation starts on the assumption that work is unowned |
| Recovery-needed issue with partial PR | Another deployment adopts the PR and implements only remaining requirements |
| Code-review issue | Ordinary Search and Implement does not duplicate implementation |
| Internal retry or active remediation | Issue remains owned by the controlling attempt |
| Terminal failure before any edits | Failure history and budget survive the transition to Available |
| Failure after PR creation but before recording its number | Exact recorded branch identity recovers the existing PR |
| Failure after verified implementation or merge | Recovery performs missing finalization, not reimplementation |
| Writer or merge-request outcome is unknown | Blocking state remains and automatic takeover is refused |
| Owner disappears or device sleeps | Another deployment surfaces attention without releasing on age alone |
| All three announce simultaneously | Possible duplicate starts are represented honestly, and observed contenders quiesce without clearing each other's status |
| Old attempt resumes after an observed successor | It makes no new shared mutation and does not replay obsolete cleanup |
| Injected delayed mutation after an ownership check | Test exposes the unfenced race rather than claiming the label model prevents it |
| GitHub accepts a comment/label/PR request but response is lost | Retry reconciles observed results and does not blindly repeat effects |
| Interrupted terminal label transition | Mixed labels block admission, and conclusive handoff evidence permits reconciliation |
| Human label or PR-head change | Unrelated labels/history are preserved and unexpected changes trigger reassessment |
| Multiple competing or closed-unmerged PRs | No silent canonical selection, reopening, merge, or overwrite |
| Private-only checkpoint | Cross-device continuation is not falsely offered as available |
| Retry moves across devices | Prior failures, no-progress evidence, cooldown, and holds are retained |
| Intentional cancellation | No automatic replacement is launched |
| Deleted (detectable, for example lineage gap, label without handoff, or tombstone) or contradictory attempt evidence | Admission requests attention rather than inferring a clean slate. Sole-history deletion on an Available issue without a tombstone is indistinguishable from never-attempted and is an explicit limitation (§§4.2 and 5.3), not a needs-attention trigger |
| GitHub outage, rate limit, or incomplete comment scan | No local-only claim or unsafe interpretation of missing evidence |
| Manual in-progress label with no trusted attempt | Label is respected and not age-cleared |
| Unsupported comment version or noncanonical workflow status | No silent admission or destructive normalization |
| Required labels/issue permissions unavailable | Actionable local failure, with no claim that GitHub was updated |
| Multiple failed steps and finalization errors | Original execution outcome remains distinguishable from synchronization/preservation failures |

The changed production journey is exercised through the actual selector, workflow/Activity boundaries, preserved-work resolver, publisher, and terminal reconciler. Hermetic tests model interleavings and ambiguous responses. Authorized provider verification separately establishes real GitHub behavior. A local mocked success is not evidence that all three deployed devices conform.

## 11. Rationale and excluded alternatives

No explicit todo label is needed because this design has no separate ready-for-automation approval gate. Adding one would create another synchronized value without changing the next action. A future readiness gate is a separate product decision, not a hidden reinterpretation of unlabeled issues.

Recovery needed exists because the next attempt must adopt previous work. Needs attention exists because some failures cannot safely authorize a replacement. These states carry distinct behavior rather than merely describing the previous workflow's result.

Labels alone do not hold deployment identity or recovery evidence, so attempt comments supply the minimum portable handoff. They do not become a separate coordination branch or database. Existing PRs and checkpoint mechanisms hold the code itself.

Automatic takeover on silence, per-device lock labels, forced partial-PR merges, and timestamp-based winner selection are excluded. The simplicity tradeoff is deliberate: useful cross-device recovery and visible uncertainty, without pretending the GitHub label API is an exclusive lock.

## 12. Legacy cutover implementation status

The conservative legacy reconciliation cutover for this state machine is implemented (not merely desired): the decision layer lives in `moonmind/workflows/temporal/github_issue_legacy_cutover.py` under the single surviving policy owner (`github_issue_lifecycle`), and is reachable through the existing `github_issue.assess_legacy` / `github_issue.plan_legacy_repair` activity bindings instead of a new coordination service. Bounded read-only assessment, shared-guard repair planning, retained-history drainage, mixed-deployment qualification, and upgrade/rollback fixtures are covered by `test_github_issue_legacy_cutover_4184.py` and `test_github_issue_legacy_cutover_wiring_4184.py`, including an emission-path proof that new executions never write obsolete shapes. Full cutover behavior is specified in [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md). Post-integration provider and three-device qualification remains separately reported; source implementation alone does not claim deployed compliance.

## References

[1] [GitHub REST API: labels](https://docs.github.com/en/rest/issues/labels).  
[2] [GitHub REST API: issue comments](https://docs.github.com/en/rest/issues/comments).  
[3] [GitHub REST API best practices: conditional requests and retries](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).  
[4] [GitHub REST API: pull requests and merge revision checks](https://docs.github.com/en/rest/pulls/pulls).

# GitHub Issue Status State Machine

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed; legacy cutover has a separate implementation and qualification contract
**Owners:** MoonMind Platform + Workflow Runtime + GitHub Integration  
**Updated:** 2026-09-27\
**Audience:** Workflow, preset, GitHub adapter, recovery, and dashboard contributors and operators  
**Authority:** GitHub issue lifecycle labels, selection eligibility, attempt handoffs, and cross-deployment recovery behavior. Existing execution, checkpoint, publishing, and merge contracts retain their respective authority.  
**Owning Surface:** Trusted GitHub issue operations and workflow terminal/reconciliation boundaries  
**Implementation owners:** [Lifecycle](../../moonmind/workflows/temporal/github_issue_lifecycle.py), [attempt protocol/accounting](../../moonmind/workflows/temporal/github_issue_attempts.py), [selection](../../moonmind/workflows/temporal/github_issue_search.py), [lease guard](../../moonmind/workflows/temporal/github_issue_claim_lease.py), and [claim recovery](../../moonmind/workflows/temporal/github_issue_claim_recovery.py).

**Related Docs:** [Workflow Presets System](WorkflowPresetsSystem.md), [Workflow Publishing](WorkflowPublishing.md), [Checkpoint Branch System](CheckpointBranchSystem.md), [Workflow Remediation](WorkflowRemediation.md), [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md), and [PR Merge Automation](PrMergeAutomation.md).

This document defines desired behavior, not implemented or deployment-tested capability. Implementation tracking and rollout notes belong in issues or temporary execution artifacts, not this design.

## 1. Purpose and design decision

An issue's objective can outlive workflow attempts. Labels describe lifecycle; one comment per attempt records ownership and recovery; ordinary PRs/branches hold portable code. Three independent deployments can coordinate through GitHub without sharing Temporal, databases, artifacts, or sessions.

Coordination is advisory, not an exclusive lock or fence. Participants renew bounded leases or lose shared-write authority. Expiry permits reassessment, not a claim that writers stopped, work vanished, or implementation completed. Stale labels and cleanup obligations cannot create permanent ownership. Ordinary worker disappearance alone does not require attention.

| Concern | Authority |
| --- | --- |
| Lifecycle stage | Requirements and PR evidence |
| Current reservation | Valid unexpired lease |
| Outstanding preservation/cleanup | Attempt cleanup record |
| Intentional automatic-work stop | Explicit attributable hold |

Available work needs no `status: todo`/`status: ready` label. Availability permits assessment, not unrestricted execution or disregard of prior work.

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

Ordinary labels such as bug, feature, priority, or component are independent of this state machine. Existing dependency and blocker rules continue to apply. An issue without a status label can still be blocked by its prerequisites or outside the user's requested scope. The `manual-only` label is such a start blocker: people, not automation, must finish the issue (section 5.4).

Search distinguishes empty backlog from lifecycle, dependency, author-scope, ownership, budget, and cooldown exclusions. `searchEvidence` reports counts and bounded samples (20 candidates, up to 10 attempt/comment identities per ownership conflict), with explicit truncation. Ownership blocks report `unresolved_issue_attempts`; IDs are diagnostics, not writer proof. `selectedIssueAuthor` appears only after reservation succeeds.

Idle summaries rank blocker counts. Budget/cooldown samples precede bare label exclusions and explain allowance, charged outcomes, and unfinished accounting. Author filtering happens first: retry-history exclusion is not `no_eligible_self_authored_issue`. See [selection diagnostics](../../moonmind/workflows/temporal/github_issue_search.py) for the bounded projection.

The existing configured `status: done` label may accompany verified completed closure. It is terminal presentation, not another open-work admission state. A closed issue with a not-planned disposition is not reported as successfully implemented. An open issue carrying `status: done` is inconsistent and is not silently treated as available.

### 2.1 State interpretation

A settled open issue has zero or one canonical status. Other combinations block admission until evidence reconciles them, except intentional `needs-attention` + `in-progress` while stop is unknown. That pair settles as Needs attention; authorized hold resolution clears both labels so the retained in-progress label cannot re-block it.

Unknown status/format requires classification. Superseded labels are not newly emitted or bulk-cleared. A manually applied in-progress label without trusted attempt evidence must be distinguished from confirmed stale managed bookkeeping; it is not age-cleared.

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
| In progress | All valid version-2 leases expired, with complete trusted comments and no hold or other status | Available for assessment | Retain every attempt and prior-work reference; expiry proves loss of claim authority, not stopped processes or absence of work |
| In progress | Stop is uncertain, evidence is missing, recovery is unsafe, or budget is exhausted | Needs attention | Preserve blocking information and explain the required intervention |
| In progress | The deployment could not start a runtime, so no agent ever ran | Available | Retain the attempt in lineage without charging the issue's allowance, and record its back-off |
| In progress or Code review | Objective is verified satisfied on its intended destination | Closed | Apply the existing authorized completion policy, including no-change completion when qualified |
| Code review | Existing owner or explicitly admitted PR repair starts editing | In progress | Continue the same PR under the existing review/repair contract |
| Code review | Review owner ends before the remaining authorized work is complete | Recovery needed or Needs attention | Record whether the next action is PR repair, verification, or review/merge continuation |
| Any open state | Intentional cancellation or operator hold | Needs attention | Do not automatically schedule a replacement or reset the issue to available |
| Needs attention | Authorized resolution establishes a safe next action | Available, Recovery needed, Code review, or Closed | Record the resolution and preserved-work disposition before releasing the hold |
| Closed | Human or authorized policy reopens the issue | Reassess | Do not infer fresh work or completion from old labels |

All other transitions require an explicit decision under the existing authority contracts. Only an explicitly declared claim lease can expire. Workflow timeouts, old progress timestamps, and missing local records do not create that authority. Expiry never proves completion, safe workspace deletion, or an exact resume checkpoint.

Only attempts capable of producing work consume retry allowance. Complete controlling history proving no agent child started permits `runtime_unavailable`: retained lineage, no charge or attention escalation, portable cooldown from termination rather than later sweep time. The [recovery owner](../../moonmind/workflows/temporal/github_issue_claim_recovery.py) recognizes bounded pre-launch admission/capacity histories, including legacy lease expiry; failed admission or another underlying cause is not proof. Required provider-slot cleanup must finish first.

A version-2 `in_progress` attempt whose lease lapses also remains in lineage without a charge. Before dispatch (`preparing`) it carries the single unavailable-runtime window; dispatched lapses double backoff, capped at one day. Preserved work still routes to continuation. Version-1 unfinished records and recorded terminal outcomes remain charged; diagnostics identify unfinished accounting separately from proven failure. Only an audited reset changes that accounting (section 5.3).

A workflow whose admitted finish target is a completed PR handoff may end successfully in Code review without an active implementation owner. A PR-and-merge parent can remain awaiting review under its existing contract. Lack of new code during that wait is not by itself a stalled attempt.

A failure after successful implementation or merge does not cause reimplementation. The remaining task can be status synchronization or final reporting. The source workflow remains failed when appropriate, while the issue reflects independently verified completion evidence.

## 4. Attempt comments and evidence

### 4.1 One comment per attempt

Each attempt has a globally unique attempt ID, a stable installation-specific deployment ID, and its exact workflow/run identity. A GitHub account name cannot substitute for deployment identity because all devices may use the same account.

The trusted integration creates one human-readable, machine-identifiable issue comment for that attempt. It updates its own comment during execution and leaves it as a terminal handoff afterward. Separate attempts do not overwrite one shared summary comment or rewrite each other's history.

Sparse updates preserve identity, lineage, candidate, requirements, and retry evidence unless replaced explicitly; empty requirements and zero allowance remain meaningful. Persist the canonical handoff and original completion route in the finalization plan. Lost acknowledgments reuse confirmed labels and committed candidate data, never later caller inputs or invented merge authority. Readable and machine handoffs agree on saved references, remaining work, and next action. Bounded additions are deduplicated without evicting predecessor lineage; omissions are explicit.

The comment carries only the information required to interpret and continue the work:

| Information | Contract |
| --- | --- |
| Attempt identity | Attempt ID, deployment ID, workflow/run identity, and format version |
| Lineage | Predecessor attempt/comment when continuing previous work |
| Activity | Preparing, active, awaiting review, releasing, released, or attention, with last reported activity |
| Claim lease | Version 2 declares `leaseRenewedAt` and `leaseExpiresAt` as timezone-aware UTC timestamps, at most 30 minutes apart; version 1 has no lease |
| Reservation status | Derived, never stored: live, expired, released, operator hold, legacy awaiting migration, legacy retired, or unreadable |
| Stop evidence | Whether all associated writers have stopped and publication requests have a known outcome |
| Preserved work | PR identity, exact verified head/base, and any GitHub-accessible saved branch/commit |
| Result | Failure or completion category, met/unmet requirements, and a concise verification summary |
| Next action | Fresh retry, continue implementation, verify, continue review, finalize status, or obtain operator attention |
| Retry eligibility | Applicable retry history, remaining allowance, cooldown, and any operator hold |

This is an attempt handoff, not another workflow database. Large logs, prompts, secrets, provider credentials, and runtime session material do not belong in comments. Private dashboard URLs and local artifact references are optional diagnostics, never the sole cross-device recovery input.

A marker and body do not authenticate themselves. The integration validates comment provenance, issue identity, schema, and relevant GitHub objects. Issue text and arbitrary comments remain untrusted input. Shared credentials do not establish an adversarial security boundary between deployments.

The account resolved from the Activity's posting credential is trusted for its own markers. `MOONMIND_TRUSTED_POSTERS` adds other accounts; unresolved identity remains untrusted.

### 4.2 Write and release behavior

Progress updates are coalesced and rate-bounded rather than emitted on every runtime poll. Ordinary activity timestamps remain observations. New claims use version 2 with an explicit lease whose length matches the phase it covers:

| Reservation phase | Deadline | Renewal |
| --- | --- | --- |
| Preparing (announced, execution not started) | 5 minutes | at one sixth of the running cadence |
| Running (active or awaiting review) | 30 minutes | every 5 minutes |
| Waiting for provider or host capacity | 30 minutes | renewed while the existing durable queue waits, within its original deadline |
| Execution ends | renewal stops | the reservation is relinquished promptly |

The preparing deadline is one sixth of the running lease, bounding failures between selection and dispatch. Selection probes the selected/default profile (otherwise enabled profiles) before claiming. Observable saturation/cooldown returns `local_capacity_unavailable`; unobservable capacity is not saturation. Omnigent uses its Profile's owning harness manager, not a separate capacity manager.

New UserWorkflow histories keep this search unclaimed in `awaiting_slot`, rechecking every 30 seconds for 30 minutes with the original owner/profile/step/inputs and deterministic keys. A final bounded probe may finish after the deadline. Persistent saturation fails with `RESOURCE_EXHAUSTED`, retains capacity evidence, and skips dependents even under CONTINUE; it is not idle success or verified no-commit. Empty backlog remains idle. `run-issue-search-capacity-retry-v1` preserves retained histories' immediate-idle behavior. Existing schedule overlap applies; same-owner persisted receipts are recovered before selecting another issue.

After claiming, first launches and later steps retain the same reservation while waiting for capacity. The provider manager owns queueing; the claim guard owns authority, not another capacity timeout. Inconclusive manager reads preserve the queued request and retry observation within the original queue deadline; only confirmed absence permits reassertion. New AgentRun histories use this policy; retained histories replay the earlier capacity backoff, whose terminal failures remain recoverable without charging issue retry allowance when no agent launched.

The trusted `issueClaimLease` brief binds owner, attempt, repository, issue, and comment. AgentRun verifies its durable receipt and GitHub before launch and owns renewal for managed/external runtimes. Parent integrations use the same guard; PR merge-child waits and post-merge completion retain parent renewal. The parent awaits child cancellation on lease loss.

Participants use synchronized UTC clocks and start cancellation one minute before last confirmed expiry. Transient renewal failures retry every 30 seconds, before launch within one renewal interval and during execution within that stop margin. Each observation is deadline-bounded; identity mismatch, loss, or invalid deadline stops immediately through existing cancel/preserve/cleanup owners. Merge-wait renewal has a 30-second total timeout. Historical requests without leases and replay-versioned guards retain recorded behavior.

Persist exact PATCH intent and reconcile uncertain responses before extending authority. An expired lease cannot revive: further work needs fresh admission/identity. Completion cancels the auxiliary renewer; wrapped Activity cancellation remains cancellation. Lease expiry never substitutes for terminal stop/preservation proof.

### 4.3 Relinquishing a reservation

Shared writes require current uncontested ownership. Retiring one's own reservation requires authentic ownership of that record only, even with competitors. Withdrawal and its readback cannot be blocked by the contention they resolve; they cannot clear a successor's labels or invent stopped-writer evidence.

Once stopped, settled, and preserved, an attempt's ownership ends even if label/readback/comment bookkeeping fails. Record `ownershipEnded` separately from fully confirmed `released`; pending bookkeeping stays retryable. Expiry backs up withdrawal during outages.

Serialize comment writes per attempt locally. Before announcement validate live evidence under the PostgreSQL connection-scoped claim lock, held across durable POST intent, remote write, and receipt readback. Rejected reads can abandon a fresh reservation, never clear an existing uncertain intent. Retried creation reconciles the attempt marker; duplicate identical comments are one attempt, conflicting copies require reconciliation. Local serialization cannot fence other deployments.

A terminal proposal precedes label release. Fully `released` requires stopped writers, verified or explicitly absent preservation, settled shared mutations, and observed labels. Another deployment may finish interrupted bookkeeping only from conclusive terminal evidence. Released/expired/superseded/held/conflicting attempts perform no new shared writes; resumed processes reread before effects.

Keep attempt history: update each attempt's own comment, never delete it. Unavoidable redaction first posts a visible tombstone preserving attempt/deployment/version, retry disposition, and successor lineage. Missing predecessors or labels without handoffs expose deletion; sole-history deletion on an Available issue is indistinguishable from never-attempted work. No strict retry guarantee follows from absent comments.

## 5. GitHub Issue Search and Implement

The preset has one admission policy for both fresh and recovery work. Explicit issue workflows, scheduled searches, manual submissions, and retries do not gain a bypass around that policy.

Git URL branches fail at submission with instructions to enter a branch name or clear the field for the remote default. Repository connection and branch choices remain authoritative.

Search results and cached local records are candidate discovery aids. Direct issue, comment, and PR reads control admission. The absence of an in-progress label alone is insufficient.

When a fresh GitHub read proves that a cached attempt's reservation has ended, ordinary admission ends that local ownership record while retaining its pending bookkeeping and history. The evidence must match the attempt, repository, issue, original poster, and recorded comment identity; a missing comment or changed identity does not authorize clearing the cache.

### 5.1 Candidate eligibility

An eligible issue is open, in authorized repository/query/author scope, unblocked by prerequisites, Available or Recovery needed, and free of live/legacy reservations, holds, unreadable evidence, unknown prior work, or exhausted restrictions. Expiry permits assessment only: inspect retained attempts and PRs before editing.

Search, explicit loading, continuation, publication checks, and maintenance use one ownership interpreter. Reassess advisory in-progress labels through fresh reads rather than skipping all of them. A confirmed managed label with no supporting attempt can be reconciled as leftover bookkeeping; manual labels are not age-cleared. Review/recovery/attention statuses are never age-cleared.

Report live deadline, expired/legacy reservation, hold, prerequisite blocker, continuation-required PR, and unavailable evidence distinctly. Recovery stays within requested scope and receives bounded preference. Code review enters PR follow-up only when requested. Reconciliation leaves external review work and existing operator holds unchanged when no interrupted MoonMind transition is known; missing MoonMind metadata does not establish an unresponsive owner. Attention escalation rechecks issue and comment evidence before writing, deferring changed or unreadable observations. Incomplete pagination/read budgets never mean no competitor.

### 5.2 Advisory claim

Persist the durable claim receipt, announce one attempt comment, then apply in-progress before expensive assessment/editing. Re-read issue/comments before launch, retaining the exact selected issue through recovery. A later start resolves the receipt from its execution owner and authenticates the live comment; a bare attempt ID, trusted-looking brief, or label is insufficient.

Live active contenders and legacy reservations block shared effects. Simultaneously preparing contenders use GitHub comment-ID order: the earliest confirmed announcement proceeds; later preparing attempts quiesce. Active contenders block regardless of order. Losers stop their writers and preserve independent output where authorized, without clearing another owner's status. Only conclusively settled abandonment permits moving to another candidate. Extra labels or settling delays do not create exclusive locks.

### 5.3 Shared retry history

New workflow IDs, deployments, and label removal do not reset allowance, cooldown, or no-progress history. Internal step retries are not new issue attempts. Lapsed version-2 records retain work without a charge; version-1 unfinished accounting remains visible.

A reset is a released `retry_reset` attempt naming authorizer, time, reason, and superseded attempts. Only the authenticated posting account may authorize it; configured trusted posters alone cannot reset allowance (`unauthorizedResets`). It applies only to named attempts recorded before the reset in GitHub order, never intervening later attempts, holds, or ownership. Conflicting lineage/policy fails to attention. Concurrent starts and undetectable deletion preclude exact global-count guarantees.

[Reset tool](../../tools/reset_issue_retry_allowance.py) is read-only by default. It plans exhausted allowances containing unfinished version-1 accounting; recorded outcomes require `--include-recorded-outcomes`. `--apply --reason` rechecks fresh evidence before posting and confirming, refusing live reservations, holds, or unreadable/conflicting evidence. If verified exhaustion alone caused the latest stopped attempt's attention, it first resolves `needs_attention -> available` through the lifecycle guard. Failed resolution posts no reset; unrelated attention returns `attention_not_from_retry_budget`. It never rewrites/deletes history or unrelated labels. Exit codes: 0 all requested resets confirmed, 1 apply recorded none, 2 recording/confirmation failed.

### 5.4 Manual-only issues

When no unmet requirement is work the Linux runtime can advance (for example physical hardware/presence or another OS), initial assessment records `manualOnly.reason` and nonempty `manualActions`. The trusted blocker step confirms one explanatory comment, then adds `manual-only` and ends idle without implementation/publication/failure. Uncertain writes reconcile; retries reuse the comment and ordinary released-claim handoff. An incomplete declaration is ignored.

Remaining container work or missing credentials/permission/approval alone is not manual-only. Search skips the label; explicit workflows report it at the blocker step. Only a person removes it after manual work or to retry automation.

`BLOCKED` without valid manual-only evidence fails before In Progress/implementation, retaining bounded redacted assessment summary/ref (or explicit unavailable summary). Required unreadable/malformed assessments fail with repair guidance. Durable verdicts override stale compact outputs; the defensive In Progress gate agrees. Ordinary failed-attempt finalization owns release/status, not the blocker tool.

## 6. Failure handling and recovery

### 6.1 Confirmed terminal failure

Terminal finalization belongs to the durable execution boundary, not an optional last success-only preset step. It covers exhausted retries, failures, cancellations, and blocked outcomes while preserving the original outcome separately from cleanup errors.

The owning deployment quiesces all associated writers, evaluates outstanding mutations, preserves available work under the existing checkpoint/publication policy, and records a terminal handoff. Only then can it release in-progress status to the appropriate next state.

A failed child or internal remediation iteration does not release an issue while the controlling attempt remains active. A required verification failure does not become code review solely because a PR exists. Conversely, a reporting failure does not invalidate verified implementation evidence.

The default five-minute `MoonMind.GitHubIssueReconcile` schedule is registered by API startup; its independent observer retries registration every 30 seconds. [Claim recovery](../../moonmind/workflows/temporal/github_issue_claim_recovery.py) owns bounded local sweeps; [lifecycle](../../moonmind/workflows/temporal/github_issue_lifecycle.py) owns shared decisions. Persisted repository/issue/claim cursors prevent starvation. Unchanged settled/no-effect listings may skip reads, but pending or mutable cases are reread.

Local recovery requires complete bounded controlling/descendant histories, all closed for at least five minutes. Completed owners with unfinished in-progress comments qualify; owners already in a terminal review journey retain it. Missing history reports `execution_history_unavailable`, never inferred death. Started agents require matching digest-validated runtime bindings and completed fenced cleanup. The workspace owner proves clean tracked/untracked content against the checkpoint digest/revision; GitHub must verify that revision remains reachable on the recorded branch. Conditional ETag reads may reuse prior observations. This grants no write/PR/completion authority.

Use the existing finalizer: terminal proposal, targeted label readback, then released comment. Missing labels do not bypass stop/no-work checks. Lost acknowledgments resume the saved plan. Holds, active descendants, uncertain mutations, unique commits, modified workspaces, missing preservation proof, and foreign claims remain distinct recovery dispositions. Unavailable reads defer without another agent run; expiry/accounting still follows section 3.

### 6.2 Unresponsive deployment

A missing local workflow record on device B says nothing about device A's execution. Old activity timestamps, a sleeping laptop, network loss, and a terminated workflow are not interchangeable evidence.

For version-2 claims, another deployment uses complete authenticated GitHub issue/comment reads to check the declared deadline, scope, provenance, conflicting copies, other owners, and holds. If all leases have expired and no other status blocks admission, it rereads before removing only `status: in-progress`, then verifies the result. An observed successor prevents reclamation and retains its advisory label. Expiry does not rewrite the old owner's comment as `released`, claim that its process stopped, or discard its workspace. Existing PR/branch references and retry history remain authoritative inputs for the next assessment. `status: code-review`, `status: recovery-needed`, `status: needs-attention`, and explicit comment holds are never age-cleared.

Version-1 writers never agreed to expiry. `MOONMIND_ISSUE_CLAIM_LEGACY_CUTOVER_AT` is the operator's declaration that they are stopped/upgraded; until set, report `legacy_reservation_awaiting_migration`. Only comments GitHub timestamps before that instant lose authority. Stop/upgrade old writers first because they cannot honor new supersession rules.

Run [legacy-claim recovery](../../tools/recover_legacy_issue_claims.py) from a checkout with `tools/` (the command is absent from production images):

```sh
python tools/recover_legacy_issue_claims.py --repository owner/name --cutover-at <ISO-8601-instant> --report /tmp/claim-recovery.json
```

Review its inventory before `--apply`. It rechecks scope/provenance, copies, holds, and intervening claims before each write; refuses live claims/holds; never deletes comments/labels or rewrites untrusted/successor records. Retirement stamps an expired lease at cutover and recovery disposition while preserving all lineage, retry/work refs, and the fact writers were not confirmed stopped. Saved work uses `preserve_for_continuation`. Exact body readback reconciles lost acknowledgments; incomplete reads/unapplied writes report nonzero. Ordinary selection still evaluates all other gates.

See [Legacy Cutover](GitHubIssueLegacyCutover.md) for reconciliation and mixed-deployment compatibility. No foreign database or new coordination container is required.

### 6.3 GitHub unavailability

Read errors, rate limits, incomplete pagination, and ambiguous responses stop new admission and shared mutations. MoonMind does not fall back to a local-only claim or interpret unreadable comments as no owner.

Local durable execution retains pending synchronization evidence for retry. Lease renewal retries remain bounded by the last confirmed expiry; after that deadline the old attempt must stop and cannot resume shared writes when connectivity returns. It rereads current GitHub state and uses a new admission for further work. The failure is visible locally while GitHub is unavailable and is reflected in GitHub once access is restored. Visibility and reconciliation are eventual and require at least one healthy authorized deployment.

## 7. Preserved work and PR continuation

Prefer the trusted existing PR, reassessing its current head against requirements. A completion percentage or PR existence is not evidence of completion.

| Evidence | Required routing |
| --- | --- |
| One trusted, open, writable implementation PR | Continue from its validated current head and update the same PR |
| Implementation complete, only verification or handoff incomplete | Perform only the missing gate or finalization work |
| Saved work extends beyond the PR head | Compare ancestry and current revisions before incorporating it |
| GitHub-accessible saved branch/commit but no PR | Continue preserved work and create the normal PR only under the admitted publication policy |
| PR already merged | Reassess the intended destination and complete the issue or implement only unmet requirements |
| Closed-unmerged PR, multiple competing PRs, unclear ownership, or incompatible source | Require a decision rather than silently reopen, overwrite, or choose one |
| Work exists only in another device's private storage | Require owner recovery or an authorized portable handoff, not a fresh start that silently discards it |

Record the adopted repository, PR, branch, base, and head. Persist branch identity before PR creation to reconcile lost responses; an issue-number mention alone does not identify the canonical PR.

Private filesystem/MinIO checkpoints are not cross-device recovery. Another deployment needs GitHub-accessible code and a sanitized portable handoff; retry from a step additionally needs the run's retained Temporal history and its live issue claim. Continuing saved code is not necessarily exact resume.

Publication authority is unchanged: None permits no recovery push/PR/merge, and local-only work requires owner recovery. Do not merge a partial PR merely to transfer it. Explicitly accepted partial increments need authorization and no misleading closing semantics.

## 8. Mutation safety and consistency limits

### 8.1 Label synchronization

Mutate only lifecycle-owned labels using targeted operations [1]. Add destination status before removing old blockers; Available has no destination label, so first record trustworthy stopped/no-work evidence and retry disposition. Intermediate combinations block admission.

Every retry rereads issue/attempt evidence and abandons obsolete transitions after a successor/operator decision. This is eventual reconciliation, not compare-and-swap: GitHub label/comment APIs offer no documented conditional ownership acquisition [1–3]. Missing labels cannot override unresolved attempts; delayed mutations can still race reads.

### 8.2 PR and merge effects

Reread before shared effects and stop on competitors, holds, unexpected heads, or ambiguous results. Isolate workspaces; never reconcile by force push/non-fast-forward overwrite. Reuse portable PR Skills and trusted publishing/merge owners; labels grant no credentials or permission.

GitHub's expected-head merge check protects revision, not MoonMind ownership [4]. Reconcile uncertain push/merge before repeating or replacing it; lease expiry or a currently unmerged PR cannot prove a pending request will never complete. Strict exclusivity needs separately qualified coordination or operator serialization; this design promises neither fencing nor exactly-once execution.

## 9. Visibility, operator actions, and boundaries

Every unsuccessful terminal attempt leaves its next action, even after returning to Available. Detail projections show lineage/deployment, state, saved PR/revision, requirements, retry eligibility, and sync errors. An orphaned remote attempt is unresponsive, not locally confirmed failed. Deployment identity survives recreation on `moonmind_secrets`.

Operators can continue, hold, acknowledge, resolve contention, authorize retry, or explicitly abandon work. Acknowledgment alone never releases ownership or discards progress. Trusted Activities perform GitHub I/O; local Temporal owns local runtime facts, GitHub-visible evidence controls shared admission, and dashboards remain projections.

All deployments must recognize canonical labels/formats before relying on cross-device continuation. Unknown formats fail closed; nonparticipating old clients cannot be fenced by a version field.

## 10. Verification boundaries

Qualify three isolated deployments without shared storage through actual selector, workflow/Activity, preserved-work, publisher, and terminal-reconciliation paths. [Lifecycle unit coverage](../../tests/unit/workflows/temporal/test_github_issue_lifecycle.py), [lease coverage](../../tests/unit/workflows/temporal/test_github_issue_claim_lease.py), and [retry accounting](../../tests/unit/workflows/temporal/test_github_issue_retry_accounting.py) support focused verification; hermetic success alone is not deployed qualification.

Exercise simultaneous announcements, stale writers, missing labels, unknown/deleted evidence, unavailable capacity before versus after work, uncertain comment/label/push/merge responses, interrupted release, expiry, explicit holds/cancel, and preserved-PR continuation. Test the unfenced delayed-write race honestly. Required permissions unavailable must produce an actionable local error, not a claimed GitHub update. Preserve original failure separately from synchronization/preservation errors.

## 11. Rationale and excluded alternatives

Recovery needed routes to saved work; Needs attention holds unresolved decisions/safety/budget problems. Comments supply identity and portable handoff that labels cannot hold; PR/checkpoint owners retain code. No separate todo approval gate, coordination branch/database, per-device lock label, silence-based expiry, forced partial merge, or timestamp-based winner is implied. Explicit leases bound abandoned claims while keeping ownership, cleanup, preservation, and completion distinct.

## 12. Legacy compatibility

[GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md) owns the implemented assessment/repair path, retained-history drainage, and qualification limits. Source implementation is not proof of provider or three-device deployment compliance.

## References

[1] [GitHub REST API: labels](https://docs.github.com/en/rest/issues/labels).  
[2] [GitHub REST API: issue comments](https://docs.github.com/en/rest/issues/comments).  
[3] [GitHub REST API best practices: conditional requests and retries](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).  
[4] [GitHub REST API: pull requests and merge revision checks](https://docs.github.com/en/rest/pulls/pulls).

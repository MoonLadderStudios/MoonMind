# GitHub Issue Status State Machine

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed (legacy cutover §12 implemented; remainder desired behavior)  
**Owners:** MoonMind Platform + Workflow Runtime + GitHub Integration  
**Updated:** 2026-09-14  
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

**Coordination is explicitly best-effort.** Labels and comments support discoverable ownership announcements, failure handoffs, conflict detection, and finite claim leases. They do not provide an exclusive distributed lock or fence an obsolete writer. Simultaneous duplicate starts remain possible. A participating owner must renew its GitHub-visible lease or lose permission to perform shared writes; another consumer can reassess the issue after expiry using GitHub alone. Strict single-writer enforcement would require an additional coordination capability and is not an implicit promise of this state machine.

**Reservations are expiring and advisory.**

> An issue reservation suppresses duplicate work only while its owner maintains a valid, bounded lease. Historical comments, stale labels, and incomplete cleanup never independently create permanent ownership. Losing ownership does not authorize discarding work or overwriting another contributor's changes.

The objective is therefore not "never risk a duplicate attempt". It is: avoid duplicates where practical, preserve work, and guarantee that an abandoned reservation stops blocking progress. Four concerns stay separate, and each has exactly one authority:

| Concern | What determines it |
| --- | --- |
| What stage is the issue in? | Issue lifecycle, requirements, and PR evidence |
| Is another MoonMind attempt currently reserving it? | A valid, unexpired reservation |
| Does an old attempt still owe cleanup or preservation? | Its outstanding cleanup record |
| Has someone intentionally stopped automatic work? | An explicit, attributable hold |

A stale `status: in-progress` label prompts reassessment; it never rejects an issue on its own. An expired reservation never erases a saved branch, asserts that a process stopped, or declares implementation complete: it means only that the old attempt no longer holds a current reservation. Ordinary worker disappearance is not routed to `status: needs-attention`; attention is reserved for a concrete unresolved decision or safety problem.

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

Search results distinguish an empty candidate set from candidates excluded by lifecycle,
dependencies, author scope, or unresolved attempt ownership. `searchEvidence` retains
aggregate rejection counts and at most 20 candidate examples, prioritizing ownership
conflicts. Each conflict includes up to 10 recorded attempt/comment identities or its
local claim owner; truncation is explicit. These identities are diagnostic references,
not proof of current writer status. A scan blocked by attempt ownership reports
`unresolved_issue_attempts` and explains the stop/preservation evidence needed before
retrying. Removing a status label alone never releases an attempt. `selectedIssueAuthor`
is present only after the candidate reservation succeeds.

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
| In progress | All valid version-2 leases expired, with complete trusted comments and no hold or other status | Available for assessment | Retain every attempt and prior-work reference; expiry proves loss of claim authority, not stopped processes or absence of work |
| In progress | Stop is uncertain, evidence is missing, recovery is unsafe, or budget is exhausted | Needs attention | Preserve blocking information and explain the required intervention |
| In progress or Code review | Objective is verified satisfied on its intended destination | Closed | Apply the existing authorized completion policy, including no-change completion when qualified |
| Code review | Existing owner or explicitly admitted PR repair starts editing | In progress | Continue the same PR under the existing review/repair contract |
| Code review | Review owner ends before the remaining authorized work is complete | Recovery needed or Needs attention | Record whether the next action is PR repair, verification, or review/merge continuation |
| Any open state | Intentional cancellation or operator hold | Needs attention | Do not automatically schedule a replacement or reset the issue to available |
| Needs attention | Authorized resolution establishes a safe next action | Available, Recovery needed, Code review, or Closed | Record the resolution and preserved-work disposition before releasing the hold |
| Closed | Human or authorized policy reopens the issue | Reassess | Do not infer fresh work or completion from old labels |

All other transitions require an explicit decision under the existing authority contracts. Only an explicitly declared claim lease can expire. Workflow timeouts, old progress timestamps, and missing local records do not create that authority. Expiry never proves completion, safe workspace deletion, or an exact resume checkpoint.

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
| Claim lease | Version 2 declares `leaseRenewedAt` and `leaseExpiresAt` as timezone-aware UTC timestamps, at most 30 minutes apart; version 1 has no lease |
| Reservation status | Derived, never stored: live, expired, released, operator hold, legacy awaiting migration, legacy retired, or unreadable |
| Stop evidence | Whether all associated writers have stopped and publication requests have a known outcome |
| Preserved work | PR identity, exact verified head/base, and any GitHub-accessible saved branch/commit |
| Result | Failure or completion category, met/unmet requirements, and a concise verification summary |
| Next action | Fresh retry, continue implementation, verify, continue review, finalize status, or obtain operator attention |
| Retry eligibility | Applicable retry history, remaining allowance, cooldown, and any operator hold |

This is an attempt handoff, not another workflow database. Large logs, prompts, secrets, provider credentials, and runtime session material do not belong in comments. Private dashboard URLs and local artifact references are optional diagnostics, never the sole cross-device recovery input.

A marker and body do not authenticate themselves. The integration validates comment provenance, issue identity, schema, and relevant GitHub objects. Issue text and arbitrary comments remain untrusted input. Shared credentials do not establish an adversarial security boundary between deployments.

### 4.2 Write and release behavior

Progress updates are coalesced and rate-bounded rather than emitted on every runtime poll. Ordinary activity timestamps remain observations. New claims use version 2 with an explicit lease whose length matches the phase it covers:

| Reservation phase | Deadline | Renewal |
| --- | --- | --- |
| Preparing (announced, execution not started) | 5 minutes | at one sixth of the running cadence |
| Running (active or awaiting review) | 30 minutes | every 5 minutes |
| Waiting for unavailable local capacity | not renewed | the reservation lapses and the issue returns to assessment |
| Execution ends | renewal stops | the reservation is relinquished promptly |

These are policy choices, not measured optima. The announcement deadline is one sixth of the running lease, so shortening one shortens both and the two phases cannot drift apart. The short announcement deadline exists because the gap between selection and dispatch is where a deployment most often dies while holding an issue; five minutes bounds that cost. A deployment queued behind unavailable capacity does not renew: holding the backlog behind a queue it cannot drain is worse than releasing the issue and backing off. The renewal is maintained in the same attempt comment. Participants maintain synchronized UTC clocks; the owner begins cancellation one minute before its last confirmed expiry to allow for bounded request latency and clock skew. This margin is cooperative protection, not a fence against delayed or nonparticipating writers.

The trusted brief carries the exact owner, attempt, repository, issue, and comment identity into `AgentExecutionRequest.parameters.issueClaimLease`. The canonical `MoonMind.AgentRun` entrypoint confirms this identity against its own durable receipt and GitHub before launch, then owns renewal across managed and external runtimes. Renewal failure retries only inside the last confirmed lease. Loss of authority invokes the runtime's existing cancellation, preservation, and cleanup owner. Parent integration execution uses the same guard. Requests recorded without a lease retain their historical execution path.

A renewal persists its exact PATCH intent, rereads GitHub after an uncertain response, and extends authority only after confirmation. An expired attempt cannot revive its old lease, even if no successor is visible; it requires a new admission and attempt identity. Expired comments remain intact for prior-work assessment and retry reconstruction. A lease is never a substitute for stop/preservation evidence in a terminal `released` handoff.

Agent completion cancels the auxiliary renewal loop. Wrapped Activity cancellation remains cancellation rather than a renewal retry, so an in-flight renewal cannot trap an already-completed primary result or keep extending its claim.

### 4.3 Relinquishing a reservation

Two decisions are separate and must not be entangled:

* **Permission to continue shared work** requires current ownership and no winning competing claim.
* **Permission to retire your own reservation** requires authentic ownership of that attempt record only. It does not require the absence of competitors.

An attempt can therefore record its withdrawal even when another contributor has already claimed the issue: the operation that resolves contention is never blocked by contention. A withdrawing attempt still may not clear the successor's labels, overwrite their PR, or report stopped writers it did not observe. Reading back its own pending comment update is likewise never gated on contenders. When GitHub is unavailable, immediate withdrawal can fail; expiry is the backstop, so a cleanup failure cannot make a reservation permanent.

Ownership and bookkeeping are also separate facts. Once an attempt is proven stopped, settled, and preserved, its reservation ends even if the terminal label change is denied, the read-back is unknown, or the released comment is unconfirmed. Finalization reports `released` (bookkeeping complete) and `ownershipEnded` (this attempt no longer reserves the issue) independently; the pending bookkeeping stays recorded and retryable. A delayed finalizer that observes a successor retires its own attempt instead of remaining an unresolved contender forever.

Comment creation and updates are serialized per attempt within the owning deployment. A retried create first checks for the same attempt marker. A lost HTTP response is an unknown result, not proof that the operation failed. Duplicate comments with the same attempt identity are one logical attempt, not extra retry allowance. Conflicting copies require reconciliation, not choosing whichever timestamp is newest.

Before announcement, the owning deployment validates live comment evidence
under its claim lock. PostgreSQL connection-scoped serialization remains held
across the durable POST-intent commit, remote write, and receipt readback.
A rejected read before intent leaves a fresh reservation abandonable; an
existing intent is never cleared by this path because a prior write may be
unknown. Selection can skip a conflicting remote candidate before reservation.
This local serialization does not fence a writer in another deployment.

A terminal handoff records a proposed disposition before removing blocking labels. It becomes released only after the writer is stopped, preservation is verified or explicitly absent, pending shared mutations are settled, and the intended label transition has been observed.

Attempt comments are append-only. Deployments and operators do not delete attempt comments. When redaction is unavoidable, the redacting party first posts a GitHub-visible tombstone retaining the attempt ID, deployment ID, format version, retry/no-progress disposition, and successor lineage pointer, so admission can still detect prior history and request attention rather than infer a clean slate. Deletion without such a tombstone is detectable only through a lineage gap (a successor referencing a missing predecessor), a status label without its required handoff, or another observable marker; deletion of the sole history on an issue that has already returned to Available leaves no such marker and is indistinguishable from an issue that was never attempted (see §§5.3 and 10).

An interrupted release is recoverable from the comment and GitHub evidence. Another deployment may finish that bookkeeping only when the recorded terminal stop and mutation outcomes are conclusive. Otherwise it requests attention.

A released or expired attempt performs no further issue-state or PR writes. A resumed old process rereads GitHub before effects and stops when its attempt is released, expired, superseded, held, or in conflict. This rejects observable stale work, but it is not a fence against every delayed external request.

## 5. GitHub Issue Search and Implement

The preset has one admission policy for both fresh and recovery work. Explicit issue workflows, scheduled searches, manual submissions, and retries do not gain a bypass around that policy.

Search results and cached local records are candidate discovery aids. Direct issue, comment, and PR reads control admission. The absence of an in-progress label alone is insufficient.

When a fresh GitHub read proves that a cached attempt's reservation has ended, ordinary admission ends that local ownership record while retaining its pending bookkeeping and history. The evidence must match the attempt, repository, issue, original poster, and recorded comment identity; a missing comment or changed identity does not authorize clearing the cache.

### 5.1 Candidate eligibility

An issue is eligible only when it is open, within the authorized repository/query scope, free of applicable start-blocking prerequisites, in Available or Recovery needed state, and not contradicted by a live reservation, a legacy reservation awaiting migration, an operator hold, unreadable attempt evidence, unknown prior work, or exhausted retry restrictions. Search and explicit issue loading reconcile advisory in-progress labels using fresh GitHub reads before applying this policy. Lease expiry permits assessment; the successor must inspect the retained attempt history and linked PRs before editing.

Selection does not begin by skipping every in-progress label. An advisory label is reassessed against current attempt evidence, and the protocol always announces its attempt comment before applying the label, so a label with no attempt evidence behind it is left-over bookkeeping rather than ownership. Other settled statuses (`status: code-review`, `status: recovery-needed`, `status: needs-attention`) still decide eligibility on their own and are never age-cleared.

Search, explicit issue loading, continuation, publication checks, and maintenance all resolve effective ownership through one interpreter, so one path cannot reclaim an issue while another keeps treating its historical comment as a lock. Maintenance sweeps remain useful, but ordinary selection recovers an expired reservation without waiting for one.

Exclusions are reported as actionable distinctions rather than one undifferentiated conflict — live reservation (with its deadline), expired reservation eligible for reassessment, legacy reservation awaiting migration, explicit operator hold, blocked from starting by another issue, existing PR requires continuation, and GitHub evidence temporarily unavailable. These are diagnostics in `searchEvidence`, not new GitHub labels.

Recovery candidates remain within the user's requested search and priority scope. Eligible continuations receive bounded preference without allowing one repeatedly failing issue to monopolize every run. Code-review issues are routed to the existing PR follow-up journey when explicitly requested, not rediscovered as untouched implementation work.

A bounded reconciliation scan also examines managed in-progress and attention issues. It is not limited to the selector's currently eligible results, so stranded labels remain visible. Exhausted pagination or read budgets produce an explicit incomplete-evidence result. They do not mean no competing attempt exists.

### 5.2 Advisory claim

Before expensive assessment or editing, an admitted candidate receives an attempt announcement and `status: in-progress`. Admission rereads the issue and attempt comments before launching work. It preserves the chosen issue identity so recovery cannot rerun the original search and silently switch to a different issue.

The admission Activity persists a claim receipt before publishing its attempt comment and applying the in-progress label. A later start step resolves that receipt from its durable execution owner and verifies the authenticated live comment before treating the existing in-progress label as already applied. The trusted brief carries the admitted issue and attempt identity through intermediate steps; those fields alone do not grant ownership. A bare attempt ID or a historical label-only claim is insufficient ownership evidence; missing, conflicting, stopped, or unreadable attempt evidence blocks continuation.

A detected competing preparing or active attempt with an unexpired lease, or a legacy attempt without a lease, blocks shared mutations. Contenders stop or quiesce their own writers, preserve independent output where authorized, and surface the conflict. A deployment never clears the shared in-progress label merely because its own contender stopped. Automatic selection can move on to another candidate once its abandoned attempt is conclusively settled.

There is no `status: claiming` label or per-device status-label family. Extra labels, fixed settling delays, and rereads are not represented as exclusive claim primitives.

### 5.3 Shared retry history

Fresh workflow IDs, device changes, and label removal do not reset the issue's automatic retry allowance. Admission evaluates linked attempt history and the applicable bounded policy. A continuation retains prior failures and no-progress evidence. Internal step retries are not separate issue attempts.

An operator-authorized retry reset is recorded explicitly. Conflicting lineage or policy evidence fails to attention rather than inventing a fresh budget. Exact global retry-count enforcement is not claimed under simultaneous duplicate starts. It is likewise not claimed when attempt evidence was deleted without a tombstone: an Available issue with no observable attempt comments is indistinguishable from one that was never attempted, so admission treats it as no observable history and does not claim the shared retry/cooldown budget was verified. Strict-budget operators must rely on tombstones and explicit reset records, not on the absence of comments.

## 6. Failure handling and recovery

### 6.1 Confirmed terminal failure

Terminal finalization belongs to the durable execution boundary, not an optional last success-only preset step. It covers exhausted retries, failures, cancellations, and blocked outcomes while preserving the original outcome separately from cleanup errors.

The owning deployment quiesces all associated writers, evaluates outstanding mutations, preserves available work under the existing checkpoint/publication policy, and records a terminal handoff. Only then can it release in-progress status to the appropriate next state.

A failed child or internal remediation iteration does not release an issue while the controlling attempt remains active. A required verification failure does not become code review solely because a PR exists. Conversely, a reporting failure does not invalidate verified implementation evidence.

API startup registers `MoonMind.GitHubIssueReconcile` every five minutes, enabled
by default. The existing API schedule observer retries a failed registration
every 30 seconds without depending on the workflow queue it is registering.
Its empty-input maintenance route discovers repositories from configured scope,
durable claim receipts, and its persisted repository list. It rotates through
at most three repositories per sweep with a 40-second limit per repository and
at most 25 issues per scan, with ten seconds per issue; persisted issue/page cursors prevent a busy prefix
from permanently starving later issues. Shared lease decisions use GitHub only.
The same route additionally rotates through at most 25 local claims, including
claims whose advisory labels are missing. A failed controlling execution and all
of its descendants must have been closed for at least five minutes before release
is considered. For a small backlog with healthy services, verified no-work claims
therefore become retryable on the next sweep after that grace period (normally
five to ten minutes after terminal closure and runtime cleanup).
Each claim has a 30-second observation/finalization budget; the sweep stops
starting local claims after two minutes and preserves its rotation cursor. Slow owners
therefore cannot indefinitely starve later claims. Portable comment lineage
retains the existing retry allowance across fresh workflow IDs; exhaustion
produces an attention disposition instead of an unlimited retry loop.

The owner reads complete, bounded Temporal histories rather than inferring death
from comment age or an execution projection. Started agent children must have
matching, digest-validated runtime bindings with completed fenced cleanup. The
workspace owner binds a clean-worktree observation, including untracked files,
to the preserved checkpoint's archive digest and exact revision. The reconciler
then verifies through GitHub that this revision is still reachable from the
recorded source branch. The comparison grants no branch-write, PR, or completion
authority. Remote read failures defer to a subsequent sweep without another agent
turn. A run that never started an agent can release its unused reservation or
confirmed announcement after the same terminal checks.

Automatic release uses the existing finalizer and per-attempt comment receipt:
publish the terminal proposal, observe targeted label changes, and confirm the
released comment before freeing the database reservation. An already-removed
in-progress label does not bypass the stopped-writer and no-work checks. Lost
acknowledgements resume the recorded finalization plan. Holds, active descendants,
unknown mutation outcomes, unique commits, modified workspaces, missing historical
preservation evidence, and foreign deployment claims remain explicit recovery
dispositions. They are not converted into fresh issue implementations by age.

### 6.2 Unresponsive deployment

A missing local workflow record on device B says nothing about device A's execution. Old activity timestamps, a sleeping laptop, network loss, and a terminated workflow are not interchangeable evidence.

For version-2 claims, another deployment uses complete authenticated GitHub issue/comment reads to check the declared deadline, scope, provenance, conflicting copies, other owners, and holds. If all leases have expired and no other status blocks admission, it rereads before removing only `status: in-progress`, then verifies the result. An observed successor prevents reclamation and retains its advisory label. Expiry does not rewrite the old owner's comment as `released`, claim that its process stopped, or discard its workspace. Existing PR/branch references and retry history remain authoritative inputs for the next assessment. `status: code-review`, `status: recovery-needed`, `status: needs-attention`, and explicit comment holds are never age-cleared.

Persisted version-1 comments never agreed to a deadline, and an old activity timestamp cannot retroactively become a lease agreement. They are therefore ended by one explicit operator act rather than by an invented expiry or by remaining blockers forever.

`MOONMIND_ISSUE_CLAIM_LEGACY_CUTOVER_AT` records the operator's declaration that every version-1 writer is upgraded or stopped. Until it is set, a version-1 attempt still reserves its issue and is reported as `legacy_reservation_awaiting_migration` — a named, actionable diagnostic, not a silent empty backlog. Once set, a version-1 attempt whose comment GitHub timestamps *before* that instant no longer holds write authority; anything announced at or after it is untouched. The order matters: stop or upgrade the old writers first, because they do not understand that a superseded reservation no longer permits publication.

`tools/recover_legacy_issue_claims.py` performs the one-time backlog migration as a reviewed batch rather than dozens of manual edits. It takes an inventory first (owner, protocol version, last known activity, linked PR or branch, explicit holds, proposed action) and only writes with `--apply`. It never deletes a comment, never removes a label, never rewrites a successor's or an untrusted author's comment, and never touches an issue that still has a live reservation or an explicit hold. Retiring a comment stamps the lease it never carried — renewed one lease-duration before the cutover, expired at the cutover — and appends a recovery disposition. Every other field is preserved: the attempt, its lineage, its retry history, its PR and branch references, and its unchanged claim that writers were never confirmed stopped. An attempt with preserved work is retired with `preserve_for_continuation` so the next attempt continues or reconciles it. Mixed deployments therefore drain through an explicit cutover; a missing foreign database or Temporal record is never a prerequisite for, or evidence of, expiry.

Run the command from a repository checkout where `tools/` is available (production images do not copy `tools/` into `/app`; only `tools/verify_deployed_ui_assets.py` ships in the image). Operators can invoke it with `python tools/recover_legacy_issue_claims.py --repository owner/name --cutover-at <ISO-8601-instant> --report /tmp/claim-recovery.json`, adding `--apply` after reviewing the inventory and confirming that old writers have stopped or upgraded. Application rechecks the complete issue evidence before every comment write, including scope, provenance, conflicting copies, holds, and intervening reservations. A remote readback of the exact intended body proves retirement even if the write acknowledgement was lost. Incomplete reads or unapplied planned writes remain in the report and produce a nonzero exit status. The command does not declare the repository available: ordinary selection still checks lifecycle state, prerequisites, retry history, and retained work.

Reconciliation runs through existing MoonMind/Temporal facilities with bounded work and operational defaults. It adds no permanently running coordination container. It can repair incomplete confirmed handoffs, classify inconsistencies, and expose orphaned attempts without access to another deployment's services.

### 6.3 GitHub unavailability

Read errors, rate limits, incomplete pagination, and ambiguous responses stop new admission and shared mutations. MoonMind does not fall back to a local-only claim or interpret unreadable comments as no owner.

Local durable execution retains pending synchronization evidence for retry. Lease renewal retries remain bounded by the last confirmed expiry; after that deadline the old attempt must stop and cannot resume shared writes when connectivity returns. It rereads current GitHub state and uses a new admission for further work. The failure is visible locally while GitHub is unavailable and is reflected in GitHub once access is restored. Visibility and reconciliation are eventual and require at least one healthy authorized deployment.

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

GitHub's merge API can check an expected PR-head SHA [4]. That checks the target revision, not MoonMind ownership. A read immediately before pushing or merging cannot eliminate every pause-between-check-and-write race. An explicitly recorded uncertain push or merge must be reconciled before repeating or replacing that operation; lease expiry does not resolve its outcome. A PR currently appearing unmerged does not prove an earlier request can no longer complete.

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
| Writer or merge-request outcome is unknown | No terminal stop/completion proof is invented; an uncertain shared write must be reconciled before repeating or replacing it |
| Version-2 owner disappears or device sleeps | Another deployment reclaims the expired advisory label from GitHub evidence alone, retaining prior work and retry history |
| Version-1 owner disappears or device sleeps | No retroactive lease; require terminal evidence or authorized operator resolution |
| Renewal stops while an agent is active | The public AgentRun entrypoint cancels through the existing runtime owner before the last confirmed deadline; reconnection cannot revive the expired attempt |
| All three announce simultaneously | Possible duplicate starts are represented honestly, and observed contenders quiesce without clearing each other's status |
| Old attempt resumes after an observed successor | It makes no new shared mutation and does not replay obsolete cleanup, and it can still retire its own reservation |
| Reservation announced, then local capacity rejects the launch | The reservation is not renewed and lapses on its announcement deadline; the deployment backs off instead of claiming the next issue |
| Withdrawal attempted while a contender is present | The withdrawal succeeds; other lifecycle updates still require uncontested ownership |
| Label or terminal-comment bookkeeping fails during finalization | Ownership still ends; the bookkeeping stays pending and retryable |
| Advisory status label with no attempt evidence at all | The issue is reassessed and the stale label reconciled, without summoning an operator |
| Version-1 backlog is migrated | Old comments remain as history but stop acting as permanent reservations |
| Completion dependencies remain open | Independently useful implementation can still start; the completion gate still holds closure |
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

Undeclared expiry based on silence, per-device lock labels, forced partial-PR merges, and timestamp-based winner selection are excluded. Explicit comment leases bound failed-owner lockouts while retaining the distinction between claim authority, process cleanup, preservation, and completion. GitHub labels remain an advisory coordination surface.

## 12. Legacy cutover implementation status

The conservative legacy reconciliation cutover for this state machine is implemented (not merely desired): the decision layer lives in `moonmind/workflows/temporal/github_issue_legacy_cutover.py` under the single surviving policy owner (`github_issue_lifecycle`), and is reachable through the existing `github_issue.assess_legacy` / `github_issue.plan_legacy_repair` activity bindings instead of a new coordination service. Bounded read-only assessment, shared-guard repair planning, retained-history drainage, mixed-deployment qualification, and upgrade/rollback fixtures are covered by `test_github_issue_legacy_cutover_4184.py` and `test_github_issue_legacy_cutover_wiring_4184.py`, including an emission-path proof that new executions never write obsolete shapes. Full cutover behavior is specified in [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md). Post-integration provider and three-device qualification remains separately reported; source implementation alone does not claim deployed compliance.

## References

[1] [GitHub REST API: labels](https://docs.github.com/en/rest/issues/labels).  
[2] [GitHub REST API: issue comments](https://docs.github.com/en/rest/issues/comments).  
[3] [GitHub REST API best practices: conditional requests and retries](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).  
[4] [GitHub REST API: pull requests and merge revision checks](https://docs.github.com/en/rest/pulls/pulls).

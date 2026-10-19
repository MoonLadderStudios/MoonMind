# Checkpoint Branch System

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Owners:** MoonMind Platform + Workflow Runtime + Dashboard  
**Updated:** 2026-09-06  
**Audience:** Workflow, runtime, recovery, API, and dashboard contributors and operators  
**Authority:** Checkpoint Branch lineage, branch-turn lifecycle, source restoration, promotion, and verified output-branch projection. Workflow Publishing owns authored publication intent; repository-provider contracts own repository targets and publication evidence.  
**Owning Surface:** Checkpoint Branch records and operations, Step Execution handoff, and recovery/output projections  
**Related Implementation:** `workflow_checkpoint_branches`, `workflow_checkpoint_branch_turns`, `agent_runtime.publish_terminal_checkpoint`, and the existing Step Execution and AgentRun owners.

Omnigent checkpoint and branch authority comes from the bound
[policy snapshot](../Omnigent/PolicyAuthority.md). The single authored repository,
branch, and publication contract is defined in [Workflow Publishing](WorkflowPublishing.md).
This document describes target behavior, not an implementation or permission to
bypass currently enforced recovery gates.

## Related Docs

[Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md), [Recovery](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md), [Workflow Remediation](WorkflowRemediation.md), [Workflow Publishing](WorkflowPublishing.md), [Finish Summary](WorkflowFinishSummarySystem.md), [Repository Access](../RepositoryAccessAndWorkspaceDesign.md), and [Omnigent Adapter](../Omnigent/OmnigentAdapter.md).

## 1. Purpose

Checkpoint Branches support independently admitted continuations from durable workflow/step checkpoints, immutable turns, candidate comparison, and explicit promotion. Checkpoints, Step Executions, artifacts, and isolated code workspaces supply the durable identities.

Instructions and workspace/runtime policies may differ by admitted continuation; publication is not a per-turn override. Automatic/nested branches inherit frozen parent intent. An operator's independent execution from saved content uses fresh normal admission with one policy; children cannot manufacture that escape.

Controlled-failure remote preservation requires compatible scope and destination authority. None uses qualified artifact saving, never a recovery-push exception. Unsupported required save contracts reject new execution before work; saving cannot turn failure into success.

## 2. Architectural decision summary

Logical Steps, Step Executions, manifests, and checkpoints remain execution truth. Each Checkpoint Branch turn creates a Step Execution or qualified equivalent evidence. The execution owner allocates runtime/host/lease/session identities; callers supply intent only. Transient retries reuse the operation identity. After an uncertain start, remediation observes the exact persisted claim without redispatching; a lost acknowledgment cannot prove no effect or a successful repair.

Repository branches are isolation bindings, not product identities; analysis-only branches need none. Provider-aware bindings follow [Lore VCS Integration Design](LoreVcsIntegrationDesign.md), not a new Git-only authoring domain. Provider sessions and URLs are runtime/diagnostic metadata, never durable branch authority.

Creating, continuing, or publishing a candidate does not promote it. Promotion is workflow-owned and evidence/side-effect/approval-gated. Execution, repair verification, publication, promotion, and cleanup remain separate facts.

Verified recovery publication proves remote availability, not objective success, a PR, or promotion. A failed workflow retains FAILED after saving. Artifact-only preservation is durable saved content, not a Saved Work Branch.

## 3. Candidate lineage and preservation

The checkpoint is the fork point; immutable branch-local turns create execution evidence. Several candidates may share a checkpoint, but fan-in promotes at most one. Combining outputs requires a new admitted branch or Step Execution naming both as evidence, not an automatic merge. Historical candidates remain inspectable.

### 3.1 Controlled failure preservation

Preservation uses existing authoritative state, not a speculative new branch
from arbitrary logs. Candidate source preference is the terminal Step Execution's
live managed workspace, equivalent independently verified output branch/PR
revision, then the latest valid checkpoint for that same Step Execution and
baseline. With no valid source, report unavailable/skipped and do not claim saved
work.

Select by identity and digest, not timestamps alone. Among valid equivalent
boundaries, prefer the latest one containing that exact head, such as
before_publication, after_gate, or after_execution. Apply source/save and remote
publication authority independently.

---

## 4. Terminology

| Term | Meaning |
| --- | --- |
| Checkpoint | Durable evidence sufficient to restore/validate a workflow boundary |
| Checkpoint Branch | Product continuation lane forked from a validated checkpoint |
| Branch Turn | Immutable instruction-bearing semantic execution on that lane |
| Work branch | Provider-owned repository isolation binding, not another authored selector |
| Branch root checkpoint | Validated fork point |
| Branch head | Latest accepted turn/Step Execution/checkpoint and applicable revision |
| Parent branch/turn | Explicit lineage source for a fork |
| Promotion | Explicit acceptance as canonical workflow progress |
| Comparison | Artifact-backed comparison of candidates |
| Archive | Non-destructive removal from active presentation |
| Controlled failure | Structured terminal decision while authoritative source remains available |
| Sudden infrastructure failure | Loss before deterministic capture/finalization can be completed |
| Terminal checkpoint publication | Admitted isolated remote preservation after controlled failure |
| Saved Work Branch | Remotely verified recovery branch shown to operators |
| Output branch | Read-only projection of verified normal or recovery publication |

Rerun, retry from a step, step re-execution, checkpoint fork, turn, promotion,
and publication remain distinct. Repository publication means a remote effect;
local saved content and workflow promotion do not imply a push or merge.

---

## 5. Admission invariants

Validate source workflow/run/step/ordinal, boundary, ref, digest, and immutable instruction/policy evidence before effects. Branches, comparisons, and promotion are explicit durable records, never inferred from logs, names, sessions, or UI projections. External effects must be admitted and isolated/idempotent, compensated, or approval-gated before work.

An authored `maxBudgetUsd` is immutable launch authority. A runtime without a provider-native USD hard stop rejects before capacity, host, session, or billing acquisition; terminal cost observation is not prospective enforcement.

## 6. Branch lifecycle

### 6.1 States

| Branch state | Meaning |
| --- | --- |
| created | Record exists; no turn launched |
| preparing | Source/workspace/context/runtime preparation |
| active | Turn active or ready for continuation |
| blocked | Required evidence, permission, or prerequisite unavailable |
| failed | Latest turn failed without an active automatic continuation |
| succeeded | Latest turn completed its required gates |
| promotable | Candidate is eligible for explicit promotion |
| promoted | Accepted into canonical progress |
| archived | Hidden from active work, evidence preserved |
| superseded | Replaced by another accepted candidate or fork |

Turn states are created, preparing, running, checking, succeeded, failed,
blocked, canceled, and superseded. Existing repository-binding publication
projections remain unpublished, preparing, published, failed, and archived.

Terminal preservation operation dispositions are pushed, already_published,
no_changes, skipped, and failed. Here no_changes is the existing recovery
operation's no-content-to-preserve disposition, not a new alias for workflow
NO_COMMIT. Skipped is ineligible or unavailable preservation; failed means an
eligible attempted operation failed. Both remain distinct from the original
workflow outcome.

### 6.2 Operations

```text
checkpoint_branch.create
checkpoint_branch.continue
checkpoint_branch.fork
checkpoint_branch.compare
checkpoint_branch.promote
checkpoint_branch.archive
checkpoint_branch.publish
checkpoint_branch.publish_terminal_checkpoint
agent_runtime.publish_terminal_checkpoint
```

Every effect is idempotent and audit-backed. These operations do not bypass the
single publication compiler or grant new destination authority.

### 6.3 Promotion

Record promoted branch/turn/Step Execution, accepted output refs, applicable
provider revision/PR references, verdicts, side-effect dispositions, downstream
invalidation/revalidation, and approval/policy evidence. Do not delete competing
branches. Promotion cannot waive missing publication or source handoff authority.

### 6.4 Terminal preservation lifecycle

```text
structured terminal failure
  -> classify save and remote-publication eligibility separately
  -> resolve authoritative live workspace or checkpoint
  -> preserve required content through qualified artifact/checkpoint capture
  -> if remote preservation is admitted, resolve isolated work branch
  -> reconcile an already-published exact revision
  -> prepare deterministic candidate and outbound scan
  -> conditional provider publication and exact remote verification
  -> persist operation summary and unified publication evidence reference
  -> project verified output independently from primary failure
  -> release cleanup only after the required preservation handoff
```

The remote attempt is best-effort relative to the primary objective, but is
awaited rather than fire-and-forget when eligible. Save failure retains the sole
workspace under bounded recovery. None never becomes an implicit remote-push
exception.

A branch turn keeps its child's actual result once child compute has
succeeded. If the parent capture or checkpoint fails, or the turn is canceled,
the turn is recorded failed or canceled with `saveCommit.status: incomplete`.
The child's own verified saved candidate (`savedWorkspaceCheckpoint` refs and
digests, plus the history bundle and index patch its manifest names as
`dependencyRefs`) is retained in the stored agent result. Preservation never
upgrades that outcome. A turn advances the branch head only while the head it
started from, both its checkpoint ref and head version, is still current, so a
rollback to the same ref by another owner is never overwritten. A later turn
that finishes after the head moved keeps its outcome and checkpoint on the turn
(`headAdvanced: false`) without replacing newer work, the latest-result index,
or the branch state.

---

## 7. Data model

The following physical columns illustrate existing records. Their Git-specific
names are not public authoring aliases. Provider-aware target/binding/checkpoint
interfaces evolve through their existing owners as specified in
LoreVcsIntegrationDesign, without another parallel repository domain.

### 7.1 Persistence owners

Exact columns and relationships live in the [database models](../../api_service/db/models.py) (`WorkflowCheckpointBranch`, `WorkflowCheckpointBranchTurn`, `WorkflowCheckpointBranchGitBinding`, `WorkflowCheckpointBranchArtifact`) and [typed models](../../moonmind/schemas/checkpoint_branch_models.py). The [branch service](../../api_service/services/checkpoint_branch_service.py) owns persistence. These sources describe current serialization, not proof that every target-state migration is complete.

Branches retain source/parent lineage, policies, current accepted head, repository binding, promotion evidence, and diagnostics. Turns retain immutable source/instructions, stable idempotency and execution identities, outcome and artifact refs. Git-specific physical names are internal bindings, not new public authoring aliases.

A system recovery branch requires valid checkpoint or typed source evidence. It may be labelled “Recovered work from failed workflow” with `created_by = system:terminal-checkpoint`; missing source never fabricates a branch record.

`binding_metadata.terminalPublication` is only a bounded recovery projection (intent, disposition/reason, source, revision, remote observation, evidence ref). Its `evidenceRef` points to validated `moonmind.publish.repository.v1` under the exact owning attempt. A writable projection or `remoteVerified` boolean is not proof. Add indexes only for a concrete query need.

### 7.2 Step Execution manifest extension

The manifest can include branchId, branchTurnId, rootCheckpointRef,
parentBranchId, parentTurnId, and the provider work-branch binding. These are
server-owned lineage/derived state, not replacements for the Step Execution
tuple or new authoring fields. Historical Git-specific gitWorkBranch projections
remain readable without becoming caller-controlled branch authority.

### 7.3 Terminal publication evidence

Each finalizer attempt records a bounded recovery-operation artifact, including
failed or skipped attempts. It identifies workflow/run/Step Execution, branch,
source checkpoint/digest, preservation intent, disposition/reason, idempotency,
source/target association, and safe diagnostics/evidence refs.

For an actual repository publication, the publisher emits the single
`moonmind.publish.repository.v1` contract owned by
[Lore VCS Integration Design section 3.13](LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence).
The recovery-operation summary references it. Do not introduce a competing
Git-only publication schema, treat a boolean remoteVerified projection as proof,
or relabel old evidence as new. None and ineligible skipped operations produce
no repository-publication artifact, although their recovery/save summary remains
required where finalization ran.

Historical terminal-checkpoint `schemaVersion: v1` payloads and Git-only fields
retain their original decoding/bytes. They are not new authoritative repository
writes. Current acceptance validates artifact ownership, exact attempt and target,
connection/client provenance, scan, conditional operation, and provider revision
proof through the shared publication reader.

Raw logs, diffs, provider payloads, credentials, and full command output stay in
appropriately authorized diagnostic/patch artifacts rather than compact records.

---

## 8. API surface

### 8.1 Branch discovery

```http
GET /api/executions/{workflowId}/checkpoints
GET /api/executions/{workflowId}/checkpoint-branches
GET /api/executions/{workflowId}/checkpoint-branches/{branchId}
GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/turns
```

### 8.2 Create branch

```http
POST /api/executions/{workflowId}/checkpoint-branches
```

Representative scoped request:

```json
{
  "source": {
    "runId": "run_abc",
    "logicalStepId": "implement-story-S004",
    "executionOrdinal": 2,
    "checkpointBoundary": "after_execution",
    "checkpointRef": "artifact://checkpoint-after-execution"
  },
  "label": "Try minimal API contract fix",
  "instructions": {
    "text": "Keep the useful changes. Fix only the API contract failure. Add one regression test."
  },
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "runtimeContextPolicy": "fresh_agent_run",
  "idempotencyKey": "mm:wf:checkpoint:after_execution:minimal-api-fix"
}
```

The server derives repository/branch and publication scope from authenticated
parent authority and validated source. There is no independently authored
publishMode, startingBranch, or targetBranch here. A separately authorized user
request to start independent work from this checkpoint goes through the normal
new-execution authoring path with one task.publish selection and explicit lineage;
it is not an override accepted from a scoped child.

Create, continue, and fork persist intent and route the initial turn through the
server-owned execution owner. Callers never prepopulate Step Executions, AgentRuns,
bridges, hosts, leases, sessions, checkpoints, results, publication, capture,
cleanup, or terminal evidence.

### 8.3 Continue branch

`POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/continue` accepts new immutable instructions, label, qualified workspace/runtime policy, and idempotency key.

### 8.4 Fork branch

`POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/fork` also identifies the exact `parentTurnId`.

Continue/fork allocate a new semantic Step Execution from the accepted parent turn output, digest, and execution identity. They never revert to the root after progress or re-evaluate publication defaults. [Typed requests](../../moonmind/schemas/checkpoint_branch_models.py) own exact current serialization.

### 8.5 Launch branch turn

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/turns/{branchTurnId}/launch
```

The body accepts stable idempotencyKey and, where required, expectedBranchHeadVersion.
Unknown fields are rejected. No runtime identity, workspace, result, publication,
diagnostics, capture, cleanup, or terminal authority can be supplied by a caller.

One durable owner validates pinned source/lineage/digests, instructions, bindings, scope, stored policies/profiles, current credentials, and expected head. It claims the turn and persists Step Execution/AgentRun identities before dispatch. Ordinary profile-bound AgentRun uses `checkpointRecovery.recoveryAction = branch_required`; deterministic OmnigentSession owns bounded host/session/evidence/cleanup Activities. Harvest result, workspace, checkpoint, publication, capture, and cleanup evidence before releasing host/Profile authority. Turn outcome remains separate from remediation verification.

`POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/verification` accepts only `verifierArtifactRef` (`artifact://...`). The owner reads immutable JSON and checks publisher, source workflow/run, completed checking turn, Step Execution, exact head version/ref/digest, and canonical verdict. It records through the remediation-head owner; callers cannot provide verdict/head updates. Rejected evidence returns 409 `checkpoint_verification_rejected`; schema-invalid requests return 422. Accepted receipts resume the saved action without repeating compute. Candidate mutation by the verifier records objective failure. Failed/canceled/blocked turns and finalization retries resume the same read-only action verification.

Retries reuse one server-derived workflow/branch/turn launch identity and cannot send a second first message. The owner verifies the persisted instruction bytes before linking that exact Temporal ref/digest to its recorded created Step workflow; local Omnigent instructions keep their validated read/promotion path. Fenced terminal retention reconciles a missing historical instruction link from the stored request; prepared-input lists and submitted refs cannot grant access. Fresh session authority never copies the source OAuth lease. The operation ledger claims launch before artifacts; turn-plus-kind ownership reuses and byte-validates committed artifacts after partial failures.

Terminal acceptance resolves, validates, links, and pins retained artifacts, including nested checkpoints and copied Omnigent evidence. The actual Activity must match the stored turn launch before reads can use its source, turn, or recorded AgentRun workflow scope; artifact references cannot add authority, and raw/quarantine gates remain in force. Reject local paths, raw credentials, provider grants, and unrestricted authority; live handles remain runtime-owned. If bounded retention/validation retries exhaust, record sanitized blocked state with only the unchanged terminal payload's digest.

### 8.6 Compare branches

```http
GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}&objective={text}&rubricId={rubric}
```

Return artifact-backed diffs, gates, and explanation for one explicit objective and the sole supported `checkpoint-branch-gates` rubric; reject other rubrics. Preserve both candidates, source differences, missing-evidence flags, limits, observed/estimated cost and provenance, winner-or-none, and no-side-effect attestation. No work, defaults, deployment, promotion, publication, or inference is authorized. Missing promotion gates fall back to `latest_verification_verdict`; budgets use `diagnostics.runtimeSelection`. Partial/failed/evidence-poor samples have no winner or universal quality/security claim. Incompatible lineage fails with `incompatible_checkpoint_lineage`. Repeated identical requests reuse the ledgered report; changed diagnostics invalidate it.

`POST /api/executions/{workflowId}/checkpoint-branches/comparison-preview` previews 2–4 explicit candidates without effects. With `allowNewRuns: false`, each names a distinct existing branch with completed head. With true, each new candidate supplies shared nonblank `sourceIntentRef`, positive finite `maxBudgetUsd`, explicit workspace policy, and fresh agent run within resource bounds. Reused candidates carry no new-run settings. Reject Cartesian expansion. Attest isolation only when every new run uses an isolating policy; otherwise disclose it. Return run count, material cost/privacy/authority changes, and stable preview digest. Actual creation still uses ordinary admission.

### 8.7 Promote branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/promote
```

The request binds expected head Step Execution and applicable exact provider
revision, required approval evidence, and a stable idempotencyKey. Historical Git
expectedHeadCommit forms remain frozen at their original contract. New provider
identities follow the repository contract. Promotion does not broaden publication
or grant source access merely because the candidate passed tests.

### 8.8 Archive branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/archive
```

Archive hides active work without deleting records, artifacts, verified refs,
or authorized diagnostic history.

### 8.9 Execution detail output branch

The optional outputBranch is a read-only projection of verified provider evidence
and its checkpoint binding. A historical canonical finish summary is an allowed
compatibility read source only when it preserves the same verified facts.


Rules:

1. Detail-only until a concrete list/filter need justifies indexing.
2. Name can be shown without URL when no safe provider URL exists.
3. URLs are provider-generated/server-validated, never invented from untrusted metadata.
4. Intent distinguishes normal_publish from terminal_checkpoint.
5. PR identity is independent; a run can show both verified branch and real PR.
6. New authoring has one canonical repository branch/target binding. startingBranch, targetBranch, git.startingBranch, and task.git.branch are historical-only fields and cannot be used as new input fallbacks, PR selectors, or output evidence.
7. Output is not copied back into the authored branch on Edit/Rerun, and a coordinator's local None does not replace the original scope policy.

### 8.10 Authored context, source evidence, and historical decoding

The source checkpoint identifies the exact old run/step, baseline/revision,
content, and artifact authorization. It does not grant live repository or
publication authority. The new/inherited canonical repository target supplies
one applicable authored branch role. Runtime isolation creates a derived work
branch. Verified publication creates an output projection. These four facts are
not interchangeable.

Scoped branch turns preserve their admitted policy and validated target derivation.
An existing-PR continuation explicitly names its PR locator and derives head/base
through the target contract. It does not infer the PR from a source checkout,
non-default generic branch, or old startingBranch fallback.

Frozen old histories retain original fields, bytes, digests, and replay semantics.
New writers reject superseded aliases. Draft reconstruction can collapse old
copies only with proven equivalence to the single role; conflicting source/target
pairs or unknown provenance require visible review. Choosing the newest field,
current default, or a convenient output head is not reconstruction. A user wishing
to change intent must use the normal newly admitted authoring path.

---

## 9. Workspace and repository policy

### 9.1 Branch creation modes

| Mode | Meaning |
| --- | --- |
| from_checkpoint_worktree | Restore qualified archive/live state into a new isolated workspace |
| from_checkpoint_patch | Start at exact baseline and apply validated delta |
| from_last_accepted_commit | Use the latest accepted exact provider revision |
| fresh_from_source_branch | Prepare from the single admitted source branch |
| external_provider_state | Use qualified provider-state restoration only |

Existing Git-named implementation modes normalize through the repository owner;
they are not another public branch selector.

### 9.2 Workspace policies

Reuse the existing continue_from_previous_execution, restore_pre_execution,
apply_previous_execution_diff_to_clean_baseline, start_from_last_passed_commit,
and fresh_branch_from_source policies where qualified. Record the selected policy
in branch/turn/Step Execution evidence. These policies preserve or derive content;
they cannot rewrite publication intent or recover old credential grants.

### 9.3 Work branch naming

```text
mm/{workflow-slug}/{logical-step-slug}/{checkpoint-short}/{branch-short}-{label-slug}
```

Names are sanitized and stable under idempotency. Protected main/master,
detached/empty/unknown refs are never work branches. A collision is reusable only
when binding evidence proves the same branch/operation owner; otherwise fail closed.
Terminal preservation reuses a safe bound output at the same exact head when
possible and otherwise uses this existing deterministic naming owner.

### 9.4 Publish versus promote

Remote publication and canonical workflow promotion are independent. A branch
can be neither, published only, promoted only, both, or recovery-published after
failure without being promotable. A later Publish Saved Work request is separately
admitted through the existing publisher; it does not alter the original scope.

### 9.5 Terminal checkpoint publication on controlled failure

#### 9.5.1 Eligibility

Remote terminal publication requires a structured terminal decision, retained
authoritative state, qualified isolated target, allowed destination mutation,
current credentials, and outbound policy **compatible with the frozen scope**.
Permission to edit local repository files is not permission to push.

| Condition | Disposition |
| --- | --- |
| Controlled user_error/execution_error | Attempt only with all admitted remote prerequisites |
| Controlled integration_error | Same, or adopt already verified equivalent provider output |
| Caught AgentRun timeout with reachable state | Same admitted preservation path |
| Review/verification/gate failure after work | Same, using exact checkpoint if necessary |
| system_error/unhandled failure or host/worker/workflow loss | No speculative new publication; preserve/adopt only available verified evidence |
| User cancellation | New remote terminal publication remains excluded in the initial qualified contract |
| Explicit scope None, read-only, dry run, or noRemoteWrites | No remote preservation; qualified required artifact saving remains separate |
| No candidate content and no existing output to preserve | Recovery operation no_changes |

A caught budget-exhausted AgentRun result differs from a workflow timeout that
prevents finalization. No classification grants authority absent from admission.

#### 9.5.2 Live managed-workspace flow

Defer destructive cleanup, preserve required captured content, inspect equivalent
verified output, choose the admitted isolated work branch, stage only publishable
tracked/untracked paths, create at most one deterministic candidate when needed,
scan the full intended outbound range, use exact provider CAS/lease semantics,
verify remote revision, retain the unified publication artifact and recovery
summary, then release cleanup under the save contract.

A bounded deterministic Git commit message may be:

```text
MoonMind terminal checkpoint for workflow {workflowId} run {runId}
```

Use existing safe workflow-provided text only when bounded and validated. Never
force protected refs or reuse broader source credentials to make preservation work.

#### 9.5.3 Checkpoint-restoration fallback

A parent can reach a controlled failure after child completion. Check existing
verified child publication first, then select an exact valid checkpoint, verify
identity/digest/baseline/Step Execution lineage, restore in isolation, and invoke
the same admitted preservation operation. Missing authoritative state yields
skipped/checkpoint_unavailable, not a branch reconstructed from logs.

#### 9.5.4 Existing branch and PR adoption

Before repeating effects, reconcile bound output, exact live remote revision,
matching PR head, or qualified provider-native evidence. Equivalent verified
state returns already_published without another commit, push, branch, or PR.
Adopting old remote facts does not turn stale attempt evidence into current Skill
objective completion.

#### 9.5.5 Idempotency and concurrency

The existing operation identity includes workflowId, runId, terminal Step
Execution or checkpoint digest, and terminal-checkpoint contract version. Record
candidate, branch owner, and exact remote expectation before effects. Retry must
reuse them, reconcile an already-equal remote, and reject unexpected ownership,
lease widening, or unresolved conflict. Do not add a second operation just because
another authorized API path requests recovery.

#### 9.5.6 Publication result contract

The recovery result is an operation summary, not a second repository evidence
schema. It carries intent terminal_checkpoint, disposition/reason, attempted
flag, safe projected branch/revision, and the validated evidenceRef. Actual remote
operations use moonmind.publish.repository.v1 with required connection/client,
scan, and exact provider proof. Skipped/None results do not fabricate that artifact.

Existing reason codes include graceful_failure_checkpoint_pushed,
already_published, no_changes, policy_disabled, read_only, checkpoint_unavailable,
workspace_unavailable, system_failure_ineligible, scan_rejected,
protected_branch, lease_conflict, authentication_failed, and
remote_verification_failed. Preserve the distinction between ineligibility and
attempted failure.

#### 9.5.7 Finish-summary semantics

A failed objective remains FAILED at its original stage/reason. Local recovery
publication and saved-work evidence are separately projected with their intent
and evidence refs. Neither a derived branch mode nor output head overwrites the
original authored policy/branch. Secondary scan/push/verification/reporting errors
cannot replace the first meaningful primary diagnostic.

#### 9.5.8 External providers

Adopt independently verified provider-native output through the canonical result
boundary. A session ID or artifact listing alone cannot produce a Saved Work
Branch. A new local recovery operation requires a qualified local checkpoint or
provider workspace binding that reconstructs exact content plus fresh compatible
authority; no synthetic checkout from vague provider output is allowed.

---

## 10. Runtime/session policy

| Policy | Meaning |
| --- | --- |
| fresh_agent_run | Fresh ordinary AgentRun for the semantic turn |
| reuse_session_new_epoch | Qualified workflow-scoped session reset for a new epoch |
| reuse_session_same_epoch | Explicit rare capability/approval-gated continuity |
| external_provider_continuation | Qualified provider continuation contract |

New branches and earlier-turn forks normally use fresh_agent_run. Same-branch
continuation uses fresh execution or a qualified new epoch. Omnigent's current
branch mode uses fresh session authority. Future same-session behavior requires
its explicit typed provider contract, not a generic override.

Terminal preservation is finalization work, not a new agent turn. It invokes the
trusted publisher/restorer and does not buy another model request. Later explicit
continuation of saved work is a separate semantic operation.

---

## 11. Context bundle

Each turn receives an immutable digest-addressed context bundle with workflow/run,
logical step/ordinal, reason, branch/turn/parent/source lineage, original input
snapshot and plan refs/digest, instruction refs, workspace policy, exact baseline
and delta/restore evidence, prior verdict/diagnostic refs, comparison refs, and
builder contract identity.

Bounded summaries and refs only. No raw logs/diffs/provider payloads/credentials.
The derived work-branch binding does not become an authored selector. Context
carries or references the existing frozen publication-scope authority, never an
independent turn publishMode. Terminal preservation uses its operation/result
and checkpoint bindings without manufacturing a full agent context bundle.

---

## 12. Omnigent integration

### 12.1 Fresh Omnigent session from checkpoint

Current mode `fresh_omnigent_session_from_checkpoint` validates source manifest/digests/lineage, exact repository baseline/head, Profile, and current credentials; restores isolated content; sends immutable `parameters.omnigent.prompt.instructionRef`; and captures output into the new turn's artifacts under admitted policy. Launch identity includes workflow/branch/turn and differs from the source first message.

Independent `branchCreationAvailable` evidence is required; a live session is insufficient. Capacity waits are distinct from permanent source denials.

### 12.2 Qualified provider continuation

Same-session continuation requires its own typed lifecycle, validated source-session binding, explicit `continuationMode`, immutable instructions, and harvest owner. It is not a generic override or publication grant.

### 12.3 Omnigent terminal publication

Use the shared publication reader for verified provider-native output. Artifact-only saving cannot prove a remote branch; restored publication still requires exact source/target and compatible authority, including None's prohibition.

## 13. Branch comparison

[Compare branches](#86-compare-branches) defines the request and evidence contract. A failed source's saved content may be compared, but its failed objective and save/remote verification remain separately visible. Large differences remain artifact-backed.

## 14. Security and isolation

Branches inherit artifact, runtime, repository, secret, and scope policy. No raw
secrets enter records or artifacts. Refs are not grants. Validate source before
restore/launch. Isolate workspaces. Never push protected refs, break another
owner's lock, widen a lease, or use restored credentials. Qualified continuation,
non-idempotent effects, and promotion retain their explicit approval/ownership
requirements. Archival preserves evidence.

Remote terminal preservation uses the same secret/content scan, bound
noninteractive credential path, provider CAS/lease, and exact verification as
normal publication. Unified evidence and output projection do not bypass those
checks. Failure to publish preserves the primary failure and required saved work.

Source, digest, plan, workspace, collision, continuation, approval, budget, scan, lease, and remote-verification failures remain distinguishable diagnostics. New admission rejects conflicting bindings/historical-only aliases instead of choosing a convenient value.

## 15. UI requirements

### 15.1 Branch Explorer

Show mainline, source/checkpoint, named candidates, turn outcomes, and branch counts/status. Retain archived/failed evidence for authorized inspection; progressively disclose runtime plumbing without hiding source/scope/save outcome.

### 15.2 Branch actions and previews

Capability-gate create/continue/fork/compare/promote/publication/archive and evidence views. Promote, publish, and archive ask first. Execution previews show source, workspace/derived branch, runtime/session policy, inherited publishing/effective behavior, side-effect risk, budget, and approvals. Inherited values are not override editors. Independent continuation or Publish Saved Work requires normal fresh admission.

Promotion previews show exact head, gates, revision/PR, downstream invalidation, side effects, approvals, and retained competitors.

### 15.3 Saved Work Branch on workflow detail

The Git & Publish facts show verified outputBranch before PR Link. Label
terminal_checkpoint as Saved Work Branch and normal_publish as Published Branch.
Render the safe name as code; link only to a server-validated URL. Failed status
stays failed. PR identity is shown independently.

Do not substitute authored branch, historical Starting/Target Branch, a generated
work name, or an unverified local path for missing output evidence. Historical
fields are read-only provenance, not active new inputs. Run Summary and detail use
the same accepted output projection. Keep the primary failure prominent and any
preservation failure secondary. Artifact-only saved work is shown as saved
content, not as a remote branch.

---

## 16. Artifact requirements

Branch and turn artifacts preserve source checkpoints, immutable instructions, context and restore/binding evidence, agent request/result, Step Execution manifest, accepted head, and diagnostics. Their stable ownership is turn plus artifact kind; see the [turn execution owner](../../api_service/services/checkpoint_branch_turn_execution.py) and [branch workflow](../../moonmind/workflows/temporal/workflows/checkpoint_branch_turn.py).

Promotion records accepted evidence and downstream invalidation. Comparison retains summary, provider range-diff, and metadata. An invoked terminal finalizer writes `output.branch.terminal_publication.json` as its operation summary and links actual unified repository evidence where required. Legacy artifact filenames do not confer provider authority.

Record pushed/already_published/no_changes/skipped/failed honestly. Sudden loss
before finalization does not justify backfilling assumed results. Retention follows
checkpoint/artifact ownership. Failed source status is not permission to delete a
saved remote branch. Archive can hide but not erase evidence. Remote deletion
requires explicit retention policy and audit.

---

## 17. Historical compatibility and bounded extension

Implementation phases and rollout checklists belong in the existing temporary
plans, not this target view. Durable-history compatibility is a contract:

- Preserve original branch/source/policy payload bytes, digests, and Temporal decisions for recorded histories.
- Decode legacy startingBranch/targetBranch/task.git only at frozen historical boundaries. No new create/continue/fork/rerun producer emits or accepts them as active source or PR-selector fallbacks.
- Reconstruct equivalent old intent only with source provenance; surface ambiguous pairs and mixed policies for review.
- New local/remote save behavior is enabled only with qualified preservation and compatible workers. Existing operations cannot bypass deployed recovery gates because this document proposes another mechanism.
- New repository publication uses the unified provider evidence contract and actual admitted connection/client identity. Historical operation summaries remain readable but are not live publication-proof alternatives.
- Definition/default changes cannot change scoped child policy, permit None recovery pushes, or turn an output branch into a newly authored target.
- Private-only (local-only scope) checkpoints are never cross-device recovery points: the legacy cutover assessment reports them with their owner and requires owner recovery or an authorized portable handoff; see [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md).

A bounded automated-exploration definition can declare triggers, maximum
branches/turns, approval-gated promotion, workspace policy, and instruction
artifact templates. It does not supply per-branch publication overrides.
Same-session continuation and broader exploration require separately qualified
capabilities, not silent fallbacks from failed restoration.

Metrics include terminal_checkpoint_publication_eligible, attempted, pushed,
already_published, no_changes, skipped, and failed. Bounded dimensions are failure
class, runtime family, source kind, and reason code, not repository URLs, branch
names, errors, or other high-cardinality/private data.

---

## 18. Verification boundaries

Exercise real create/continue/fork → execution → capture → finalization → detail paths, not only projections. Existing coverage includes [branch workflow behavior](../../tests/unit/workflows/test_checkpoint_branches.py), [API admission](../../tests/unit/api/routers/test_checkpoint_branch_apis.py), [turn execution integration](../../tests/integration/workflows/temporal/test_checkpoint_branch_turn_execution.py), and [Omnigent execution](../../tests/unit/omnigent/test_checkpoint_branch_turn_execution.py).

Qualification must cover corrupt/unauthorized source rejection, isolated exact-baseline restoration, stable launch/artifact ownership, head-version races, approval-gated promotion, and historical replay. Preservation cases include caught failures versus sudden loss, clean-ahead and dirty candidates, no-content dispositions, already-published adoption, uncertain push/PR acknowledgments, None's artifact-only path, and secondary failure without lost work. Verify accepted evidence across the publisher and detail UI; a boolean, helper test, or form alone is insufficient.

New-authoring rejection of legacy aliases and scoped publication overrides remains a target requirement. Retained-history readers preserve original meaning, and independent continuation requires fresh admission.

## 19. Qualification limits

Linked-execution versus subordinate-lane delivery, no-code promotion, broader exploration, and graceful-cancellation remote preservation require explicit qualified capabilities. They cannot relax source/evidence requirements, None's remote-write prohibition, or the distinction between failed objectives and verified saved work. Retention and remote deletion remain explicitly policy-owned.

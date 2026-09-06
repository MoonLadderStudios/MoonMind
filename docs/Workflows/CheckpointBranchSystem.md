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

**Implementation tracking:** Rollout notes, spikes, temporary handoffs, and migration checklists live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design.

## Related Docs

- `docs/Steps/StepExecutionsAndCheckpointing.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`
- `docs/Workflows/WorkflowRemediation.md`
- `docs/Workflows/WorkflowPublishing.md`
- `docs/Workflows/WorkflowFinishSummarySystem.md`
- `docs/Workflows/WorkflowRunsApi.md`
- `docs/Api/ExecutionsApiContract.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/Omnigent/OmnigentAdapter.md`
- `docs/ManagedAgents/ManagedAgentsGit.md`
- `docs/RepositoryAccessAndWorkspaceDesign.md`
- `docs/Workflows/LoreVcsIntegrationDesign.md`

---

## 1. Purpose

Checkpoint Branches let an operator or an admitted workflow create independent
continuations from a durable workflow or step checkpoint. A branch is a
continuation lane backed by checkpoints, Step Executions, artifact refs, and an
isolated repository workspace when code is involved. Branching a conversation is
the mental model, not a substitute for those durable identities.

> From an eligible checkpoint, create a named continuation, execute immutable branch turns, compare candidates, and explicitly promote zero or one candidate into the canonical workflow line.

The system is broader than remediation. Instructions, workspace and runtime
policies may differ in separately admitted continuations. That does not create a
per-turn publication override. Automatically created branches and nested
continuations remain inside the parent's frozen publication scope. An operator
can explicitly author a new independent execution from saved checkpoint content
with its own single policy through normal admission, but a child capability
cannot claim this authority to escape its parent.

The same substrate supports terminal checkpoint preservation. A controlled
failure may preserve authoritative work on an isolated remote branch **only
when that operation is compatible with the admitted scope and destination
mutation authority**. Explicit None uses qualified artifact-backed saving, not
an implicit recovery push. A deployment that cannot honor the required save
contract rejects the unsupported new execution before work. Saved work does not
turn a failed objective into success.

Core goals are safe parallel continuations; durable inspectable branch evidence;
workspace isolation; immutable instructions; repeated turns with lineage;
evidence-backed comparison and explicit promotion; qualified runtime/session
continuation; no silent canonical advancement or unauthorized external effects;
controlled-failure preservation; and a clear distinction between the one
authored branch context, derived work branch, and verified output.

---

## 2. Architectural decision summary

### 2.1 Product-level branch graph above Step Executions

Existing Logical Steps, Step Executions, manifests, and checkpoints remain the
execution-plane truth. Checkpoint Branches add a product-level graph:

```text
Workflow Execution
  -> Logical Step
      -> Step Execution
          -> Checkpoint
              -> Checkpoint Branch
                  -> Branch Turn
                      -> Step Execution
                      -> Checkpoint
                          -> Child Checkpoint Branch
```

A branch is not a Step Execution. Each semantic turn creates a Step Execution or
a qualified continuation operation recorded as equivalent Step Execution evidence.

### 2.2 Product branches and repository branches

A Checkpoint Branch is a product continuation lane. A Git or Lore work branch is
an isolation binding, not that lane's identity. Analysis-only branches need no
repository branch. Repository bindings may be restored under admitted policy
without changing the product branch's durable identity.

Git-specific persistence examples below illustrate existing internal bindings.
They do not reintroduce Git-only authoring or a second repository domain. New
provider-discriminated targets, workspace bindings, checkpoints, and publication
evidence follow [Lore VCS Integration Design](LoreVcsIntegrationDesign.md).

### 2.3 Branching is not ordinary retry

A semantic branch turn has a new Step Execution identity, branch lineage, exact
source checkpoint, immutable instruction artifact/digest, declared workspace and
runtime/session policy, and manifest evidence. Low-level transient retries reuse
the existing operation identity; they are not new turns.

The execution owner allocates Step Execution, AgentRun, bridge, host, lease, and
session identities. API callers provide intent only, never runtime results or
self-attested execution ownership.

### 2.4 Branches are candidates until promoted

Creating, continuing, or publishing a branch does not make it canonical.
Promotion is an explicit workflow-owned decision gated by structured evidence,
side-effect classification, workspace validation, and applicable approval.

For remediation, branch creation, turn execution, action delivery, and repair
verification remain separate facts. Detail projections show source/checkpoint,
workspace/runtime policies, isolated work branch, current turn/version/head,
verified output/PR, verdict and remaining-work refs, publication, promotion, and
archive/cleanup state from their authoritative records.

### 2.5 Provider sessions are runtime bindings

Provider/session/runner/file IDs and URLs are diagnostic or runtime-binding
metadata. Branch records, Step Execution manifests, checkpoints, artifact refs,
repository bindings, and promotion records own the durable product state.

### 2.6 Terminal checkpoint publication is recovery evidence, not success

Verified terminal publication proves that work is available at a remote revision.
It does not prove the objective passed, a PR was created, or a candidate was
promoted. Workflow outcome, save outcome, recovery publication, promotion, and
normal PR/merge completion remain separate.

A failed workflow remains failed with a FAILED finish outcome after successful
preservation. The remote branch is partial-success/recovery evidence. A verified
artifact save is also durable preservation but is not a Saved Work Branch.

---

## 3. Conceptual model

### 3.1 Conversation-style branching mapped to MoonMind

```text
Checkpoint C1
  Branch A: try minimal fix
    Turn A1 -> Step Execution A1 -> Checkpoint A1C
    Turn A2 -> Step Execution A2 -> Checkpoint A2C
  Branch B: try rewrite
    Turn B1 -> Step Execution B1 -> Checkpoint B1C
  Branch C: continue provider state
    Turn C1 -> Step Execution C1 -> Checkpoint C1C
```

The checkpoint is the fork point. Turns are branch-local instruction messages.
Step Executions are the actual attempts. Their source and admitted policy remain
explicit even when the UI presents a simple conversational tree.

### 3.2 Mainline versus candidates

The mainline is the accepted workflow path. A candidate can remain exploratory,
be diagnosis-only, become promotable, publish under its allowed policy without
promotion, preserve failed work, be archived, or fork into children. Historical
branches remain inspectable after a different candidate is promoted.

### 3.3 Fan-out and fan-in

Several branches can start from one checkpoint. Fan-in explicitly promotes one
candidate. Combining multiple outputs requires a new admitted branch or Step
Execution naming both as evidence; it is not an automatic multi-branch merge.

### 3.4 Controlled failure preservation

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

Retry, step re-execution, failed-step recovery, checkpoint fork, turn, promotion,
and publication remain distinct. Repository publication means a remote effect;
local saved content and workflow promotion do not imply a push or merge.

---

## 5. Core invariants

1. Branches are explicit records, never inferred solely from logs, Git names, or sessions.
2. Every branch has validated checkpoint or typed source-state evidence.
3. Source identity pins workflow/run, applicable step/ordinal, boundary, ref, and digest.
4. Launched turn instructions, source, workspace/runtime policies, and scope are immutable.
5. Semantic work creates or references Step Execution manifest evidence.
6. Evidence is append-only across failure, archive, supersession, and promotion.
7. Product branch and repository work-branch identities remain distinct.
8. Repository-mutating candidates have qualified isolated workspaces/work branches.
9. Provider sessions cannot replace MoonMind's durable state or policy authority.
10. Promotion is explicit, never inferred from tests, a push, or a PR.
11. Publication and promotion are independent.
12. External effects are admitted, isolated/idempotent, compensated, or approval-gated before work.
13. Comparisons are artifact-backed, not reconstructed from UI projections.
14. Invalid restore or unsupported continuation fails closed, not by a broader fallback.
15. Controlled failures preserve required work before disposal through the admitted save contract; remote preservation additionally requires compatible publication authority.
16. Saving or pushing a recovery branch does not change the failed objective.
17. An authored maxBudgetUsd remains immutable launch authority. A runtime without a provider-native USD hard stop rejects before capacity, host, session, or billing acquisition; terminal cost observation is not prospective enforcement.
18. Secondary preservation failures do not overwrite the primary failure diagnostic.
19. Saved Work Branch requires independently verified remote revision or qualified provider-native evidence.
20. Infrastructure loss is never described as saved without prior verified artifact/remote evidence; a Saved Work Branch specifically requires remote proof.
21. Retried preservation reuses operation, candidate, and branch identities without duplicate effects.
22. One canonical authored repository branch/target context is distinct from derived work state and verified outputBranch. startingBranch, targetBranch, and task.git aliases are historical-only, not active new-input or PR-selector fallbacks.
23. Automatically scoped branches/turns inherit the frozen publication intent, not a coordinator's local None. A new independent user-authored continuation needs normal fresh admission and cannot be manufactured by a child capability.

---

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

---

## 7. Data model

The following physical columns illustrate existing records. Their Git-specific
names are not public authoring aliases. Provider-aware target/binding/checkpoint
interfaces evolve through their existing owners as specified in
LoreVcsIntegrationDesign, without another parallel repository domain.

### 7.1 workflow_checkpoint_branches

```text
branch_id primary key
workflow_id
root_workflow_id
source_run_id
logical_step_id null
source_execution_ordinal null
source_checkpoint_boundary
source_checkpoint_ref
source_checkpoint_digest null
parent_branch_id null
parent_turn_id null
label
state
branch_kind
workspace_policy
runtime_context_policy
git_repository null
git_base_branch null
git_base_commit null
git_work_branch null
current_head_step_execution_id null
current_head_checkpoint_ref null
current_head_commit null
pull_request_url null
artifact_refs json
promotion_evidence json null
diagnostics json
promoted_at null
archived_at null
created_by
created_at
updated_at
```

A system-owned recovery branch may be created only from a valid checkpoint or
typed state. Its label can be Recovered work from failed workflow and created_by
system:terminal-checkpoint. No source means no fabricated branch record.

### 7.2 workflow_checkpoint_branch_turns

```text
branch_turn_id primary key
branch_id
parent_turn_id null
source_checkpoint_ref null
source_checkpoint_digest null
source_state_kind null
source_state_ref null
source_state_digest null
instruction_ref
instruction_digest
context_bundle_ref null
step_execution_manifest_ref null
created_step_execution_id null
runtime_agent_run_id null
provider_session_id null
git_work_branch null
idempotency_key unique
status
diagnostics json
started_at null
completed_at null
created_at
```

### 7.3 workflow_checkpoint_branch_git_bindings

```text
branch_id
repository
base_branch
base_commit
work_branch
worktree_ref null
provider_workspace_ref null
head_commit null
patch_ref null
pull_request_url null
workspace_policy
creation_mode
publish_status
binding_metadata json
created_at
updated_at
```

Existing binding_metadata.terminalPublication is a bounded recovery-operation
projection, for example:

```json
{
  "intent": "terminal_checkpoint",
  "status": "pushed",
  "reasonCode": "graceful_failure_checkpoint_pushed",
  "source": "live_workspace",
  "headSha": "abc123",
  "baseBranch": "main",
  "remoteVerified": true,
  "verifiedAt": "2026-07-12T12:00:00Z",
  "evidenceRef": "artifact://verified-repository-publication"
}
```

It is not an independently writable publication proof. New evidenceRef points
to validated `moonmind.publish.repository.v1` under the exact owning attempt.
Provider-specific projected fields reflect that evidence and do not establish
another authored source. Add indexed columns only when a concrete query need
justifies them.

### 7.4 workflow_checkpoint_branch_artifacts

```text
branch_id
branch_turn_id null
artifact_ref
artifact_kind
content_type null
digest null
created_at
```

### 7.5 Step Execution manifest extension

The manifest can include branchId, branchTurnId, rootCheckpointRef,
parentBranchId, parentTurnId, and the provider work-branch binding. These are
server-owned lineage/derived state, not replacements for the Step Execution
tuple or new authoring fields. Historical Git-specific gitWorkBranch projections
remain readable without becoming caller-controlled branch authority.

### 7.6 Terminal publication evidence

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

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/continue
```

```json
{
  "label": "Add focused regression coverage",
  "instructions": {"text": "Continue this branch. Add tests without broadening the interface."},
  "workspacePolicy": "continue_from_previous_execution",
  "runtimeContextPolicy": "fresh_agent_run",
  "idempotencyKey": "mm:wf:cbr_01J:turn:add-tests"
}
```

### 8.4 Fork branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/fork
```

```json
{
  "parentTurnId": "cbt_01J...",
  "label": "Alternative: remove adapter abstraction",
  "instructions": {"text": "Fork after Turn 2 and simplify the adapter boundary."},
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "idempotencyKey": "mm:wf:cbr_01J:fork:remove-adapter-abstraction"
}
```

Continue/fork allocate a new semantic Step Execution from the exact accepted
parent turn output, digest, and Step Execution identity. They do not revert to
the branch root after progress or re-evaluate another publication default.

### 8.5 Launch branch turn

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/turns/{branchTurnId}/launch
```

The body accepts stable idempotencyKey and, where required, expectedBranchHeadVersion.
Unknown fields are rejected. No runtime identity, workspace, result, publication,
diagnostics, capture, cleanup, or terminal authority can be supplied by a caller.

One durable owner validates pinned source/lineage/digests, immutable instructions,
repository binding, scope, stored policies/profiles, current credential generation,
and expected head before mutation. It claims the turn and persists Step Execution
and AgentRun identities before dispatch. The canonical profile-bound
external/omnigent request uses checkpointRecovery.recoveryAction branch_required
and ordinary AgentRun; the deterministic OmnigentSession child owns bounded
host/session/turn/evidence/cleanup activities.

Harvest results, workspace/checkpoint/output/publication/capture/cleanup evidence
before releasing host/Profile authority in the normal release-last order. Persist
turn state separately from remediation verification.

Retries reuse the stored runtime launch identity and cannot post a second first
message. Branch execution gets fresh host/session authority, not the source
session's mutable OAuth lease. Caller operation keys deduplicate their API calls;
the server derives one launch identity from workflow/branch/turn so another
recovery operation still reaches the same owner.

The operation ledger claims launch before creating turn artifacts. Each context,
manifest, request, result, and diagnostics artifact uses turn plus artifact kind
as stable ownership key. Retry reuses and byte-validates completed artifacts
instead of allocating replacement refs after partial failure.

Before accepting terminal branch state, resolve retained refs and reject local
paths, raw credentials, provider grants, and unrestricted runtime authority.
Persist safe evidence and bounded facts; live host/lease/session/credential
handles stay with their runtime owners. Link and pin each accepted artifact,
including copied Omnigent artifacts and nested checkpoints. If retention or
validation exhausts bounded retries, a sanitized Activity records blocked state
using only the digest of the unchanged terminal payload.

### 8.6 Compare branches

```http
GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}
```

Return artifact-backed comparison, provider diff refs, gate/diagnostic summaries,
and bounded explanation, not an inferred merge or promotion.

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

Representative Git projection:

```json
{
  "outputBranch": {
    "name": "mm/mm-wf/implement/cp-9f2/recovered-work",
    "url": "https://github.com/owner/repo/tree/mm/mm-wf/implement/cp-9f2/recovered-work",
    "headSha": "abc123",
    "baseBranch": "main",
    "intent": "terminal_checkpoint",
    "status": "pushed",
    "evidenceRef": "artifact://verified-repository-publication"
  }
}
```

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

The safe current mode is fresh_omnigent_session_from_checkpoint. Validate complete
source manifest, digests, lineage, exact repository baseline/head, Profile and
current credential authority. Restore isolated content, create a fresh session,
pass immutable instructions through parameters.omnigent.prompt.instructionRef,
retain admitted policy while selecting a qualified host, capture output in
MoonMind artifacts, and bind it to the new turn.

The Omnigent launch key includes workflowId/branchId/turnId and is distinct from
the source attempt because the first-message digest differs. Branch creation
requires the independent branchCreationAvailable evidence; a live source session
alone is insufficient. Capacity/readiness waits are distinct from permanent
source/evidence denials and do not masquerade as resumability.

### 12.2 Qualified provider continuation

Future same-session continuation requires typed lifecycle activities such as
integration.omnigent.send_message and integration.omnigent.harvest_session, a
validated source-session binding, explicit continuationMode, immutable instruction
ref, and harvest ownership. Server-owned session refs are runtime metadata, not
user-chosen product branch identity or evidence of permission to publish.

### 12.3 Omnigent terminal publication

A verified provider-native branch/PR can supply outputBranch through the shared
reader. Artifacts alone may prove saved content but not a remote branch. A
checkpoint restoration used to publish must preserve exact target/source and
admitted scope, including the explicit None prohibition.

---

## 13. Branch comparison

Comparison artifacts identify left/right candidates, common source checkpoint,
provider range/diff refs, gate/verdict summaries, and a bounded narrative ref.
Large differences remain artifact-backed. A failed source's saved content is a
valid comparison input; its failed objective and remote/save verification remain
visible separately. Comparison neither merges candidates nor grants promotion.

---

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

Fail-closed diagnostics include checkpoint_missing, checkpoint_invalid,
checkpoint_unauthorized, checkpoint_digest_mismatch, plan_mismatch,
workspace_policy_incompatible, git_base_commit_mismatch, git_branch_collision,
protected_branch_ref, side_effect_policy_blocked,
provider_continuation_unsupported, approval_required, budget_exhausted,
outbound_scan_rejected, remote_lease_conflict, and remote_verification_failed.
Binding conflicts and historical-only aliases are rejected at new admission, not
silently reconciled by choosing one field.

---

## 15. UI requirements

### 15.1 Branch Explorer

Show source step/checkpoint and named candidates with their turn outcomes. Keep
mainline prominent, expose branch count/status near checkpoints, and retain
archived/failed candidate evidence for authorized inspection.

### 15.2 Default detail view

The ordinary view remains simple. Provider/runtime plumbing is progressively
disclosed without hiding the actual source, scope, and preservation result.

### 15.3 Branch actions

Supported actions include create, continue, fork, compare, promote, admitted
publication, archive, and evidence/diff/diagnostic views. Availability comes from
backend capabilities. A Publish action cannot change an active scoped policy;
independent Publish Saved Work goes through newly authorized admission.

### 15.4 Risk and authority previews

Before branch execution show exact source checkpoint, workspace policy, derived
work-branch information, runtime/session policy, inherited publication intent and
effective behavior, side-effect risk, budget, and approvals. Inherited fields are
not override editors. A separately authored independent continuation uses the
normal shared Create form with its single context and publication selection.

Before promotion show exact head, verdicts, revision/PR evidence, downstream
invalidation, side-effect disposition, approvals, and competing branches retained.

### 15.5 Saved Work Branch on workflow detail

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

Minimum branch artifacts:

```text
input.branch.root_checkpoint.json
input.branch.initial_instructions.md
runtime.branch.context_bundle.json
runtime.branch.workspace_restore.json
runtime.branch.git_binding.json
output.branch.summary.json
output.branch.latest_head.json
```

Minimum turn artifacts:

```text
input.branch_turn.instructions.md
runtime.branch_turn.context_bundle.json
runtime.branch_turn.agent_request.json
runtime.branch_turn.agent_result.json
output.branch_turn.step_execution_manifest.json
output.branch_turn.checkpoint.json
output.branch_turn.diagnostics.json
```

Promotion uses output.branch_promotion.record.json and its downstream_invalidation
artifact. Comparison uses summary, provider range-diff, and metadata artifacts.
An invoked terminal finalizer writes output.branch.terminal_publication.json as
its bounded operation summary and links actual unified repository evidence when
an operation requires it. Provider-discriminated schema evolution does not infer
authority from these legacy artifact filenames.

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

## 18. Testing requirements

### 18.1 Schema tests

Require source refs/typed state, instruction refs/digests, immutable launched
turns, distinct product/work-branch identities, safe bounded records/URLs, and
secret-free artifacts. New authoring rejects startingBranch, targetBranch,
task.git, caller-supplied outputBranch, and per-turn publishing overrides.

### 18.2 Checkpoint validation tests

Valid source enables qualified creation. Missing/corrupt/unauthorized/wrong-plan
or wrong-workspace evidence blocks. Terminal fallback uses exact Step Execution
and digest, not time alone. Ambiguous state remains skipped/unavailable. New
existing-PR work requires an explicit locator, not a checkout-branch inference.

### 18.3 Repository isolation tests

Start from the expected revision, sanitize generated names, reject protected
refs/foreign collisions, reuse same-owner bindings idempotently, isolate forks,
retain original base, and enforce exact conditional writes. No output URL or
Saved Work Branch without shared-validator remote proof.

### 18.4 Runtime tests

Create/continue/fork allocate appropriate semantic Step Executions and stable
launch ownership. Retry does not create a duplicate first message. Branch failures
preserve artifacts and eligible continuations. Qualified controlled failures and
caught timeouts await admitted preservation; system loss does not invent a push.
Equivalent remote head is adopted, dirty state yields at most one deterministic
candidate, clean-ahead state does not make another commit, no-content preservation
uses its correct operation disposition, and publication failure keeps the primary
failure. Explicit None saves through the qualified artifact path and never pushes.

### 18.5 Promotion tests

Require exact current head, verdicts, side-effect disposition, and approvals.
Record downstream invalidation, keep competing branches, and never automatically
promote a recovery branch because publication succeeded.

### 18.6 Omnigent tests

New branches use fresh qualified session authority and immutable instructionRef;
source refs remain evidence. Launch keys differ from source attempts. Unsupported
same-session continuation is rejected. Capture binds to the right turn. Qualified
provider output is accepted only after verification; session-only output cannot
produce outputBranch.

### 18.7 Workflow, replay, API, and UI tests

Cover parent checkpoint fallback, child-published reconciliation, lost push/PR
acknowledgement, coherent binding/finalizer/detail projections, original failure
preserved after saving, historical cleanup replay, and new qualified save ordering.
Branch-only, PR-only, combined output, missing/unsafe URLs, slash names, and
infrastructure loss display accurately.

Also exercise the single-context contract across Create, continuation, fork,
Edit/Rerun, and remediation; frozen inherited intent through coordinator None;
independent user admission versus child escape attempts; no generic-branch PR
fallback; exact old-pair reconstruction or review; and unified managed/agent
publication evidence. A projection boolean or correct form alone is not proof.

---

## 19. Open questions

Remaining product choices include whether a branch always uses a linked execution
or a subordinate lane, the explicit promotion delivery mechanism, branch-ID
allocation, archive/remote retention policy, bounded comparison scheduling,
high-branch-count presentation, qualified automated exploration, and promotion of
no-code diagnosis. These choices cannot change single-context authority or the
source/evidence requirements.

Broader graceful-cancellation remote preservation requires an explicit qualified
policy. Current controlled-failure rules are not open: remote publication requires
admitted authority and retained exact state; explicit None forbids new remote
preservation; sudden loss cannot fabricate a save; failure stays failure; and
Saved Work Branch requires remote verification.

---

## 20. Desired end state

```text
Workflow
  -> Step
      -> Checkpoint
          -> Checkpoint Branch
              -> Immutable Branch Turn
                  -> Step Execution
                  -> Derived repository/runtime binding
                  -> Saved artifacts and diagnostics
              -> Next Branch Turn
          -> Competing Checkpoint Branch
```

Operators can continue or fork from an exact checkpoint, compare candidates,
promote one, archive others, and publish saved work through the appropriate
already-admitted or independently reauthorized publication path. They do not
juggle starting/target/output fields as competing inputs or silently alter a
scope by editing one turn.

A controlled failure preserves useful work before destructive cleanup under the
qualified save contract. An admitted remotely verified recovery branch is shown
as Saved Work Branch; artifact-only results remain useful saved content. Neither
changes the failed objective. Infrastructure loss never produces an unsupported
claim that work was saved.

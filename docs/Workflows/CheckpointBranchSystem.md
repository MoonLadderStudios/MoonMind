# Checkpoint Branch System

Status: Proposed design  
Owners: MoonMind Platform + Workflow Runtime + Dashboard  
Last updated: 2026-07-12

**Implementation tracking:** rollout notes, spikes, temporary handoffs, and migration checklists should live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

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

---

## 1. Purpose

This document defines the desired-state **Checkpoint Branch System** for MoonMind.

Checkpoint Branches let an operator, workflow, or policy create one or more independent continuations from a durable workflow or step checkpoint. Each continuation branch may use different instructions, workspace policy, runtime policy, model/provider settings, and publish strategy. The mental model is similar to branching a conversation, but each branch is backed by MoonMind checkpoints, Step Execution evidence, artifact refs, and, when repository work is involved, an isolated git branch or worktree.

The system is not limited to failure recovery or remediation. Remediation is one consumer of the primitive. The broader primitive is:

> From any eligible checkpoint, create a named continuation branch, execute one or more branch turns with branch-local instructions, compare competing branches, and explicitly promote zero or one branch back into the canonical workflow line.

The same checkpoint and git-binding substrate also owns **terminal checkpoint publication**. When a repository-mutating workflow reaches a controlled terminal failure while authoritative in-flight work is still available, MoonMind should attempt to commit that work and publish it to an isolated remote branch before the workspace is disposed. This preserves recoverable work without reclassifying the workflow as successful.

Core goals:

1. allow multiple safe continuations from the same checkpoint;
2. preserve every branch as durable, inspectable evidence;
3. isolate code changes with git branches or worktrees;
4. keep branch instructions immutable and auditable;
5. allow repeated branch turns without losing provenance;
6. support branch comparison and explicit promotion;
7. keep runtime/provider continuation semantics behind typed policies;
8. prevent branch exploration from silently advancing, publishing, or mutating canonical workflow state;
9. preserve authoritative in-flight repository work when a workflow fails in a controlled way;
10. expose the saved or published output branch independently from the workflow's requested starting and target branches.

---

## 2. Architectural decision summary

### 2.1 Add a product-level branch graph above Step Executions

MoonMind already has Logical Steps, Step Executions, Step Execution manifests, and Step Execution checkpoints. Those remain the execution-plane source of truth. The Checkpoint Branch System adds a product-level graph over that substrate:

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

A branch is not itself a Step Execution. A branch is a durable continuation lane. Each branch turn creates a new Step Execution or delegates to a typed runtime continuation operation that is still recorded as Step Execution evidence.

### 2.2 Separate product branches from git branches

A **Checkpoint Branch** is the MoonMind product concept. A **git branch** is the repository isolation mechanism used when the branch performs code work.

The two should usually have a one-to-one relationship for repository-mutating branches, but they are not the same identity. A product branch may exist for analysis-only work with no git branch. A git branch may be regenerated or pushed under infrastructure policy while the Checkpoint Branch identity remains stable.

### 2.3 Branching is not ordinary retry

A Checkpoint Branch turn is a new semantic execution of work. It must not be represented as a low-level retry of the same Activity or the same provider call.

Every branch turn that executes agent/tool work must:

1. create a new Step Execution identity;
2. record branch lineage;
3. record source checkpoint identity;
4. record immutable branch-turn instructions by artifact ref and digest;
5. declare workspace policy before launch;
6. declare runtime/session policy before launch;
7. write or update Step Execution manifest evidence.

### 2.4 Branches are candidates until promoted

Creating, continuing, or publishing a branch does not automatically make it the canonical continuation of the workflow. A branch becomes canonical only through an explicit promotion operation.

Promotion is a workflow-owned decision. It must be gated by structured evidence, side-effect classification, workspace validation, and approval policy where applicable.

### 2.5 Provider sessions are runtime bindings, not branch authority

External provider sessions, including Omnigent sessions, may be associated with a branch or branch turn. They do not become the branch source of truth.

MoonMind-owned branch records, Step Execution manifests, checkpoints, artifact refs, git refs, and promotion records are authoritative. Provider ids, session ids, runner ids, file ids, and provider URLs are diagnostics/runtime binding metadata.

### 2.6 Terminal checkpoint publication is recovery evidence, not success

A successful terminal checkpoint publication means only that MoonMind preserved repository work on a remotely verifiable branch. It does not mean that the workflow completed its objective, passed validation, produced a PR, or was promoted.

The following remain separate:

```text
workflow outcome               = success, failure, cancellation, or other terminal state
terminal checkpoint publication = best-effort preservation of authoritative repository work
promotion                       = acceptance into canonical workflow progress
PR or merge                     = normal repository publication and integration
```

A workflow that fails and saves a branch must still finish with a failed workflow state and a `FAILED` finish outcome. The saved branch is partial-success and recovery evidence attached to that failure.

---

## 3. Conceptual model

### 3.1 Conversation-style branching mapped to MoonMind

The familiar conversation model is:

```text
Message 1
  Message 2
    Message 3a
    Message 3b
      Message 4b
```

MoonMind's checkpoint branch model is:

```text
Checkpoint C1
  Branch A: "try minimal fix"
    Turn A1 -> Step Execution A1 -> Checkpoint A1C
    Turn A2 -> Step Execution A2 -> Checkpoint A2C
  Branch B: "try rewrite"
    Turn B1 -> Step Execution B1 -> Checkpoint B1C
  Branch C: "continue provider state"
    Turn C1 -> Step Execution C1 -> Checkpoint C1C
```

The source checkpoint is the fork point. Branch turns are the branch-local instruction messages. Step Executions are the actual work attempts produced by those turns.

### 3.2 Mainline vs branches

The **mainline** is the currently accepted path of the workflow. Branches are candidate continuations from a checkpoint.

A branch may be:

- exploratory and never promoted;
- used for diagnosis only;
- promoted to become the canonical continuation;
- published as a pull request but not yet promoted;
- published only to preserve work from a failed workflow;
- archived after comparison;
- forked into child branches.

The UI may visually emphasize a current mainline, but historical branches remain inspectable.

### 3.3 Branch fan-out and fan-in

Branch fan-out means creating several branches from one checkpoint. Branch fan-in means explicitly promoting one branch's result back to the canonical workflow line.

MoonMind should not automatically merge multiple competing branch results. Combining two branch outputs requires a new explicit branch or Step Execution that names both branches as input evidence.

### 3.4 Controlled failure preservation

Terminal checkpoint publication does not create a speculative branch after arbitrary failure. It preserves one authoritative state that already exists.

The preservation source is selected in this order:

1. the live managed workspace tied to the terminal Step Execution;
2. an already verified remote output branch or PR head at the same commit;
3. the latest validated checkpoint whose manifest pins the terminal Step Execution and repository baseline;
4. no source, in which case publication is skipped and MoonMind must not claim that work was saved.

Checkpoint selection must use identity and digest evidence, not timestamps alone. When several valid boundaries exist, prefer the latest boundary that contains the same repository head, such as `before_publication`, `after_gate`, or `after_execution`.

---

## 4. Terminology

| Term | Meaning |
|---|---|
| Checkpoint | Durable evidence sufficient to restore or validate state at a workflow or step boundary. |
| Checkpoint Branch | MoonMind product-level continuation lane forked from a checkpoint. |
| Branch Turn | One branch-local instruction message that launches or continues work on a branch. |
| Product branch | Synonym for Checkpoint Branch when contrasting with git branch. |
| Git work branch | Repository branch used to isolate code changes for a product branch. |
| Branch root checkpoint | The checkpoint from which a Checkpoint Branch was first created. |
| Branch head | Latest Step Execution, checkpoint, git commit, or provider continuation state for a branch. |
| Parent branch | The branch from which a child branch was forked. |
| Parent turn | The branch turn or checkpoint from which a child branch was forked. |
| Branch promotion | Explicit operation that accepts a branch result as canonical workflow progress. |
| Branch comparison | Artifact-backed comparison between two or more branches. |
| Branch archive | Non-destructive operation hiding a branch from active work while preserving evidence. |
| Branch continuation | Adding another turn to an existing branch. |
| Branch fork | Creating a new branch from a checkpoint, branch turn, or branch head. |
| Controlled terminal failure | A structured workflow or AgentRun failure for which MoonMind still has authoritative live workspace or validated checkpoint state. |
| Sudden infrastructure failure | Loss of the worker, host, workspace, Temporal execution budget, or other system state before MoonMind can deterministically finalize and verify publication. |
| Terminal checkpoint publication | Best-effort commit and remote push of authoritative in-flight work after a controlled terminal failure. |
| Saved work branch | Operator-facing label for a remotely verified branch published through terminal checkpoint publication. |
| Output branch | API/UI projection of the verified branch produced by normal publication or terminal checkpoint publication. |

Terms that must remain distinct:

```text
retry                          = same Step Execution, transient/idempotent low-level retry
step re-execution              = new Step Execution for the same logical step
recover failed step            = linked recovery flow from failed step checkpoint
checkpoint branch              = named continuation lane from a checkpoint
branch turn                    = one instruction-bearing execution on a branch
promotion                      = accept a branch result into canonical workflow progress
publication                    = push git branch or create/update PR
terminal checkpoint publication = preserve authoritative work after controlled failure
```

---

## 5. Core invariants

1. **Checkpoint Branches are explicit.** A branch is never inferred solely from logs, git branch names, provider sessions, or dashboard projections.
2. **Every branch has a root checkpoint or typed source state.** A branch cannot exist without source evidence that can be validated.
3. **Branch source identity is pinned.** The root source must include workflow id, run id, logical step id when applicable, source execution ordinal when applicable, checkpoint boundary, checkpoint ref, and checkpoint digest when available.
4. **Branch turns are immutable.** A turn's instruction artifact, digest, source checkpoint, workspace policy, and runtime policy cannot be edited after launch. Corrections create a new turn or child branch.
5. **Branch work creates Step Execution evidence.** Any branch turn that executes agent/tool work must create or reference a Step Execution manifest.
6. **Branch evidence is append-only.** Failed, archived, superseded, or unpromoted branch evidence remains durable.
7. **Product branches and git branches are separate identities.** A git branch is a binding of the product branch, not a replacement for it.
8. **Repository-mutating branches require isolation.** Code-changing branches must use a distinct git branch, worktree, or provider workspace binding.
9. **Provider sessions are runtime bindings.** Provider state may support continuation, but MoonMind-owned records and artifacts remain authoritative.
10. **Promotion is explicit.** A branch never becomes canonical merely because it completed, passed tests, pushed a branch, or opened a PR.
11. **Publication is separate from promotion.** A branch may be published as a PR or saved-work branch without becoming canonical workflow progress.
12. **Side effects are branch-scoped until promoted.** External effects must be idempotent, isolated, compensated, or gated before branch execution and promotion.
13. **Branch comparison is evidence-backed.** Comparisons must produce artifacts and should not be reconstructed from UI projections.
14. **No silent fallback.** If checkpoint validation, workspace restoration, or provider continuation fails, MoonMind must fail closed or request a safer branch mode.
15. **Controlled failures preserve work before disposal.** When eligible, terminal checkpoint publication is awaited before the authoritative workspace is cleaned up.
16. **Saved work does not change the outcome.** A failed workflow remains failed even when its recovery branch is pushed successfully.
17. **Primary failure wins.** Publication failure, scan rejection, or remote verification failure is secondary evidence and must not replace the original workflow failure.
18. **Remote verification is required.** MoonMind may expose a Saved Work Branch only after independently verifying the remote ref and expected head SHA, or after adopting equivalent provider-native evidence.
19. **No false claims after infrastructure loss.** A sudden infrastructure failure must not be reported as saved unless a remote branch or PR head was already verified.
20. **Retries are idempotent.** Retrying terminal publication must not create duplicate commits, branches, or publication records.
21. **Requested and produced branches are distinct.** `startingBranch` and `targetBranch` describe workflow input; `outputBranch` describes verified output.

---

## 6. Branch lifecycle

### 6.1 States

Checkpoint Branch states:

| State | Meaning |
|---|---|
| `created` | Branch record exists but no branch turn has launched. |
| `preparing` | Workspace, git branch, context bundle, or provider binding is being prepared. |
| `active` | At least one branch turn is running or ready for continuation. |
| `blocked` | Branch cannot continue without approval, missing evidence, or external prerequisite. |
| `failed` | Latest branch turn failed and no automatic continuation is active. |
| `succeeded` | Latest branch turn completed and passed required gates. |
| `promotable` | Branch has an accepted candidate result that can be promoted under policy. |
| `promoted` | Branch result was accepted into canonical workflow progress. |
| `archived` | Branch is hidden from active work but evidence remains inspectable. |
| `superseded` | Another branch was promoted or this branch was replaced by a child branch. |

Branch Turn states:

| State | Meaning |
|---|---|
| `created` | Turn record and instruction artifact exist. |
| `preparing` | Context bundle and workspace/provider state are being prepared. |
| `running` | Agent/tool/provider continuation is active. |
| `checking` | Verification/gates are active. |
| `succeeded` | Turn completed and gates passed. |
| `failed` | Turn failed or gates failed. |
| `blocked` | Turn requires approval or missing prerequisite. |
| `canceled` | Turn was canceled. |
| `superseded` | Later turn or child branch replaced this candidate. |

Checkpoint git-binding publication states remain coarse persistence states:

| State | Meaning |
|---|---|
| `unpublished` | No remote publication has been verified. |
| `preparing` | A publication operation is in progress. |
| `published` | A remote branch or PR head has been verified. |
| `failed` | The latest publication operation failed. |
| `archived` | The binding is retained as historical evidence and no longer active. |

Terminal publication operation results are more specific:

| Result | Meaning |
|---|---|
| `pushed` | A branch was committed or reused, pushed, and remotely verified. |
| `already_published` | The expected remote branch or PR head was already at the authoritative commit. |
| `no_changes` | No commits or workspace changes existed beyond the base, and no output branch needed preservation. |
| `skipped` | Policy, failure class, workspace state, or checkpoint evidence made publication ineligible. |
| `failed` | Publication was eligible and attempted, but commit, scan, push, or verification failed. |

### 6.2 Operations

Canonical product operations:

```text
checkpoint_branch.create
checkpoint_branch.continue
checkpoint_branch.fork
checkpoint_branch.compare
checkpoint_branch.promote
checkpoint_branch.archive
checkpoint_branch.publish
checkpoint_branch.publish_terminal_checkpoint
```

The corresponding runtime activity for live managed workspaces is:

```text
agent_runtime.publish_terminal_checkpoint
```

Each side-effecting operation must be idempotent and audit-backed.

### 6.3 Promotion

Promotion accepts one branch result as canonical workflow progress. Promotion must record:

1. promoted branch id;
2. promoted branch turn id;
3. promoted Step Execution id;
4. accepted output refs;
5. git commit/branch/PR refs when applicable;
6. gate verdict refs;
7. side-effect disposition refs;
8. downstream invalidation or revalidation effects;
9. approval and policy evidence.

Promotion does not delete competing branches.

### 6.4 Terminal checkpoint publication lifecycle

Terminal checkpoint publication follows this lifecycle:

```text
structured terminal failure
  -> classify eligibility
  -> resolve authoritative live workspace or checkpoint
  -> resolve or generate isolated git work branch
  -> detect already-published equivalent head
  -> commit dirty publishable paths if needed
  -> scan outbound commit range
  -> push with lease protection
  -> verify remote branch and head SHA
  -> persist checkpoint binding and publication artifact
  -> project outputBranch and finish-summary evidence
  -> clean up workspace
```

The operation is best-effort with respect to the primary workflow outcome, but it is not fire-and-forget. When the workspace is available, the workflow must deterministically await the publication attempt before cleanup.

---

## 7. Data model

### 7.1 `workflow_checkpoint_branches`

Representative fields:

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

A workflow that was not already executing as an explicit product branch may create a system-owned root Checkpoint Branch for terminal preservation only when a validated checkpoint or typed source state exists. Recommended metadata:

```text
label       = Recovered work from failed workflow
created_by  = system:terminal-checkpoint
branch_kind = root
```

MoonMind must not fabricate a product branch record when no valid source evidence exists.

### 7.2 `workflow_checkpoint_branch_turns`

Representative fields:

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
updated_at
```

### 7.3 `workflow_checkpoint_branch_git_bindings`

Representative fields:

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

`binding_metadata.terminalPublication`, when present, should contain only bounded, non-secret fields:

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
  "evidenceRef": "artifact://..."
}
```

Dedicated columns are not required for the first rollout. The binding, artifact refs, and finish-summary projection are authoritative enough until filtering or retention queries justify additional indexed columns.

### 7.4 `workflow_checkpoint_branch_artifacts`

Representative fields:

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

Step Execution manifests should include optional branch metadata:

```json
{
  "branch": {
    "branchId": "cbr_01J...",
    "branchTurnId": "cbt_01J...",
    "rootCheckpointRef": "artifact://checkpoint-after-execution",
    "parentBranchId": null,
    "parentTurnId": null,
    "gitWorkBranch": "mm/mm-824/implement-story-s004/cp-9f2/minimal-api-fix"
  }
}
```

This does not replace the Step Execution identity tuple. It adds lineage.

### 7.6 Terminal publication evidence

Every attempted terminal publication should produce a bounded artifact, including failed attempts:

```json
{
  "schemaVersion": "v1",
  "contentType": "application/vnd.moonmind.terminal-checkpoint-publication+json;version=1",
  "workflowId": "mm:wf",
  "runId": "run_abc",
  "stepExecutionId": "mm:wf:run_abc:implement:execution:2",
  "branchId": "cbr_01J...",
  "sourceCheckpointRef": "artifact://checkpoint-after-execution",
  "sourceCheckpointDigest": "sha256:...",
  "repository": "owner/repo",
  "baseBranch": "main",
  "branchName": "mm/mm-wf/implement/cp-9f2/recovered-work",
  "headSha": "abc123",
  "intent": "terminal_checkpoint",
  "status": "pushed",
  "reasonCode": "graceful_failure_checkpoint_pushed",
  "failureClass": "execution_error",
  "remoteVerified": true,
  "idempotencyKey": "mm:wf:run_abc:implement:execution:2:terminal-checkpoint:v1",
  "createdAt": "2026-07-12T12:00:00Z"
}
```

Raw logs, diffs, provider payloads, credentials, and full command output do not belong in this artifact. They remain behind diagnostic or patch refs.

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

Representative request:

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
    "text": "Keep the useful changes. Fix only the API contract failure. Add one regression test. Do not create a duplicate PR."
  },
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "runtimeContextPolicy": "fresh_agent_run",
  "publishMode": "none",
  "idempotencyKey": "mm:wf:checkpoint:after_execution:minimal-api-fix"
}
```

### 8.3 Continue branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/continue
```

Representative request:

```json
{
  "label": "Add focused regression coverage",
  "instructions": {
    "text": "Continue this branch. Add tests for the API contract fix. Do not broaden the public interface."
  },
  "workspacePolicy": "continue_from_previous_execution",
  "runtimeContextPolicy": "reuse_session_new_epoch",
  "idempotencyKey": "mm:wf:cbr_01J:turn:add-tests"
}
```

### 8.4 Fork branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/fork
```

Representative request:

```json
{
  "parentTurnId": "cbt_01J...",
  "label": "Alternative: remove adapter abstraction",
  "instructions": {
    "text": "Fork from the checkpoint after Turn 2. Instead of patching the adapter, remove the adapter abstraction and simplify the call path."
  },
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "idempotencyKey": "mm:wf:cbr_01J:fork:remove-adapter-abstraction"
}
```

### 8.5 Compare branches

```http
GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}
```

Comparison produces an artifact-backed branch comparison record, including git range diff refs, gate verdict summaries, diagnostics refs, and a bounded natural-language summary.

### 8.6 Promote branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/promote
```

Representative request:

```json
{
  "expectedHeadStepExecutionId": "mm:wf:run:implement-story-S004:execution:5",
  "expectedHeadCommit": "def456",
  "approvalToken": "approval_...",
  "idempotencyKey": "mm:wf:cbr_01J:promote:def456"
}
```

### 8.7 Archive branch

```http
POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/archive
```

Archive is non-destructive. It hides the branch from active work but keeps records, artifacts, git refs, and provider diagnostics inspectable.

### 8.8 Execution detail output branch

The execution detail contract should expose a first-class optional `outputBranch` field. It is derived from the verified checkpoint git binding when available, with the canonical finish summary as a compatibility fallback.

```json
{
  "outputBranch": {
    "name": "mm/mm-wf/implement/cp-9f2/recovered-work",
    "url": "https://github.com/owner/repo/tree/mm/mm-wf/implement/cp-9f2/recovered-work",
    "headSha": "abc123",
    "baseBranch": "main",
    "intent": "terminal_checkpoint",
    "status": "pushed",
    "evidenceRef": "artifact://..."
  }
}
```

Rules:

1. `outputBranch` is detail-only unless a future list/filter requirement justifies projection indexing.
2. `name` may be returned without `url` when the provider URL cannot be safely validated.
3. `url` must be provider-generated or server-validated; the browser must not construct it from arbitrary metadata.
4. `intent` distinguishes `normal_publish` from `terminal_checkpoint`.
5. `prUrl` remains an independent field. A workflow may expose both an output branch and a PR.
6. `startingBranch` and `targetBranch` remain authored-input fields and must not be reused as output evidence.

---

## 9. Workspace and git policy

### 9.1 Branch creation modes

| Mode | Meaning |
|---|---|
| `from_checkpoint_worktree` | Restore a durable worktree archive or live workspace ref into a new worktree/git branch. |
| `from_checkpoint_patch` | Start from `baseCommit`, apply checkpoint patch, then create a new git work branch. |
| `from_last_accepted_commit` | Start from latest accepted commit or published ref. |
| `fresh_from_source_branch` | Create a clean git work branch from the repository source branch. |
| `external_provider_state` | Use provider state only when adapter-specific validation allows it. |

### 9.2 Workspace policies

Branch turns reuse the Step Execution workspace policies:

| Policy | Branch behavior |
|---|---|
| `continue_from_previous_execution` | Keep the branch head workspace and continue with a new turn. |
| `restore_pre_execution` | Restore the workspace from the checkpoint before executing new branch work. |
| `apply_previous_execution_diff_to_clean_baseline` | Reset to clean baseline, apply prior diff, then execute branch work. |
| `start_from_last_passed_commit` | Start from latest accepted commit or published ref. |
| `fresh_branch_from_source` | Start a new work branch from the source repository branch. |

The selected policy must be recorded in branch metadata, branch turn metadata, Step Execution manifest, and diagnostics.

### 9.3 Git branch naming

Default generated branch name:

```text
mm/{workflow-slug}/{logical-step-slug}/{checkpoint-short}/{branch-short}-{label-slug}
```

Example:

```text
mm/mm-824/implement-story-s004/cp-a1b2c3/cbr-9f2-minimal-api-fix
```

Rules:

1. branch names must be deterministic under an idempotency key;
2. branch names must be sanitized;
3. protected branch names such as `main`, `master`, `HEAD`, empty strings, detached heads, and unknown refs must never be used as work branches;
4. branch name collisions are allowed only when branch metadata proves the existing ref belongs to the same Checkpoint Branch or terminal publication operation;
5. otherwise collisions fail closed.

Terminal publication should reuse an existing safe output branch when that branch is bound to the same workflow/Step Execution and expected head. Otherwise it uses the deterministic checkpoint branch naming helper rather than introducing a second naming convention.

### 9.4 Publish vs promote

Publication and promotion are separate.

```text
publication = push git branch or create/update PR
promotion   = accept branch result into canonical workflow progress
```

A branch may be:

1. unpublished and unpromoted;
2. published but unpromoted;
3. promoted but not yet published;
4. both published and promoted;
5. published as recovery evidence from a failed workflow and not promotable yet.

### 9.5 Terminal checkpoint publication on controlled failure

#### 9.5.1 Eligibility

Terminal checkpoint publication is eligible only when all of the following are true:

1. the workflow or AgentRun reached a structured terminal decision;
2. the failure is not an unhandled infrastructure failure;
3. repository mutation was permitted for the run;
4. MoonMind has a live authoritative workspace or validated checkpoint/git binding;
5. the branch can be isolated from protected refs;
6. remote credentials and outbound policy permit a push.

Initial classification:

| Terminal condition | Attempt publication? |
|---|---|
| Structured `user_error` | Yes, when authoritative repository state exists. |
| Structured `execution_error` | Yes. |
| Structured `integration_error` | Yes when MoonMind still owns a workspace; otherwise adopt verified provider branch evidence when available. |
| Caught AgentRun `timed_out` result | Yes when the workspace or checkpoint remains reachable. |
| Review, validation, or gate failure after repository work | Yes, using the latest authoritative checkpoint if needed. |
| `system_error` or unhandled exception | No new publication attempt. Adopt only independently verified existing remote evidence. |
| Temporal workflow timeout, worker death, host loss, or inaccessible workspace | No new publication attempt. |
| User cancellation | Excluded from the first rollout. |
| Read-only workflow, dry run, or explicit `noRemoteWrites` policy | No. |
| No commit or diff beyond the base and no pre-existing output branch | Return `no_changes`. |

A caught execution budget exhaustion represented as an AgentRun result is different from a Temporal workflow that disappears before finalization. Only the former is a controlled terminal failure.

#### 9.5.2 Live managed-workspace flow

```text
AgentRun returns structured failure
  -> defer workspace cleanup
  -> inspect existing output branch and remote head
  -> switch away from a protected current branch if necessary
  -> stage only publishable tracked and untracked paths
  -> create one deterministic commit when dirty
  -> scan the outbound range
  -> push with force-with-lease semantics
  -> verify remote ref and expected head SHA
  -> merge publication evidence into AgentRunResult
  -> publish result artifacts
  -> clean up managed workspace
```

The commit message should be deterministic and bounded, for example:

```text
MoonMind terminal checkpoint for workflow {workflowId} run {runId}
```

Existing workflow-provided commit text may be used only when it is bounded and safe.

#### 9.5.3 Checkpoint-restoration fallback

The parent workflow needs a fallback because a controlled failure may occur after the child AgentRun has completed, such as during review, verification, PR creation, or a publication gate.

```text
Parent workflow reaches controlled failure
  -> check child result for verified branch or PR evidence
  -> adopt existing evidence when the head matches
  -> otherwise select latest authoritative checkpoint
  -> validate checkpoint identity, digest, base, and Step Execution lineage
  -> restore into an isolated worktree
  -> invoke the same terminal publication operation
  -> persist checkpoint branch binding and evidence
  -> finalize failed workflow
```

If no authoritative checkpoint can be restored, return `skipped` with `checkpoint_unavailable`. Do not construct a branch from logs or an ambiguous filesystem snapshot.

#### 9.5.4 Existing branch and PR adoption

Before committing or pushing, MoonMind must check for equivalent existing publication evidence:

1. a bound work branch at the authoritative local head;
2. a remote branch at the expected head SHA;
3. an existing PR whose head branch and SHA match;
4. provider-native branch or PR evidence that has been independently verified.

When equivalent evidence exists, return `already_published`. Do not create another commit, branch, or PR.

#### 9.5.5 Idempotency and concurrency

The publication idempotency key should include:

```text
{workflowId}:{runId}:{stepExecutionId-or-checkpointDigest}:terminal-checkpoint:v1
```

The operation must:

- create at most one deterministic commit for the same dirty state;
- reuse the deterministic branch name;
- resolve the recorded remote SHA before push;
- use lease-protected push semantics;
- treat a remote head already equal to the local head as success;
- fail closed on an unexpected branch owner or unresolved lease conflict;
- write one durable operation/evidence record that is safe to replay.

#### 9.5.6 Publication result contract

Representative operation result:

```json
{
  "intent": "terminal_checkpoint",
  "status": "pushed",
  "reasonCode": "graceful_failure_checkpoint_pushed",
  "attempted": true,
  "commitCreated": true,
  "branchPushed": true,
  "branchName": "mm/mm-wf/implement/cp-9f2/recovered-work",
  "branchUrl": "https://github.com/owner/repo/tree/mm/mm-wf/implement/cp-9f2/recovered-work",
  "headSha": "abc123",
  "baseBranch": "main",
  "remoteVerified": true,
  "evidenceRef": "artifact://..."
}
```

Recommended reason codes include:

```text
graceful_failure_checkpoint_pushed
already_published
no_changes
policy_disabled
read_only
checkpoint_unavailable
workspace_unavailable
system_failure_ineligible
scan_rejected
protected_branch
lease_conflict
authentication_failed
remote_verification_failed
```

#### 9.5.7 Finish-summary semantics

A successfully saved branch does not change the finish outcome:

```json
{
  "finishOutcome": {
    "code": "FAILED",
    "stage": "review",
    "reason": "Validation failed."
  },
  "publish": {
    "mode": "branch",
    "intent": "terminal_checkpoint",
    "status": "pushed",
    "reasonCode": "graceful_failure_checkpoint_pushed",
    "commitCreated": true,
    "branchPushed": true,
    "branchName": "mm/mm-wf/implement/cp-9f2/recovered-work",
    "branchUrl": "https://github.com/owner/repo/tree/mm/mm-wf/implement/cp-9f2/recovered-work",
    "headSha": "abc123",
    "baseBranch": "main",
    "prUrl": null,
    "evidenceRef": "artifact://..."
  }
}
```

The primary failure diagnostic remains canonical. A secondary publication error may be included under `publish.reason`, `publish.reasonCode`, or a bounded diagnostic ref, but it must not overwrite `finishOutcome.reason` or the first-failure-wins diagnostic.

#### 9.5.8 External providers

External providers that already own repository state should return branch or PR evidence through their canonical AgentRun result. MoonMind should adopt and verify that evidence when possible.

MoonMind must not synthesize a local recovery branch for an external provider unless MoonMind also has a validated local checkpoint or provider workspace binding sufficient to reproduce the authoritative state.

---

## 10. Runtime/session policy

Branch turns must declare a runtime context policy before launch.

| Policy | Meaning |
|---|---|
| `fresh_agent_run` | Start a new MoonMind AgentRun child workflow for the branch turn. |
| `reuse_session_new_epoch` | Reuse a workflow-scoped managed session container but clear/reset to a new epoch before the branch turn. |
| `reuse_session_same_epoch` | Keep session continuity across branch turns; rare and explicit. |
| `external_provider_continuation` | Delegate continuation to provider-specific semantics when MoonMind cannot directly control runtime state. |

Recommended defaults:

| Operation | Default runtime policy |
|---|---|
| Create new branch from checkpoint | `fresh_agent_run` |
| Continue same branch | `reuse_session_new_epoch` or `fresh_agent_run` |
| Fork from earlier turn | `fresh_agent_run` |
| Same runtime conversation | `reuse_session_same_epoch`, approval/capability gated |
| Omnigent v1 | `fresh_agent_run` with new Omnigent session |
| Omnigent v2 same-session message | `external_provider_continuation` |
| Terminal publication from live managed workspace | No new agent turn; use an agent-runtime publication activity. |
| Terminal publication from checkpoint | Restore an isolated workspace without resuming agent reasoning. |

Terminal checkpoint publication is finalization work, not a branch turn. It does not create another model/provider request unless a later operator explicitly continues the saved branch.

---

## 11. Context bundle

A branch turn receives an immutable, digest-addressed context bundle.

Representative shape:

```json
{
  "schemaVersion": "v1",
  "workflowId": "mm:wf",
  "runId": "run_new",
  "logicalStepId": "implement-story-S004",
  "executionOrdinal": 5,
  "reason": "checkpoint_branch",
  "branch": {
    "branchId": "cbr_01J...",
    "branchTurnId": "cbt_01J...",
    "label": "Try minimal API contract fix",
    "sourceCheckpointRef": "artifact://checkpoint-after-execution",
    "parentBranchId": null,
    "parentTurnId": null,
    "gitWorkBranch": "mm/mm-824/implement-story-s004/cp-a1b2c3/cbr-9f2-minimal-api-fix"
  },
  "taskInputSnapshotRef": "artifact://original-task-input",
  "planRef": "artifact://plan",
  "planDigest": "sha256:...",
  "instructionRefs": [
    "artifact://branch-initial-instructions",
    "artifact://turn-instructions"
  ],
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "workspaceBaseline": {
    "kind": "git_patch",
    "baseCommit": "abc123",
    "patchRef": "artifact://execution-2-patch"
  },
  "priorEvidenceRefs": [
    "artifact://failed-attempt-manifest",
    "artifact://failed-attempt-gate-report",
    "artifact://failed-attempt-diagnostics"
  ],
  "branchComparisonRefs": [],
  "builderMetadata": {
    "version": "v1",
    "digest": "sha256:..."
  }
}
```

Rules:

1. include refs and bounded summaries only;
2. never inline raw logs, raw diffs, provider payloads, or credentials;
3. include workspace policy and baseline;
4. include source checkpoint identity;
5. include branch lineage;
6. include instruction refs and digests;
7. include builder version and digest in the artifact metadata.

Terminal publication does not need a full branch-turn context bundle. It uses the terminal publication evidence artifact plus the existing Step Execution manifest, checkpoint, and git binding.

---

## 12. Omnigent integration

### 12.1 v1 branch mode: fresh Omnigent session from checkpoint

Omnigent v1 uses the streaming-gateway activity shape. It returns a terminal `AgentRunResult` and does not expose non-terminal provider states to MoonMind as branchable Temporal state. Therefore the safe v1 Checkpoint Branch mode is:

```text
fresh_omnigent_session_from_checkpoint
```

Flow:

1. validate the source checkpoint;
2. restore or synthesize an isolated git work branch/workspace from MoonMind evidence;
3. create a new Omnigent session;
4. pass branch-turn instructions through `parameters.omnigent.prompt.instructionRef`;
5. include prior Omnigent capture refs as evidence refs;
6. capture the new Omnigent session output into MoonMind artifacts;
7. bind the new Omnigent result to the branch turn evidence.

The branch must use a new Omnigent idempotency key:

```text
{workflowId}:{branchId}:{branchTurnId}:omnigent
```

It must not reuse the parent Omnigent attempt's idempotency key because branch-turn instructions produce a different first-message digest.

### 12.2 v2 branch mode: provider continuation

Same-session continuation should be enabled only when Omnigent exposes typed lifecycle activities such as:

```text
integration.omnigent.send_message
integration.omnigent.harvest_session
```

In that mode, a branch turn may use:

```json
{
  "runtimeContextPolicy": "external_provider_continuation",
  "omnigentContinuation": {
    "sourceSessionId": "conv_parent",
    "continuationMode": "send_message",
    "instructionRef": "artifact://turn-instructions",
    "harvestAfterTurn": true
  }
}
```

Even in v2, Omnigent session ids remain runtime bindings and diagnostics metadata. They are not product branch identity.

### 12.3 Omnigent terminal publication

If Omnigent returns a verified provider-native branch or PR, MoonMind may adopt it as `outputBranch`. If it returns only artifacts or a provider session id, MoonMind may not claim a Saved Work Branch unless a validated MoonMind checkpoint or provider workspace binding can reproduce and verify the repository head.

---

## 13. Branch comparison

Branch comparison should produce durable artifacts.

Representative comparison artifact:

```json
{
  "schemaVersion": "v1",
  "leftBranchId": "cbr_A",
  "rightBranchId": "cbr_B",
  "baseCheckpointRef": "artifact://checkpoint-after-execution",
  "git": {
    "leftDiffRef": "artifact://branch-a-diff",
    "rightDiffRef": "artifact://branch-b-diff",
    "rangeDiffRef": "artifact://range-diff"
  },
  "quality": {
    "leftGateVerdict": "FULLY_IMPLEMENTED",
    "rightGateVerdict": "ADDITIONAL_WORK_NEEDED"
  },
  "summaryRef": "artifact://branch-comparison-summary"
}
```

Comparison must be ref-backed and bounded. Large diffs and diagnostics stay behind artifact refs.

A saved-work branch is a valid comparison input. Its failed source workflow does not imply that the branch itself is unusable; the comparison surface should show its source failure and verification state separately.

---

## 14. Security and safety

Checkpoint Branches inherit MoonMind's normal artifact, secrets, runtime, and repository policies.

Rules:

1. no raw secrets in branch records, branch turns, context bundles, diagnostics, instruction artifacts, or terminal publication evidence;
2. artifact refs are identifiers, not direct storage access grants;
3. checkpoint validation must occur before workspace restoration or runtime launch;
4. branch workspaces must not write outside approved worktrees;
5. branch git operations must never push protected branches;
6. provider continuation must be capability-gated;
7. non-idempotent external side effects require isolation, compensation, or approval;
8. promotion requires fresh branch-head validation;
9. branch archival must not delete audit evidence;
10. branch comparison must not inline large or sensitive evidence;
11. terminal publication must run the same outbound secret/content scan as normal publication;
12. terminal publication must use a non-interactive credential path and must not expose tokens in command output or artifacts;
13. a push must use lease protection or an equivalent provider concurrency guard;
14. Saved Work Branch UI and API fields require remote head verification;
15. failure to publish must not hide or mutate the primary failure diagnostic.

Fail-closed cases:

```text
checkpoint_missing
checkpoint_invalid
checkpoint_unauthorized
checkpoint_digest_mismatch
plan_mismatch
workspace_policy_incompatible
git_base_commit_mismatch
git_branch_collision
protected_branch_ref
side_effect_policy_blocked
provider_continuation_unsupported
approval_required
budget_exhausted
outbound_scan_rejected
remote_lease_conflict
remote_verification_failed
```

A scan rejection or lease conflict is an eligible publication operation that failed, not a reason to convert the original workflow failure into a publication failure.

---

## 15. UI requirements

### 15.1 Branch Explorer

The workflow detail page should include a Branch Explorer:

```text
Step 2 / checkpoint after_execution / cp-a1b2c3
  ├─ Branch A: Try minimal API contract fix
  │   ├─ Turn 1: failed tests
  │   └─ Turn 2: passed gate, promotable
  ├─ Branch B: Rewrite client module
  │   └─ Turn 1: too broad, archived
  └─ Branch C: Continue Omnigent state
      └─ Turn 1: provider session unavailable, failed closed
```

### 15.2 Default detail view

The default workflow detail view should remain simple. It should show the mainline by default, with branch count and branch status affordances near checkpoints and failed/blocked steps.

### 15.3 Branch actions

The UI should support:

```text
Create branch from checkpoint
Continue branch
Fork from this turn
Compare branches
Promote branch
Publish branch
Archive branch
View branch evidence
View git diff
View provider diagnostics
```

### 15.4 Safety previews

Before branch creation, show:

```text
Source checkpoint
Workspace policy
Git work branch name
Runtime/session policy
Publish mode
Side-effect risk
Budget impact
Approval requirements
```

Before promotion, show:

```text
Branch head
Gate verdict
Git commit / PR
Downstream invalidations
Side-effect classification
Approval requirements
Competing branches that will remain active or become superseded
```

### 15.5 Saved Work Branch on workflow detail

The workflow detail **Git & Publish** fact group should render `outputBranch` immediately before the PR link.

Label rules:

- `intent=terminal_checkpoint` -> **Saved Work Branch**
- `intent=normal_publish` -> **Published Branch**

Display rules:

1. show the branch name as code;
2. link the name only when the server supplied a validated provider URL;
3. show the field for failed workflows without changing failed status styling;
4. show PR Link independently when a PR also exists;
5. do not substitute Starting Branch or Target Branch for missing output evidence;
6. expose the evidence ref in the Run Summary or evidence surface, not as noisy default copy;
7. when terminal publication failed, keep the original failure prominent and show the preservation failure as secondary diagnostic text.

The Run Summary should use the same canonical output-branch object so that the overview does not show a contradictory duplicate branch.

---

## 16. Artifact requirements

Minimum artifacts per branch:

```text
input.branch.root_checkpoint.json
input.branch.initial_instructions.md
runtime.branch.context_bundle.json
runtime.branch.workspace_restore.json
runtime.branch.git_binding.json
output.branch.summary.json
output.branch.latest_head.json
```

Minimum artifacts per branch turn:

```text
input.branch_turn.instructions.md
runtime.branch_turn.context_bundle.json
runtime.branch_turn.agent_request.json
runtime.branch_turn.agent_result.json
output.branch_turn.step_execution_manifest.json
output.branch_turn.checkpoint.json
output.branch_turn.diagnostics.json
```

Minimum artifacts per promotion:

```text
output.branch_promotion.record.json
output.branch_promotion.downstream_invalidation.json
```

Minimum artifacts per comparison:

```text
output.branch_comparison.summary.json
output.branch_comparison.range_diff.patch
output.branch_comparison.metadata.json
```

Minimum artifact per attempted terminal publication:

```text
output.branch.terminal_publication.json
```

The terminal publication artifact is produced for `pushed`, `already_published`, `no_changes`, `skipped`, and `failed` results when the workflow reached the publication finalizer. When a sudden infrastructure loss prevents finalization entirely, the absence of this artifact is expected and must not be backfilled from assumptions.

Retention rules:

1. terminal publication evidence follows checkpoint evidence retention;
2. a saved-work remote branch must not be automatically deleted merely because the source workflow failed;
3. archival may hide the branch from active UI while preserving binding and publication evidence;
4. remote branch deletion, if supported later, requires explicit retention policy and audit evidence.

---

## 17. Migration and rollout

### 17.1 Phase 1 — Branch graph and fresh runtime turns

Deliver:

1. branch and branch turn schemas;
2. branch create/continue/fork/archive APIs;
3. instruction artifact storage;
4. git work branch binding;
5. fresh runtime turn execution;
6. branch list/detail UI;
7. Omnigent v1 fresh-session branch support.

Out of scope:

1. same-session provider continuation;
2. rich branch compare UI;
3. auto branch exploration policies;
4. multi-branch merge.

### 17.2 Phase 2 — Compare and promote

Deliver:

1. branch comparison artifacts;
2. compare UI;
3. promotion API;
4. downstream invalidation;
5. promotion audit artifacts;
6. branch publish/PR controls.

### 17.3 Phase 3 — Provider continuation

Deliver provider-gated continuation for adapters that support it.

For Omnigent, this requires typed lifecycle activities such as:

```text
integration.omnigent.send_message
integration.omnigent.harvest_session
```

### 17.4 Phase 4 — Policy-driven branch exploration

Allow workflow templates or presets to request bounded automated branch exploration:

```yaml
checkpointBranching:
  enabled: true
  triggers:
    - gate_additional_work_needed
    - failed_step
    - operator_requested
  maxBranchesPerCheckpoint: 3
  maxTurnsPerBranch: 4
  promotionPolicy: approval_gated
  defaultWorkspacePolicy: apply_previous_execution_diff_to_clean_baseline
  branchTemplates:
    - label: minimal_fix
      instructionsRef: artifact://template-minimal-fix
    - label: alternative_design
      instructionsRef: artifact://template-alternative-design
```

### 17.5 Terminal checkpoint publication rollout

Terminal checkpoint publication should ship in four reviewable slices:

#### Slice A — Contracts and design

1. add the policy and typed result contract;
2. add `outputBranch` to execution detail;
3. extend finish-summary publication semantics;
4. define the Temporal patch marker and idempotency key;
5. align this document, workflow finish summary, publishing, API, and UI contracts.

#### Slice B — Live managed workspaces

1. defer cleanup for newly started compatible AgentRun histories;
2. add `agent_runtime.publish_terminal_checkpoint`;
3. reuse existing commit, scan, push, and remote verification machinery;
4. integrate controlled AgentRun failures and caught timeouts;
5. retain legacy cleanup ordering for replaying histories through a Temporal patch marker.

#### Slice C — Parent/checkpoint fallback

1. detect parent-level review, gate, and PR-creation failures;
2. adopt child publication evidence when already verified;
3. restore the latest authoritative checkpoint when the live workspace is gone;
4. persist system-owned checkpoint branch bindings and evidence;
5. fail closed when the checkpoint is unavailable or ambiguous.

#### Slice D — API, UI, and operations

1. project `outputBranch`;
2. render Saved Work Branch next to PR Link;
3. add metrics and audit events;
4. add branch retention and archival handling;
5. complete runtime, replay, API, and frontend coverage.

Recommended metrics:

```text
terminal_checkpoint_publication_eligible
terminal_checkpoint_publication_attempted
terminal_checkpoint_publication_pushed
terminal_checkpoint_publication_already_published
terminal_checkpoint_publication_no_changes
terminal_checkpoint_publication_skipped
terminal_checkpoint_publication_failed
```

Bounded dimensions may include failure class, runtime family, source (`live_workspace`, `checkpoint_restore`, or `provider_native`), and reason code. Do not include repository URLs, branch names, errors, or other high-cardinality values as metric labels.

---

## 18. Testing requirements

### 18.1 Schema tests

1. Branch requires source checkpoint ref or typed source state.
2. Branch turn requires instruction ref and digest.
3. Branch turn cannot mutate after launch.
4. Product branch id and git branch name remain distinct.
5. Raw logs, diffs, provider payloads, and secrets are rejected from compact branch state.
6. `outputBranch` accepts bounded verified metadata and rejects unsafe URLs.
7. Terminal publication evidence forbids raw credentials and command output.

### 18.2 Checkpoint validation tests

1. Valid checkpoint enables branch creation.
2. Missing checkpoint blocks branch creation.
3. Corrupted checkpoint blocks branch creation.
4. Plan mismatch blocks branch creation.
5. Workspace policy mismatch blocks branch creation.
6. Unauthorized artifact blocks branch creation.
7. Terminal fallback selects by Step Execution identity and digest, not timestamp alone.
8. Ambiguous checkpoint state produces `skipped/checkpoint_unavailable`.

### 18.3 Git isolation tests

1. Branch worktree starts from expected base commit.
2. Generated branch name is sanitized.
3. Protected branch push is refused.
4. Existing branch with matching metadata is reused idempotently.
5. Existing branch with mismatched metadata fails closed.
6. Fork from earlier turn creates a distinct git branch.
7. Terminal publication switches away from protected current branches.
8. Lease conflicts never overwrite another writer.
9. Remote verification is required before returning a branch URL.

### 18.4 Runtime tests

1. New branch creates new Step Execution.
2. Continue branch creates another branch turn and Step Execution.
3. Fork creates child branch with correct parent lineage.
4. Runtime idempotency key includes branch turn identity.
5. Branch failure preserves artifacts and allows follow-up turn.
6. Controlled AgentRun failure publishes before cleanup.
7. Caught AgentRun timeout publishes when the workspace remains reachable.
8. `system_error` and unhandled exceptions do not start a new publication attempt.
9. Existing remote head returns `already_published`.
10. Dirty workspace creates one deterministic commit.
11. Clean worktree with commits ahead of base pushes without another commit.
12. No diff beyond base returns `no_changes`.
13. Publication failure preserves the primary AgentRun failure.
14. Retried activity creates no duplicate commit or branch.

### 18.5 Promotion tests

1. Promotion requires matching expected branch head.
2. Promotion requires passed gates.
3. Promotion records accepted output and invalidations.
4. Promotion does not delete competing branches.
5. Promotion requires approval when policy says so.
6. A saved-work branch is not automatically promotable.

### 18.6 Omnigent tests

1. Omnigent v1 branch turn creates a fresh Omnigent session.
2. Omnigent v1 branch turn uses `parameters.omnigent.prompt.instructionRef`.
3. Omnigent prior session refs are evidence, not branch identity.
4. Omnigent idempotency key differs from source attempt.
5. Same-session continuation is rejected unless adapter capability is enabled.
6. Omnigent capture artifacts bind to branch turn evidence.
7. Provider-native branch evidence is adopted only after verification.
8. Session-only evidence does not produce `outputBranch`.

### 18.7 Workflow, replay, API, and UI tests

1. Parent review or gate failure publishes from the latest authoritative checkpoint.
2. Child already published branch prevents parent duplication.
3. PR creation failure preserves the already-pushed branch.
4. Finish summary, terminal-state activity, checkpoint binding, and execution detail agree.
5. Failed workflow remains failed after successful branch preservation.
6. Old workflow histories replay with legacy cleanup ordering.
7. New workflow histories execute patch-gated deferred cleanup.
8. Execution detail returns Saved Work Branch for a failed workflow.
9. Branch-only, PR-only, and branch-plus-PR states render correctly.
10. Branch name renders without a link when the URL is unavailable.
11. Invalid or unverified branch URL is omitted.
12. Slash-containing branch names render safely.
13. Run Summary and Git & Publish surfaces use the same output branch.
14. Sudden infrastructure failure never falsely claims saved work.

---

## 19. Open questions

1. Should branch creation always create a linked follow-up Workflow Execution, or can it run inside the same logical Workflow Execution as a child branch lane?
2. Should branch promotion update the existing workflow mainline through Continue-As-New, through a workflow update, or through a linked accepted-branch relation?
3. Should branch ids be globally unique ULIDs or deterministic from source checkpoint plus idempotency key?
4. Should archived branch git branches be deleted, retained, or left to repository retention policy?
5. Should branch comparison be synchronous for small diffs and asynchronous for large diffs?
6. How should UI display many branches from one checkpoint without overwhelming the main workflow detail page?
7. Which workflows may permit auto-generated branch exploration without user approval?
8. Should branch promotion support accepted no-code diagnosis as a first-class accepted output?
9. After the initial rollout, should explicit graceful user cancellation also attempt terminal checkpoint publication?
10. Should successful normal publication always populate `outputBranch`, or only branch-only and failure-preservation outcomes?

The initial terminal-publication decisions are not open:

- controlled failures are eligible when authoritative repository state exists;
- sudden infrastructure failures are ineligible unless remote evidence was already verified;
- user cancellation is excluded initially;
- the workflow remains failed after successful preservation;
- publication errors never replace the primary failure;
- Saved Work Branch requires remote verification.

---

## 20. Desired end state

The desired end state is:

```text
Workflow
  -> Step
      -> Checkpoint
          -> Checkpoint Branch
              -> Branch Turn
                  -> Step Execution
                  -> Git work branch / provider runtime binding
                  -> Artifacts and diagnostics
              -> Branch Turn
                  -> Step Execution
          -> Checkpoint Branch
              -> Branch Turn
                  -> Step Execution
```

Users can safely say:

```text
Continue from this checkpoint with these instructions.
Create another branch from the same checkpoint with a different strategy.
Fork from before that bad turn.
Compare branch A and branch B.
Promote branch A.
Archive branch B.
Publish branch C as a PR but do not promote it yet.
```

When a repository-mutating workflow fails in a controlled way, operators can also rely on this behavior:

```text
The workflow remains failed.
MoonMind attempts to preserve authoritative in-flight work before cleanup.
A remotely verified branch is shown as Saved Work Branch.
The branch can be inspected, continued, compared, archived, or promoted later.
A sudden infrastructure failure never falsely claims that work was saved.
```

MoonMind remains responsible for checkpoint validation, artifact authority, Step Execution identity, workspace and git isolation, provider/runtime boundaries, gates, side-effect classification, publication safety, first-failure-wins diagnostics, and explicit promotion into canonical workflow progress.

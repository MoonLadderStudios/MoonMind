# Checkpoint Branch System

Status: Proposed design  
Owners: MoonMind Platform + Workflow Runtime + Dashboard  
Last updated: 2026-07-01

**Implementation tracking:** rollout notes, spikes, temporary handoffs, and migration checklists should live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

- `docs/Steps/StepExecutionsAndCheckpointing.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`
- `docs/Workflows/WorkflowRemediation.md`
- `docs/Workflows/WorkflowPublishing.md`
- `docs/Workflows/WorkflowRunsApi.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Omnigent/OmnigentAdapter.md`
- `docs/ManagedAgents/ManagedAgentsGit.md`

---

## 1. Purpose

This document defines the desired-state **Checkpoint Branch System** for MoonMind.

Checkpoint Branches let an operator, workflow, or policy create one or more independent continuations from a durable workflow or step checkpoint. Each continuation branch may use different instructions, workspace policy, runtime policy, model/provider settings, and publish strategy. The mental model is similar to branching a ChatGPT conversation, but each branch is backed by MoonMind checkpoints, Step Execution evidence, artifact refs, and, when repository work is involved, an isolated git branch or worktree.

The system is not limited to failure recovery or remediation. Remediation is one consumer of the primitive. The broader primitive is:

> From any eligible checkpoint, create a named continuation branch, execute one or more branch turns with branch-local instructions, compare competing branches, and explicitly promote zero or one branch back into the canonical workflow line.

Core goals:

1. allow multiple safe continuations from the same checkpoint;
2. preserve every branch as durable, inspectable evidence;
3. isolate code changes with git branches or worktrees;
4. keep branch instructions immutable and auditable;
5. allow repeated branch turns without losing provenance;
6. support branch comparison and explicit promotion;
7. keep runtime/provider continuation semantics behind typed policies;
8. prevent branch exploration from silently advancing, publishing, or mutating canonical workflow state.

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

---

## 3. Conceptual model

### 3.1 ChatGPT-style branching mapped to MoonMind

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
- archived after comparison;
- forked into child branches.

The UI may visually emphasize a current mainline, but historical branches remain inspectable.

### 3.3 Branch fan-out and fan-in

Branch fan-out means creating several branches from one checkpoint. Branch fan-in means explicitly promoting one branch's result back to the canonical workflow line.

MoonMind should not automatically merge multiple competing branch results. Combining two branch outputs requires a new explicit branch or Step Execution that names both branches as input evidence.

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

Terms that must remain distinct:

```text
retry                 = same Step Execution, transient/idempotent low-level retry
step re-execution     = new Step Execution for the same logical step
recover failed step   = linked recovery flow from failed step checkpoint
checkpoint branch     = named continuation lane from a checkpoint
branch turn           = one instruction-bearing execution on a branch
promotion             = accept a branch result into canonical workflow progress
publication           = push git branch or create/update PR
```

---

## 5. Core invariants

1. **Checkpoint Branches are explicit.** A branch is never inferred solely from logs, git branch names, provider sessions, or dashboard projections.
2. **Every branch has a root checkpoint.** A branch cannot exist without a source checkpoint ref or a typed source state ref that can be validated.
3. **Branch source identity is pinned.** The root source must include workflow id, run id, logical step id when applicable, source execution ordinal when applicable, checkpoint boundary, checkpoint ref, and checkpoint digest when available.
4. **Branch turns are immutable.** A turn's instruction artifact, digest, source checkpoint, workspace policy, and runtime policy cannot be edited after launch. Corrections create a new turn or child branch.
5. **Branch work creates Step Execution evidence.** Any branch turn that executes agent/tool work must create or reference a Step Execution manifest.
6. **Branch evidence is append-only.** Failed, archived, superseded, or unpromoted branch evidence remains durable.
7. **Product branches and git branches are separate identities.** A git branch is a binding of the product branch, not a replacement for it.
8. **Repository-mutating branches require isolation.** Code-changing branches must use a distinct git branch, worktree, or provider workspace binding.
9. **Provider sessions are runtime bindings.** Provider state may support continuation, but MoonMind-owned records and artifacts remain authoritative.
10. **Promotion is explicit.** A branch never becomes canonical merely because it completed, passed tests, pushed a branch, or opened a PR.
11. **Publication is separate from promotion.** A branch may be published as a PR without becoming canonical workflow progress.
12. **Side effects are branch-scoped until promoted.** External effects must be idempotent, isolated, compensated, or gated before branch execution and promotion.
13. **Branch comparison is evidence-backed.** Comparisons must produce artifacts and should not be reconstructed from UI projections.
14. **No silent fallback.** If checkpoint validation, workspace restoration, or provider continuation fails, MoonMind must fail closed or request a safer branch mode.

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

### 6.2 Operations

Canonical operations:

```text
checkpoint_branch.create
checkpoint_branch.continue
checkpoint_branch.fork
checkpoint_branch.compare
checkpoint_branch.promote
checkpoint_branch.archive
checkpoint_branch.publish
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
promoted_at null
archived_at null
created_by
created_at
updated_at
```

### 7.2 `workflow_checkpoint_branch_turns`

Representative fields:

```text
branch_turn_id primary key
branch_id
parent_turn_id null
source_checkpoint_ref
source_checkpoint_digest null
instruction_ref
instruction_digest
context_bundle_ref null
created_step_execution_id null
runtime_agent_run_id null
provider_session_id null
idempotency_key unique
status
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
head_commit null
patch_ref null
pull_request_url null
publish_status
created_at
updated_at
```

### 7.4 `workflow_checkpoint_branch_artifacts`

Representative fields:

```text
branch_id
branch_turn_id null
artifact_ref
artifact_kind
created_at
```

### 7.5 Step Execution manifest extension

Step Execution manifests should include optional branch metadata:

```json
{
  "branch": {
    "branchId": "cbr_01J...",
    "branchTurnId": "cbt_01J...",
    "rootCheckpointRef": "art_checkpoint_after_execution",
    "parentBranchId": null,
    "parentTurnId": null,
    "gitWorkBranch": "mm/mm-824/implement-story-s004/cbr-9f2-minimal-api-fix"
  }
}
```

This does not replace the Step Execution identity tuple. It adds lineage.

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
    "checkpointRef": "art_checkpoint_after_execution"
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
4. branch name collisions are allowed only when branch metadata proves the existing ref belongs to the same Checkpoint Branch;
5. otherwise collisions fail closed.

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
4. both published and promoted.

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
    "sourceCheckpointRef": "art_checkpoint_after_execution",
    "parentBranchId": null,
    "parentTurnId": null,
    "gitWorkBranch": "mm/mm-824/implement-story-s004/cp-a1b2c3/cbr-9f2-minimal-api-fix"
  },
  "taskInputSnapshotRef": "art_original_task_input",
  "planRef": "art_plan",
  "planDigest": "sha256:...",
  "instructionRefs": [
    "art_branch_initial_instructions",
    "art_turn_instructions"
  ],
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "workspaceBaseline": {
    "kind": "git_patch",
    "baseCommit": "abc123",
    "patchRef": "art_execution_2_patch"
  },
  "priorEvidenceRefs": [
    "art_failed_attempt_manifest",
    "art_failed_attempt_gate_report",
    "art_failed_attempt_diagnostics"
  ],
  "branchComparisonRefs": []
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
    "instructionRef": "art_turn_instructions",
    "harvestAfterTurn": true
  }
}
```

Even in v2, Omnigent session ids remain runtime bindings and diagnostics metadata. They are not product branch identity.

---

## 13. Branch comparison

Branch comparison should produce durable artifacts.

Representative comparison artifact:

```json
{
  "schemaVersion": "v1",
  "leftBranchId": "cbr_A",
  "rightBranchId": "cbr_B",
  "baseCheckpointRef": "art_checkpoint_after_execution",
  "git": {
    "leftDiffRef": "art_branch_a_diff",
    "rightDiffRef": "art_branch_b_diff",
    "rangeDiffRef": "art_range_diff"
  },
  "quality": {
    "leftGateVerdict": "FULLY_IMPLEMENTED",
    "rightGateVerdict": "ADDITIONAL_WORK_NEEDED"
  },
  "summaryRef": "art_branch_comparison_summary"
}
```

Comparison must be ref-backed and bounded. Large diffs and diagnostics stay behind artifact refs.

---

## 14. Security and safety

Checkpoint Branches inherit MoonMind's normal artifact, secrets, runtime, and repository policies.

Rules:

1. no raw secrets in branch records, branch turns, context bundles, diagnostics, or instruction artifacts;
2. artifact refs are identifiers, not direct storage access grants;
3. checkpoint validation must occur before workspace restoration or runtime launch;
4. branch workspaces must not write outside approved worktrees;
5. branch git operations must never push protected branches;
6. provider continuation must be capability-gated;
7. non-idempotent external side effects require isolation, compensation, or approval;
8. promotion requires fresh branch-head validation;
9. branch archival must not delete audit evidence;
10. branch comparison must not inline large or sensitive evidence.

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
```

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
      instructionsRef: art_template_minimal_fix
    - label: alternative_design
      instructionsRef: art_template_alternative_design
```

---

## 18. Testing requirements

### 18.1 Schema tests

1. Branch requires source checkpoint ref.
2. Branch turn requires instruction ref and digest.
3. Branch turn cannot mutate after launch.
4. Product branch id and git branch name remain distinct.
5. Raw logs, diffs, provider payloads, and secrets are rejected from compact branch state.

### 18.2 Checkpoint validation tests

1. Valid checkpoint enables branch creation.
2. Missing checkpoint blocks branch creation.
3. Corrupted checkpoint blocks branch creation.
4. Plan mismatch blocks branch creation.
5. Workspace policy mismatch blocks branch creation.
6. Unauthorized artifact blocks branch creation.

### 18.3 Git isolation tests

1. Branch worktree starts from expected base commit.
2. Generated branch name is sanitized.
3. Protected branch push is refused.
4. Existing branch with matching metadata is reused idempotently.
5. Existing branch with mismatched metadata fails closed.
6. Fork from earlier turn creates a distinct git branch.

### 18.4 Runtime tests

1. New branch creates new Step Execution.
2. Continue branch creates another branch turn and Step Execution.
3. Fork creates child branch with correct parent lineage.
4. Runtime idempotency key includes branch turn identity.
5. Branch failure preserves artifacts and allows follow-up turn.

### 18.5 Promotion tests

1. Promotion requires matching expected branch head.
2. Promotion requires passed gates.
3. Promotion records accepted output and invalidations.
4. Promotion does not delete competing branches.
5. Promotion requires approval when policy says so.

### 18.6 Omnigent tests

1. Omnigent v1 branch turn creates a fresh Omnigent session.
2. Omnigent v1 branch turn uses `parameters.omnigent.prompt.instructionRef`.
3. Omnigent prior session refs are evidence, not branch identity.
4. Omnigent idempotency key differs from source attempt.
5. Same-session continuation is rejected unless adapter capability is enabled.
6. Omnigent capture artifacts bind to branch turn evidence.

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

MoonMind remains responsible for checkpoint validation, artifact authority, Step Execution identity, workspace and git isolation, provider/runtime boundaries, gates, side-effect classification, and promotion into canonical workflow progress.

# Step Executions and Checkpointing

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-05-16
Canonical for: semantic step reattempts, checkpointed side-effect policy, gated iteration, failed-step recovery primitive, autonomous story loops
Related: `docs/Steps/StepTypes.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskRemediation.md`, `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/RunHistoryAndRerunSemantics.md`, `docs/Temporal/ActivityCatalogAndWorkerTopology.md`, `docs/Artifacts/ArtifactPresentationContract.md`

---

## 1. Purpose

This document defines the desired-state MoonMind model for repeating work safely.

MoonMind tasks often need more than one pass. An implementation step may fail tests, a verifier may report `ADDITIONAL_WORK_NEEDED`, a pull request resolver may need another repair attempt, an autonomous story loop may need a fresh agent context, or an operator may resume a failed task from the last failed step.

All of those cases depend on the same primitive:

> Re-execute a logical step as a new attempt while explicitly controlling which previous side effects are preserved, restored, ignored, invalidated, promoted, or superseded.

This document defines that primitive as **Step Executions with Checkpointing**.

It is canonical for:

1. semantic re-execution of a logical step;
2. per-attempt identity, lineage, context, artifacts, checks, and disposition;
3. workspace, git, artifact, memory, retrieval, and external side-effect policy;
4. gated iteration loops such as `implement -> verify -> remediate -> verify`;
5. the shared foundation used by failed-step recovery and autonomous PRD/story/quality-gate loops.

This document does **not** redefine:

1. product-facing Step Types;
2. executable Tool contracts;
3. generic artifact storage internals;
4. Temporal Activity retry policy;
5. provider-specific runtime launch internals;
6. full run-history product UI.

Use `docs/Steps/StepTypes.md` for the user-facing step taxonomy. Step Executions are execution-plane records, not authoring steps and not Step Types. Use `docs/Temporal/StepLedgerAndProgressModel.md` for the operator-facing step ledger shape. Use `docs/Tasks/TaskArchitecture.md` and `docs/Temporal/RunHistoryAndRerunSemantics.md` for task create, rerun, and failed-step recovery semantics.

---

## 2. Layering and Source of Truth

A MoonMind plan contains **logical steps**. A logical step may have one or more **Step Executions**.

```text
Logical Step: implement-story-S004
  Attempt 1: failed quality gate
  Attempt 2: failed integration evidence
  Attempt 3: passed and accepted
```

The execution-plane source-of-truth model is layered.

| Layer | Role | Authority |
| --- | --- | --- |
| Resolved plan artifact | Planned logical steps, order, dependencies, titles, tool/skill descriptors | Authoritative planned structure |
| Workflow state | Current/latest compact step and attempt state, active loop state, budgets, child refs | Authoritative live state |
| Step ledger query | Operator-facing current step rows, latest attempt refs, checks, preserved provenance | Read projection of workflow state |
| Step execution manifest artifact | Immutable full attempt evidence: context, outputs, diffs, gates, retrieval, memory proposals, side-effect classification | Authoritative attempt evidence |
| Checkpoint artifact/read model | Durable state needed to restore or validate step boundaries | Authoritative recovery evidence |
| Git/repo state | Accepted code changes, branch state, commits, PR refs | Authoritative accepted code state when committed/published |
| Derived app DB projection | Fast UI/API reads, degraded reads, optional attempt list | Repairable read model only |

Rules:

1. Attempt evidence is append-only.
2. Workflow state carries compact refs and bounded summaries, not large payloads.
3. The step ledger may show only the latest/current attempt by default.
4. Attempt history is available through expanded API/UI surfaces using step execution manifest refs.
5. App DB projections must be downstream of workflow state, artifact linkage, and git state. They must not invent step or attempt truth.

Recommended step execution manifest content type:

```text
application/vnd.moonmind.step-execution+json;version=1
```

Recommended checkpoint content type:

```text
application/vnd.moonmind.step-checkpoint+json;version=1
```

---

## 3. Desired-State Summary

Each Step Execution has its own:

1. attempt identity;
2. optional cross-run lineage identity;
3. reason for execution;
4. prepared input snapshot;
5. immutable context bundle;
6. workspace and git baseline;
7. agent child workflow or tool activity refs;
8. artifact refs;
9. quality checks and gate verdicts;
10. side-effect classification and disposition;
11. downstream invalidation effects;
12. terminal status and terminal disposition.

Repeating a step must never be implicit. Before launching a new attempt, MoonMind must know:

1. which logical step is being repeated;
2. which previous attempt caused the repeat;
3. what input and context the new attempt sees;
4. what workspace state the new attempt starts from;
5. which artifacts are evidence only;
6. which artifacts are reused as inputs;
7. what retrieval and memory context is visible;
8. which side effects are preserved, discarded, invalidated, or promoted;
9. what idempotency keys guard side effects;
10. what budget and stop rule applies.

A passing attempt may advance the logical step only after workflow-owned gates accept the result. Agent self-assessment alone is not sufficient.

---

## 4. Terminology

| Term | Desired meaning |
| --- | --- |
| **Logical Step** | Stable plan-node unit of work identified by `logicalStepId`. |
| **Step Execution** | One semantic execution of a logical step. Run-scoped executions are scoped to `(workflowId, runId, logicalStepId, executionOrdinal)`. |
| **Execution Ordinal** | The semantic execution number within one `(workflowId, runId, logicalStepId)` scope. |
| **Lineage Execution Ordinal** | Optional cross-run continuation ordinal used for UI/provenance, such as “recovered execution 2” when a linked recovery execution starts run-scoped execution 1. |
| **Retry** | Low-level re-run of the same idempotent Activity or provider call within the same Step Execution. |
| **Step Re-execution** | Semantic re-execution of the same logical step as a new Step Execution. |
| **RecoverFromFailedStep** | Product recovery action that creates a linked follow-up Workflow Execution and starts new work by creating a new Step Execution at the failed logical step. |
| **Checkpoint** | Durable evidence sufficient to restore or validate state at a step boundary. |
| **Context Bundle** | Immutable Step Execution-specific artifact describing instructions, prepared inputs, retrieval, memory, prior evidence, runtime-visible refs, and policy. |
| **Workspace Policy** | Explicit rule controlling the workspace or git state used to start the next Step Execution. |
| **Disposition** | The Step Execution's side-effect outcome, such as accepted, candidate, discarded, superseded, blocked, or needs human. |
| **Gate** | Structured verification step whose verdict controls whether work advances, repeats, stops, or requires human attention. |
| **Preserved Step** | A completed prior step imported into a resumed execution as provenance and reusable output refs, not freshly executed work. |
| **Invalidated Step** | A downstream step whose prior output is no longer valid because an upstream accepted output changed. |

The terms must remain distinct:

```text
retry                 = same Step Execution, transient or idempotent operation retry
step re-execution     = new Step Execution, same logical step
recover failed step   = linked Workflow Execution that begins by creating a new Step Execution at the failed step
```

Broad Temporal workflow retries are not a substitute for Step Executions when work may mutate a repository or external system.

---

## 5. Core Invariants

1. Logical step identity is stable within the resolved plan.
2. Run-scoped Step Execution identity is explicit and monotonically increasing per `(workflowId, runId, logicalStepId)`.
3. Cross-run lineage is optional provenance and must not replace run-scoped Step Execution identity.
4. A new semantic execution of a logical step must create a new Step Execution.
5. Large Step Execution content must stay in artifacts; workflow state carries compact refs only.
6. Step Execution evidence is append-only.
7. A repeated Step Execution must declare its source execution, reason, and workspace policy before execution begins.
8. The context bundle is immutable once the Step Execution starts and must be digest-addressed.
9. Side effects must be classified before the workflow advances.
10. Failed Step Execution artifacts are retained as evidence even when their workspace changes are discarded.
11. A logical implementation step is not succeeded until its accepted output is a committed/published code ref, an accepted artifact output, or an explicit accepted no-change disposition.
12. Passing a gate is the only normal path from repeated implementation work to publication or external handoff.
13. Publication, Jira movement, merge automation, and other external handoff steps must be gated by structured workflow state, not only by agent self-discipline.
14. Downstream steps that depend on changed upstream Step Execution outputs must be invalidated or revalidated before reuse.
15. Resume must not silently degrade to full rerun if checkpoint validation or restoration fails.
16. Runtime adapters may execute work, but MoonMind owns Step Execution identity, checkpoint policy, durable evidence refs, and final advancement decisions.
17. New Step Execution and checkpoint contracts that affect Temporal payloads must preserve in-flight compatibility or use an explicit versioned cutover plan.

---

## 6. Identity, Lineage, and Idempotency

### 6.1 Run-scoped Step Execution identity

The authoritative run-scoped key for a Step Execution is:

```text
(workflowId, runId, logicalStepId, executionOrdinal)
```

`executionOrdinal` is local to the current Temporal run of the current workflow execution.

Representative deterministic identifiers:

```text
stepExecutionId   = {workflowId}:{runId}:{logicalStepId}:execution:{executionOrdinal}
childWorkflowId   = {workflowId}:agent:{logicalStepId}:execution:{executionOrdinal}
checkpointId      = {workflowId}:{runId}:{logicalStepId}:execution:{executionOrdinal}:{boundary}
artifactLinkScope = step:{logicalStepId}:execution:{executionOrdinal}
```

If these identifiers are exposed to external systems, sanitize or hash fields as needed to avoid leaking sensitive task details.

### 6.2 Cross-run lineage

Failed-step recovery creates a new linked follow-up execution. In the recovery execution, the first newly executed failed step usually starts at run-scoped execution ordinal `1`, because it is a new `(workflowId, runId, logicalStepId)` scope.

For operator clarity, the recovered Step Execution may also carry lineage:

```json
{
  "executionOrdinal": 1,
  "executionScope": "run",
  "lineage": {
    "reason": "recover_from_failed_step",
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "source-run-id",
    "sourceLogicalStepId": "implement-story-S004",
    "sourceExecutionOrdinal": 1,
    "lineageExecutionOrdinal": 2,
    "relationship": "recover_from_failed_step"
  }
}
```

UI may render this as:

```text
Step 3 — resumed attempt 2, local attempt 1 in this run
```

Rules:

1. Local attempt identity remains the durable storage key.
2. Lineage identity is provenance and display metadata.
3. Resume must pin both source `workflowId` and source `runId` so lineage cannot drift when the source logical execution changes later.

### 6.3 Idempotency keys

All side-effecting Activities participating in an attempt must accept or derive stable idempotency keys.

Default key shape:

```text
{namespace}:{workflowId}:{runId}:{logicalStepId}:{attempt}:{operation}
```

Examples:

| Operation | Suggested key |
| --- | --- |
| Create step execution manifest | `{workflowId}:{runId}:{logicalStepId}:{attempt}:manifest` |
| Capture workspace checkpoint | `{workflowId}:{runId}:{logicalStepId}:{attempt}:checkpoint:{boundary}` |
| Launch managed AgentRun | `{workflowId}:{runId}:{logicalStepId}:{attempt}:agent-run` |
| Run quality gate | `{workflowId}:{runId}:{logicalStepId}:{executionOrdinal}:gate:{gateName}` |
| Commit accepted changes | `{workflowId}:{runId}:{logicalStepId}:{executionOrdinal}:commit` |
| Create PR | `{workflowId}:{runId}:{logicalStepId}:{executionOrdinal}:publish-pr` |
| Jira transition | `{workflowId}:{runId}:{logicalStepId}:{executionOrdinal}:jira-transition:{targetStatus}` |

Retries may log Temporal activity attempt numbers, but activity attempt numbers must not be the primary business idempotency key.

---

## 7. Step Execution Manifest Contract

A Step Execution manifest is an immutable artifact-backed record of one semantic execution. The workflow keeps only a compact projection of this record.

Representative shape:

```json
{
  "schemaVersion": "v1",
  "stepExecutionId": "mm:task:run-1:implement-story-S004:execution:3",
  "workflowId": "mm:task",
  "runId": "temporal-run-id",
  "logicalStepId": "implement-story-S004",
  "executionOrdinal": 3,
  "executionScope": "run",
  "lineage": {
    "sourceWorkflowId": "mm:task",
    "sourceRunId": "temporal-run-id",
    "sourceLogicalStepId": "implement-story-S004",
    "sourceExecutionOrdinal": 2,
    "lineageExecutionOrdinal": 3
  },
  "reason": "quality_gate_failed",
  "status": "running",
  "terminalDisposition": null,
  "startedAt": "2026-05-16T18:00:00Z",
  "updatedAt": "2026-05-16T18:02:30Z",
  "input": {
    "taskInputSnapshotRef": "art_task_snapshot",
    "planRef": "art_plan",
    "planDigest": "sha256:...",
    "inputSnapshotRef": "art_input_snapshot",
    "preparedInputRefs": ["art_prepared_context"]
  },
  "context": {
    "contextBundleRef": "art_step_execution_context",
    "contextBundleDigest": "sha256:...",
    "builderVersion": "step-context-builder-v1",
    "retrievalManifestRef": "art_retrieval_manifest",
    "memoryManifestRef": "art_memory_manifest"
  },
  "workspace": {
    "baseline": {
      "kind": "git_commit",
      "commit": "abc123"
    },
    "policy": "continue_from_previous_execution",
    "checkpointBeforeRef": "art_workspace_before_execution",
    "checkpointAfterRef": null
  },
  "execution": {
    "kind": "agent_run",
    "childWorkflowId": "mm:task:agent:implement-story-S004:execution-3",
    "childRunId": "child-run-id",
    "runtimeId": "codex_cli",
    "runtimeContextPolicy": "fresh_agent_run"
  },
  "outputs": {
    "summaryRef": null,
    "agentResultRef": null,
    "stdoutRef": null,
    "stderrRef": null,
    "diagnosticsRef": null,
    "diffRef": null,
    "patchRef": null
  },
  "checks": [],
  "sideEffects": {
    "git": {
      "disposition": "pending"
    },
    "external": [],
    "artifacts": [],
    "memory": [],
    "retrieval": []
  },
  "dependencyEffects": {
    "invalidatedLogicalStepIds": [],
    "preservedOutputRefs": []
  },
  "budget": {
    "executionLimit": 3,
    "executionOrdinalInLoop": 2,
    "remainingExecutions": 1
  }
}
```

### 7.1 Step Execution reasons

Canonical Step Execution reasons should include:

| Reason | Meaning |
| --- | --- |
| `initial_execution` | First Step Execution for a logical step. |
| `quality_gate_failed` | A structured verifier or gate requested more work. |
| `tests_failed` | Test, typecheck, build, or lint evidence requires repair. |
| `runtime_recovered` | Runtime/session failure requires a fresh Step Execution. |
| `recover_from_failed_step` | A linked recovery execution starts a new Step Execution at the failed step. |
| `remediation_context` | A remediation action supplied corrective context. |
| `operator_requested` | An explicit human action requested a Step Re-execution. |
| `dependency_invalidated` | A prior output changed and forced this step to re-run or revalidate. |
| `policy_revalidation` | A gate or policy changed and requires revalidation. |

Step Execution reasons must be bounded metadata, not free-form transcripts. Rich explanation belongs in artifacts.

### 7.2 Step Execution statuses

| Status | Meaning |
| --- | --- |
| `pending` | Step Execution record exists but execution has not started. |
| `preparing` | Inputs, context, or workspace are being prepared. |
| `running` | Agent/tool execution is active. |
| `checking` | Quality gates or verification are active. |
| `succeeded` | Step Execution succeeded and required checks passed. |
| `failed` | Step Execution failed or checks failed. |
| `blocked` | Step Execution cannot continue without external prerequisites or approval. |
| `canceled` | Attempt was canceled. |
| `superseded` | A later attempt replaced this attempt as the current candidate. |

### 7.3 Terminal dispositions

| Disposition | Meaning |
| --- | --- |
| `accepted` | Attempt passed gates and may advance the logical step. |
| `retryable` | Another attempt is allowed under budget and policy. |
| `blocked` | Missing prerequisite, credential, infrastructure, or approval prevents progress. |
| `needs_human` | Automated attempts are exhausted or unsafe. |
| `discarded` | Attempt evidence is retained but workspace effects are not reused. |
| `superseded` | A later attempt replaced this attempt. |
| `failed_unrecoverable` | The attempt or gate found a permanent blocker or unsafe condition. |

---

## 8. Context Bundle, Retrieval, and Memory Inputs

The context bundle is the Step Execution-specific input envelope visible to an agent, tool, or verifier. It must be immutable once the Step Execution starts.

Representative context bundle fields:

```json
{
  "schemaVersion": "v1",
  "workflowId": "mm:task",
  "runId": "temporal-run-id",
  "logicalStepId": "implement-story-S004",
  "executionOrdinal": 3,
  "reason": "quality_gate_failed",
  "taskInputSnapshotRef": "art_task_snapshot",
  "planRef": "art_plan",
  "planDigest": "sha256:...",
  "preparedInputRefs": ["art_prepared_context"],
  "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
  "workspaceBaseline": {
    "kind": "git_commit",
    "commit": "abc123"
  },
  "priorEvidenceRefs": ["art_execution_2_gate_report", "art_execution_2_diff"],
  "retrievalManifestRef": "art_retrieval_manifest_execution_3",
  "memoryManifestRef": "art_memory_manifest_execution_3",
  "runtimeSelection": {
    "runtimeId": "codex_cli",
    "skillId": "moonmind-story-execution"
  },
  "qualityGateProfile": "repo-default"
}
```

Rules:

1. The context bundle must not include raw credentials.
2. The context bundle must carry compact refs and summaries, not large logs or diffs.
3. The context bundle must include the workspace policy and baseline visible to the attempt.
4. If two attempts see different retrieval or memory context, that difference must be recorded through the context bundle and manifest refs.
5. The bundle should include a digest and builder version so operators can diagnose context drift.

### 8.1 Retrieval context

Retrieval and RAG inputs must be attempt inputs, not hidden ambient state.

Representative retrieval context:

```json
{
  "query": "Fix failing proposal-stage Temporal workflow integration evidence",
  "indexVersion": "rag-index-2026-05-16T12:00:00Z",
  "retrievedRefs": ["art_previous_failure_summary", "repo_docs_ref"],
  "retrievalManifestRef": "art_retrieval_manifest_attempt_3"
}
```

The retrieval manifest should preserve:

1. retrieval query or selector;
2. index version;
3. returned refs and scores where safe;
4. filters applied;
5. excluded refs when meaningful;
6. compact summaries included in the prompt.

### 8.2 Memory effects

Memory side effects must have promotion states.

| State | Meaning |
| --- | --- |
| `proposed` | Attempt generated a possible memory update. |
| `accepted_for_run_context` | Memory is visible to later attempts in the same run. |
| `applied_to_repo` | Memory was committed or otherwise written to durable repo instructions. |
| `rejected` | Memory proposal was intentionally not used. |
| `superseded` | A later proposal replaced this one. |

Failed or abandoned Step Executions must not silently write durable repo memory. Run-local memory may be accepted for later Step Executions when policy allows it, but durable repo instruction changes require explicit policy and normal publication gates.

Representative memory proposal:

```json
{
  "id": "mem-S004-A2-001",
  "kind": "codebase_pattern",
  "target": "frontend/billing/AGENTS.md",
  "textRef": "art_memory_proposal_text",
  "status": "proposed",
  "sourceExecutionOrdinal": {
    "workflowId": "mm:task",
    "runId": "temporal-run-id",
    "logicalStepId": "implement-story-S004",
    "executionOrdinal": 2
  }
}
```

---

## 9. Checkpoint Contract

Checkpoints are durable artifacts or durable read-model records that make Step Re-executions and RecoverFromFailedStep truthful.

A checkpoint records enough information to restore or validate state at a boundary without parsing terminal logs or reconstructing state from UI projections.

Representative checkpoint:

```json
{
  "schemaVersion": "v1",
  "checkpointId": "mm:task:run-1:implement-story-S004:execution:2:after_gate",
  "checkpointKind": "step_boundary",
  "boundary": "after_gate",
  "source": {
    "workflowId": "mm:task",
    "runId": "temporal-run-id",
    "logicalStepId": "implement-story-S004",
    "executionOrdinal": 2
  },
  "taskInputSnapshotRef": "art_original_task_input",
  "planRef": "art_plan",
  "planDigest": "sha256:...",
  "preparedInputRefs": ["art_prepared_inputs"],
  "workspace": {
    "kind": "git_patch",
    "baseCommit": "abc123",
    "patchRef": "art_execution_2_patch",
    "includesUntracked": true,
    "manifestRef": "art_attempt_2_patch_manifest"
  },
  "stepOutputs": {
    "summaryRef": "art_attempt_2_summary",
    "agentResultRef": "art_attempt_2_result",
    "verificationRef": "art_attempt_2_verification"
  },
  "createdAt": "2026-05-16T18:05:00Z"
}
```

Checkpoint refs must remain outside large inline workflow histories when they are large or binary.

### 9.1 Checkpoint boundaries

MoonMind should create or update checkpoint evidence at these boundaries:

1. after prepare succeeds;
2. before a mutating step execution starts, when a restorable baseline exists;
3. after a step execution completes;
4. after quality gates complete;
5. before publication or external state transitions;
6. before Resume restoration executes any new work.

Checkpoint writes must be idempotent because Activities and workflow tasks may retry.

### 9.2 Workspace checkpoint kinds

Supported checkpoint kinds should be explicit.

| Kind | Meaning | Restore strength |
| --- | --- | --- |
| `git_commit` | A committed repo state. | Strong for tracked code state. |
| `git_patch` | Patch artifact against a base commit, optionally including untracked-file manifest. | Good for candidate work; weaker for binary/submodule/ignored-file state. |
| `worktree_archive` | Archive of a worktree plus manifest. | Stronger for dirty worktrees and untracked files; larger artifact. |
| `ephemeral_workspace_ref` | Reference to a still-valid managed workspace/container state. | Only valid within TTL/reachability constraints. |
| `external_state_ref` | Provider-specific state ref for an external delegated runtime. | Adapter-specific; may not support direct restoration. |

Representative workspace checkpoint:

```json
{
  "kind": "worktree_archive",
  "baseCommit": "abc123",
  "archiveRef": "art_workspace_tar",
  "manifestRef": "art_workspace_manifest",
  "includesIgnoredFiles": false,
  "createdAt": "2026-05-16T18:05:00Z"
}
```

### 9.3 Policy-to-checkpoint requirements

| Workspace policy | Minimum checkpoint evidence |
| --- | --- |
| `restore_pre_execution` | `git_commit`, `worktree_archive`, or equivalent durable workspace ref from before the Step Execution. |
| `continue_from_previous_execution` | Valid live workspace ref or after-execution checkpoint. |
| `apply_previous_execution_diff_to_clean_baseline` | `git_patch` plus base commit, or equivalent patchable diff artifact. |
| `start_from_last_passed_commit` | Accepted commit SHA or accepted published ref. |
| `fresh_branch_from_source` | Source repo/ref and branch/worktree creation policy. |

If the required checkpoint evidence is unavailable, the workflow must reject the policy before launching the next Step Execution.

### 9.4 Checkpoint validation

Before a checkpoint can be used to start a new attempt or Resume execution, MoonMind must validate:

1. source `workflowId` and `runId`;
2. task input snapshot identity;
3. plan identity and digest;
4. logical step identity;
5. attempt provenance;
6. artifact existence and authorization;
7. workspace, branch, and commit consistency;
8. checkpoint kind compatibility with the selected workspace policy;
9. policy eligibility for replaying or preserving side effects.

If validation fails, MoonMind must fail explicitly before launching an agent or mutating the workspace.

---

## 10. Workspace and Git Policy

Repeated coding work is not purely functional. Each attempt may leave a changed workspace.

MoonMind must record a workspace policy before launching a repeated attempt.

Canonical policies:

| Policy | Meaning | Typical use |
| --- | --- | --- |
| `continue_from_previous_execution` | Keep the prior Step Execution's working tree and ask the next Step Execution to repair it. | Mostly good diff with failing tests. |
| `restore_pre_execution` | Restore the workspace to the checkpoint from before the failed Step Execution. | Unsafe, messy, or broad failed Step Execution. |
| `apply_previous_execution_diff_to_clean_baseline` | Reset to a clean baseline, then apply the previous Step Execution diff as an explicit patch artifact. | Preserve useful work while removing hidden drift. |
| `start_from_last_passed_commit` | Start from the latest committed accepted step state. | Autonomous story loop after a failed story Step Execution. |
| `fresh_branch_from_source` | Start a new branch/worktree from the source ref. | Full retry or unsafe workspace state. |

The selected policy must be visible in Step Execution metadata and operator diagnostics.

### 10.1 Git effect states

Every mutating Step Execution should classify its git effect.

| Disposition | Meaning |
| --- | --- |
| `accepted` | Attempt changes are committed or otherwise durable and approved to advance the logical step. |
| `candidate` | Attempt produced useful changes but did not pass gates. Diff/checkpoint evidence is retained. |
| `discarded` | Attempt changes are intentionally not carried forward. Evidence remains. |
| `superseded` | A later attempt replaces this attempt's candidate state. |
| `none` | Attempt did not produce workspace changes. |

Representative candidate git effect:

```json
{
  "baselineCommit": "abc123",
  "headCommit": null,
  "workingTreeDiffRef": "art_diff_attempt_2",
  "patchRef": "art_patch_attempt_2",
  "commitCreated": false,
  "workspaceCheckpointRef": "art_workspace_after_attempt_2",
  "disposition": "candidate"
}
```

Representative accepted git effect:

```json
{
  "baselineCommit": "abc123",
  "headCommit": "def456",
  "workingTreeDiffRef": "art_diff_attempt_3",
  "patchRef": "art_patch_attempt_3",
  "commitCreated": true,
  "workspaceCheckpointRef": "art_workspace_after_attempt_3",
  "disposition": "accepted"
}
```

MoonMind must not treat an uncommitted dirty workspace as an accepted state.

### 10.2 Accepted output rule

A logical implementation step may be marked `succeeded` only when its accepted output is one of:

1. a commit SHA;
2. a pushed branch/ref;
3. a typed non-git output artifact accepted by gates;
4. an explicit no-change accepted disposition.

For autonomous coding/story loops, the recommended default is:

```text
failed or partial attempt -> diff artifact only, no story advancement
passing attempt           -> commit, accepted git effect, logical step advancement
```

---

## 11. Side-Effect Classes and External Guardrails

Every nontrivial attempt should classify side effects before the workflow advances.

Suggested side-effect classes:

| Class | Meaning | Reattempt posture |
| --- | --- | --- |
| `workspace_mutation` | Local repo/workspace mutation. | Allowed only with workspace policy and checkpoint evidence. |
| `artifact_write` | Artifact creation or linkage. | Append-only and retry-safe through idempotent artifact IDs or content hashes. |
| `external_idempotent` | External mutation guarded by a durable idempotency key. | Allowed when key is stable and operation is safe to repeat. |
| `external_non_idempotent` | External mutation that cannot safely repeat. | Forbidden in autonomous reattempts unless explicit policy permits. |
| `publication` | Branch/PR/publish/merge handoff. | Requires gate-approved state. |
| `provider_account` | Provider profile slot, OAuth, account-level state. | Requires provider-profile policy and cleanup/release semantics. |
| `memory_update` | Run-local or repo-level memory change. | Requires promotion state and policy. |
| `retrieval_index_update` | RAG index mutation or cache update. | Requires index/version policy. |

Representative side effect:

```json
{
  "class": "external_idempotent",
  "operation": "jira.add_comment",
  "idempotencyKey": "mm:task:run-1:verify:attempt:1:jira-comment",
  "target": "MM-123",
  "disposition": "accepted"
}
```

Rules:

1. Non-idempotent external work must not be hidden inside repeated implementation attempts.
2. Publication, Jira transitions, merge, deployment, and provider-account actions require gate-approved workflow state.
3. If a non-idempotent side effect already occurred, a later attempt must account for it explicitly rather than pretending the step can be reset.
4. External cleanup or compensation must be explicit, idempotent, and observable.

---

## 12. Activity Surface and Worker Boundaries

`MoonMind.Run` owns orchestration. Activities own side effects. Runtime adapters execute attempts but do not own attempt semantics.

Suggested Activity families for implementation:

| Activity | Role | Typical queue |
| --- | --- | --- |
| `step_execution.create_manifest` | Create a step execution manifest artifact shell or deterministic ref. | `mm.activity.artifacts` |
| `step_execution.write_manifest` | Write/update immutable or append-only attempt evidence. | `mm.activity.artifacts` |
| `step_execution.record_evidence` | Link runtime outputs, checks, and side-effect refs to attempt evidence. | `mm.activity.artifacts` |
| `step_checkpoint.create` | Create checkpoint evidence at a boundary. | `mm.activity.sandbox` or `mm.activity.artifacts` depending on content |
| `step_checkpoint.validate` | Validate checkpoint source, refs, plan digest, and policy compatibility. | `mm.activity.artifacts` / `mm.activity.sandbox` |
| `workspace.capture_checkpoint` | Capture commit, patch, archive, or workspace ref. | `mm.activity.sandbox` |
| `workspace.apply_policy` | Apply selected workspace policy before an attempt. | `mm.activity.sandbox` |
| `workspace.classify_git_effect` | Inspect workspace and produce git disposition metadata. | `mm.activity.sandbox` |
| `quality_gate.run_profile` | Run typecheck/test/lint/browser/review gates. | `mm.activity.sandbox` / `mm.activity.llm` |
| `retrieval.build_step_execution_manifest` | Build retrieval manifest for attempt context. | `mm.activity.artifacts` / retrieval worker if added |
| `memory.evaluate_proposals` | Validate/dedupe/score memory proposals. | `mm.activity.llm` / `mm.activity.artifacts` |
| `memory.apply_policy` | Promote memory to run context or repo instructions according to policy. | `mm.activity.sandbox` / `mm.activity.artifacts` |

These names are desired-state examples, not a requirement to create every Activity in the first implementation slice.

Rules:

1. Workflow code must not read files, run git, inspect artifacts, call providers, or mutate memory directly.
2. Activity results crossing workflow boundaries must be compact and typed.
3. Large outputs must be persisted as artifacts and passed back as refs.
4. Activities must be idempotent or guarded by idempotency keys derived from attempt identity.

---

## 13. Gated Iteration

A gated iteration loop is a workflow-owned repetition policy around one or more logical steps.

Representative flow:

```text
verify
while verdict == ADDITIONAL_WORK_NEEDED and budget remains:
  create new remediation attempt
  run remediation using latest verification report
  create new verification attempt
  verify again

if verdict == FULLY_IMPLEMENTED:
  continue to publication
else:
  stop with remaining work and evidence
```

The loop belongs to `MoonMind.Run`, not only to agent instructions.

Agent instructions may say what to do inside an attempt, but the parent workflow must own:

1. whether another attempt is allowed;
2. which step or group repeats;
3. which verdicts pass, retry, fail, or require attention;
4. which downstream steps are skipped or invalidated when gates fail;
5. final publication eligibility.

### 13.1 Gate verdict contract

Gate-producing steps should return structured verdicts.

Suggested verdicts:

| Verdict | Meaning |
| --- | --- |
| `FULLY_IMPLEMENTED` | Gate passed and downstream publication may proceed. |
| `ADDITIONAL_WORK_NEEDED` | More bounded work is required and may be attempted if budget remains. |
| `NO_DETERMINATION` | Evidence is insufficient; proceed only when missing evidence is recoverable in the current runtime. |
| `BLOCKED` | External or policy condition prevents progress. |
| `FAILED_UNRECOVERABLE` | The gate found a permanent blocker or unsafe condition. |

Representative gate result:

```json
{
  "schemaVersion": "v1",
  "verdict": "ADDITIONAL_WORK_NEEDED",
  "confidence": "medium",
  "validatedRefs": {
    "diffRef": "art_attempt_2_diff",
    "testReportRef": "art_attempt_2_tests"
  },
  "invalidatedRefs": [],
  "remainingWorkRef": "art_remaining_work",
  "blockingEvidenceRefs": ["art_verify_report"],
  "recommendedNextAction": "reattempt_current_step",
  "targetLogicalStepId": "implement-story-S004",
  "workspacePolicyRecommendation": "apply_previous_execution_diff_to_clean_baseline",
  "recoverableInCurrentRuntime": true
}
```

For a passing gate:

```json
{
  "schemaVersion": "v1",
  "verdict": "FULLY_IMPLEMENTED",
  "confidence": "high",
  "validatedRefs": {
    "commit": "def456",
    "diffRef": "art_attempt_3_diff",
    "testReportRef": "art_attempt_3_tests",
    "browserEvidenceRef": "art_attempt_3_browser"
  },
  "invalidatedRefs": [],
  "recommendedNextAction": "advance"
}
```

The parent workflow must branch on structured gate results instead of parsing prose summaries.

### 13.2 Budgets and stop rules

Every autonomous loop must have explicit budgets.

Budget dimensions may include:

1. maximum attempts;
2. maximum wall-clock time;
3. maximum provider spend or token estimate;
4. maximum consecutive no-progress attempts;
5. maximum failed command repeats;
6. maximum unsafe or policy-denied attempts.

When a budget is exhausted, MoonMind must stop with a deterministic terminal disposition such as `needs_human`, `blocked`, or `failed_with_remaining_work`. It must publish the latest evidence and recommended next action.

---

## 14. Dependency Invalidation and Preserved Output Reuse

A repeated attempt can change the accepted output of a logical step. Downstream steps that consumed the old output may no longer be valid.

Rules:

1. Activity/tool/agent outputs used by downstream steps must be associated with the producing attempt identity.
2. A downstream input reference should resolve to a specific producing Step Execution output, not ambiguous “latest” output, unless the workflow explicitly refreshes it.
3. If a new accepted Step Execution changes semantic outputs, downstream steps that depended on old outputs must be marked `pending`, `blocked`, `superseded`, or `requires_revalidation`.
4. Preserved steps in RecoverFromFailedStep must carry `preservedFrom.workflowId`, `preservedFrom.runId`, `preservedFrom.logicalStepId`, and `preservedFrom.executionOrdinal`.
5. Preserved step outputs may be reused only when the checkpoint proves they still satisfy downstream contracts.
6. If a preserved output ref is missing, unauthorized, corrupted, or plan-mismatched, Resume must fail before executing new work.
7. A gate may validate that downstream outputs are still valid; that validation result must be structured evidence, not prose.

Representative dependency effect:

```json
{
  "changedOutputs": [
    {
      "logicalStepId": "implement-api",
      "executionOrdinal": 2,
      "outputRef": "art_api_contract_v2"
    }
  ],
  "invalidatedLogicalStepIds": ["implement-ui", "verify-integration"],
  "revalidationRequired": ["verify-integration"]
}
```

This rule is required for both failed-step recovery and autonomous story loops. Without it, MoonMind could accidentally present downstream work as valid even though it was built against a superseded upstream attempt.

---

## 15. Resume Relationship

Failed-step recovery is one consumer of Step Executions with Checkpointing.

Resume does not continue the old failed step in place. Resume creates a linked follow-up execution and starts new work by creating a new local Step Execution for the failed logical step.

Resume attempt properties:

1. `reason = recover_from_failed_step`;
2. local `attempt` starts in the resumed run scope, usually `1`;
3. `lineage.sourceExecutionOrdinal` points to the failed attempt in the source run;
4. `lineage.lineageExecutionOrdinal` may show the cross-run continuation number;
5. `sourceWorkflowId` and `sourceRunId` are pinned;
6. completed prior steps are imported as preserved progress from refs;
7. the workspace is restored from a validated checkpoint;
8. the failed step is the first newly executed logical step;
9. later steps execute normally after the failed step succeeds, subject to dependency invalidation rules.

Resume must not:

1. allow silent task input edits in the failed-step recovery path;
2. silently fall back to full rerun;
3. re-execute preserved prior steps unless a future explicit mode requests it;
4. ignore missing, corrupted, unauthorized, or inconsistent checkpoint evidence;
5. conflate local attempt identity with cross-run lineage identity.

Representative Resume step row text:

```text
Step 1: Prepare repo — completed, preserved from source run
Step 2: Implement API — completed, preserved from source run
Step 3: Run tests — resumed attempt 2, local attempt 1 running now
```

---

## 16. Managed Runtime Relationship

Managed runtimes execute attempts; MoonMind owns attempt semantics.

For a managed agent runtime attempt, `MoonMind.Run` should:

1. create the Step Execution record;
2. prepare and record the immutable context bundle;
3. validate and apply the workspace policy;
4. launch or reuse the appropriate `MoonMind.AgentRun` boundary according to runtime context policy;
5. pass compact refs and instructions to the runtime;
6. collect runtime output refs;
7. run or schedule quality gates;
8. classify side effects;
9. update the step ledger projection and step execution manifest.

An agent may recommend another attempt, but it must not unilaterally create hidden attempts or mutate the attempt ledger.

### 16.1 Runtime context policy

Attempt records should distinguish runtime/session policy from workspace policy.

Representative policy:

```json
{
  "runtimeContextPolicy": "fresh_agent_run",
  "sessionPolicy": {
    "codex_cli": "new_epoch_per_attempt",
    "claude_code": "new_process_per_attempt"
  }
}
```

Suggested values:

| Policy | Meaning |
| --- | --- |
| `fresh_agent_run` | Start a new `MoonMind.AgentRun` child workflow for the attempt. |
| `reuse_session_new_epoch` | Reuse a task-scoped managed session container but clear/reset to a new epoch before the attempt. |
| `reuse_session_same_epoch` | Keep session continuity across attempts; should be rare and explicit. |
| `external_provider_continuation` | Delegate to provider-specific continuation semantics when MoonMind cannot control runtime state directly. |

For Codex managed sessions, Ralph-style clean context should usually mean `fresh_agent_run` plus `reuse_session_new_epoch` or equivalent `clear_session` semantics. For Claude Code, it should usually mean a new process/container per attempt. External or coordinated agents may have weaker runtime control; in that mode, MoonMind should still record attempt identity, context refs, known side effects, and checkpoint evidence where available. Features that require direct workspace restoration may be unavailable or require a different policy.

---

## 17. Operator and API Surfaces

The default task detail view should stay simple:

1. each logical step shows the latest/current attempt;
2. attempt count is visible;
3. blocked or failed gate summaries are visible;
4. the latest evidence refs are accessible;
5. preserved or resumed provenance is clearly marked.

Expanded surfaces should allow operators to inspect attempt history:

1. attempt number;
2. lineage;
3. reason;
4. source attempt;
5. runtime child refs;
6. context bundle ref;
7. workspace policy;
8. git disposition;
9. quality gate verdict;
10. output, diagnostics, and diff refs;
11. terminal disposition.

APIs should expose bounded attempt projections and artifact refs. They must not inline large transcripts, diffs, provider payloads, or verification reports.

Suggested API shape:

```http
GET /api/executions/{workflowId}/steps
GET /api/executions/{workflowId}/steps/{logicalStepId}/attempts
GET /api/executions/{workflowId}/steps/{logicalStepId}/attempts/{attempt}
```

Representative attempt list response:

```json
{
  "workflowId": "mm:task",
  "runId": "temporal-run-id",
  "logicalStepId": "implement-story-S004",
  "latestStepExecution": 3,
  "stepExecutions": [
    {
      "executionOrdinal": 1,
      "status": "failed",
      "reason": "initial_execution",
      "gitDisposition": "candidate",
      "gateVerdict": "ADDITIONAL_WORK_NEEDED",
      "manifestRef": "art_execution_1_manifest"
    },
    {
      "executionOrdinal": 2,
      "status": "failed",
      "reason": "tests_failed",
      "gitDisposition": "candidate",
      "gateVerdict": "ADDITIONAL_WORK_NEEDED",
      "manifestRef": "art_execution_2_manifest"
    },
    {
      "executionOrdinal": 3,
      "status": "succeeded",
      "reason": "quality_gate_failed",
      "gitDisposition": "accepted",
      "gateVerdict": "FULLY_IMPLEMENTED",
      "manifestRef": "art_execution_3_manifest"
    }
  ]
}
```

---

## 18. Failure and Stop Semantics

When an attempt fails or a gate requests more work, MoonMind must classify the next state.

Suggested terminal dispositions:

| Disposition | Meaning |
| --- | --- |
| `accepted` | Attempt passed gates and may advance the logical step. |
| `retryable` | Another attempt is allowed under budget and policy. |
| `blocked` | Missing prerequisite, credential, infrastructure, or approval prevents progress. |
| `needs_human` | Automated attempts are exhausted or unsafe. |
| `discarded` | Attempt evidence is retained but workspace effects are not reused. |
| `superseded` | A later attempt replaced this attempt. |
| `failed_with_remaining_work` | Automation stopped with known remaining work and evidence. |

When a downstream step depends on a passing gate, the parent workflow must skip, block, invalidate, or revalidate that downstream step if the gate fails. Publication and external state transitions must not rely only on the downstream agent noticing the failed gate.

---

## 19. Security and Side-Effect Guardrails

Step Executions with Checkpointing must preserve MoonMind's security posture.

Rules:

1. Attempt context bundles must not include raw credentials.
2. Artifact refs must respect normal authorization checks.
3. Workspace restore operations must be scoped to the run workspace or approved worktree.
4. External side effects must be idempotent or guarded by durable keys.
5. Non-idempotent external work must not be reattempted without explicit policy.
6. Publication, Jira transition, merge, deployment, and provider-account actions require gate-approved state.
7. Failed attempt logs and diagnostics must be sanitized before display or publication.
8. Repo and local skill sources remain untrusted input; attempt context must respect skill source policy.
9. Step execution manifests, context bundles, checkpoints, retrieval manifests, and memory proposals must not contain raw secrets.
10. Checkpoint restoration must not allow path traversal, unauthorized file materialization, or writes outside approved workspaces.

---

## 20. Examples

### 20.1 Jira Orchestrate gated implementation

Desired behavior:

```text
1. implement task breakdown
2. verify completion -> ADDITIONAL_WORK_NEEDED
3. remediate attempt 1
4. verify remediation attempt 1 -> ADDITIONAL_WORK_NEEDED
5. remediate attempt 2
6. verify remediation attempt 2 -> FULLY_IMPLEMENTED
7. create pull request
8. move Jira to Code Review
```

If the post-remediation gate remains `ADDITIONAL_WORK_NEEDED` after budget exhaustion, `MoonMind.Run` stops before pull request creation and Jira movement. The final state includes the latest verification report, remaining work, attempted remediation evidence, side-effect dispositions, and a recommended next action.

### 20.2 Failed-step recovery

Desired behavior:

```text
source run:
  step 1 succeeded, local attempt 1
  step 2 succeeded, local attempt 1
  step 3 failed, local attempt 1

resume run:
  step 1 preserved from source refs
  step 2 preserved from source refs
  step 3 local attempt 1 executes from validated checkpoint
          lineage shows resumed attempt 2
  step 4 executes normally after step 3 passes
```

The resumed run does not reconstruct progress from logs and does not silently rerun steps 1 and 2.

### 20.3 Autonomous story loop

Desired behavior:

```text
story S004 attempt 1:
  implementation produced candidate diff
  tests failed
  diff retained as artifact

story S004 attempt 2:
  starts with policy apply_previous_execution_diff_to_clean_baseline
  fixes tests
  verifier passes
  changes are committed
  logical story step is marked passed
```

The failed attempt remains visible as evidence even though its workspace state is not accepted.

### 20.4 Dependency invalidation

Desired behavior:

```text
step 2 attempt 1 accepted API contract v1
step 3 attempt 1 implemented UI against API contract v1
step 4 verification failed and requested API change
step 2 attempt 2 accepted API contract v2
step 3 is invalidated because it consumed API contract v1
step 3 reattempts or revalidates before step 4 can pass
```

MoonMind must not preserve step 3 as successful unless a gate proves it remains valid against API contract v2.

---

## 21. Non-Goals

This design does not require:

1. infinite autonomous loops;
2. hiding attempts inside a single agent transcript;
3. storing large diffs or logs in workflow state;
4. treating Temporal Activity retries as semantic reattempts;
5. automatically replaying every possible external side effect;
6. committing memory proposals from failed attempts directly to the repo;
7. making failed-step recovery and autonomous loops separate recovery systems;
8. exposing full immutable attempt history in the default task detail view;
9. forcing every Step Type to use autonomous reattempt semantics.

---

## 22. Rollout Guidance

Implementation should be phased.

### Phase 1: Step execution manifests and evidence

1. Add step execution manifest artifact type.
2. Add latest-attempt refs to the step ledger.
3. Capture child workflow refs, summaries, diagnostics, and diffs per attempt.
4. Add bounded API support for attempt lists.

### Phase 2: Checkpoints

1. Capture before/after workspace checkpoints.
2. Validate checkpoint source identity, plan digest, prepared input refs, and workspace policy compatibility.
3. Materialize preserved prior steps in Resume.
4. Fail Resume explicitly when checkpoint validation fails.

### Phase 3: Gated reattempt loops

1. Add structured gate verdicts.
2. Add budgets and stop rules.
3. Add workspace policy selection for reattempts.
4. Add downstream invalidation/revalidation behavior.

### Phase 4: Retrieval and memory manifests

1. Add retrieval manifest refs to context bundles.
2. Add memory proposal manifests and promotion states.
3. Permit run-local memory promotion under policy.
4. Gate repo-level memory writes.

### Phase 5: Autonomous story / PRD loop

1. Compile PRD/story items into logical steps.
2. Use Step Executions for each story implementation attempt.
3. Use independent quality gates.
4. Commit only accepted attempts.
5. Stop on completion, budget exhaustion, blocked state, or human-needed disposition.

---

## 23. Constitution Alignment

Step Executions with Checkpointing supports MoonMind's core principles:

1. **Orchestrate, Don't Recreate**: agents execute attempts; MoonMind owns orchestration, state, and evidence.
2. **Own Your Data**: attempt context, outputs, diffs, and checkpoints are operator-controlled artifacts.
3. **Resilient by Default**: repeated work is explicit, bounded, idempotency-aware, and recoverable.
4. **Spec-Driven Development**: gates and attempts preserve traceability from requirement to implementation to evidence.
5. **Continuous Improvement**: failed attempts produce structured evidence and improvement signals rather than disappearing into logs.

The key desired-state rule is simple:

> A repeated step is not the same execution happening again. It is a new attempt with explicit context, checkpoint, side-effect policy, evidence, lineage, and stop rules.

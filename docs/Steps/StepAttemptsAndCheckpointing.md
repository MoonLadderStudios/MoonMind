# Step Attempts and Checkpointing

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-05-16
Canonical for: semantic step reattempts, checkpointed side-effect policy, gated iteration, failed-step recovery primitive
Related: `docs/Steps/StepTypes.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskRemediation.md`, `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Artifacts/ArtifactPresentationContract.md`

---

## 1. Purpose

This document defines the desired-state MoonMind model for repeating work safely.

MoonMind tasks often need more than one pass. An implementation step may fail tests, a verifier may report `ADDITIONAL_WORK_NEEDED`, a pull request resolver may need another repair attempt, or an operator may resume a failed task from the last failed step.

All of those cases depend on the same primitive:

> Re-execute a logical step as a new attempt while explicitly controlling which previous side effects are preserved, restored, ignored, or superseded.

This document defines that primitive as **Step Attempts with Checkpointing**.

It is canonical for:

1. semantic re-execution of a logical step;
2. per-attempt identity, context, artifacts, checks, and disposition;
3. workspace, git, artifact, memory, and retrieval side-effect policy;
4. gated iteration loops such as `remediate -> verify`;
5. the shared foundation used by failed-step Resume and autonomous story or quality-gate loops.

This document does **not** redefine:

1. product-facing Step Types;
2. executable Tool contracts;
3. generic artifact storage internals;
4. Temporal activity retry policy;
5. provider-specific runtime launch internals.

Use `docs/Steps/StepTypes.md` for the user-facing step taxonomy. Use `docs/Temporal/StepLedgerAndProgressModel.md` for the operator-facing step ledger shape. Use `docs/Tasks/TaskArchitecture.md` for task create, rerun, and Resume semantics.

---

## 2. Desired-State Summary

A MoonMind plan contains **logical steps**. A logical step may have one or more **Step Attempts**.

```text
Logical Step: implement-story-S004
  Attempt 1: failed quality gate
  Attempt 2: failed integration evidence
  Attempt 3: passed and accepted
```

Each Step Attempt has its own:

1. attempt identity;
2. reason for execution;
3. prepared input snapshot;
4. context bundle;
5. workspace and git baseline;
6. agent child workflow or tool activity refs;
7. artifact refs;
8. quality checks and gate verdicts;
9. side-effect disposition;
10. terminal status.

The current step ledger may show the latest attempt for a logical step, but attempt evidence is append-only and remains inspectable.

Repeating a step must never be implicit. Before launching a new attempt, MoonMind must know:

1. which logical step is being repeated;
2. which previous attempt caused the repeat;
3. what input and context the new attempt sees;
4. what workspace state the new attempt starts from;
5. which artifacts are evidence only;
6. which artifacts are reused as inputs;
7. what retrieval and memory context is visible;
8. which side effects are preserved, discarded, or promoted;
9. what budget and stop rule applies.

---

## 3. Terminology

| Term | Desired meaning |
|------|-----------------|
| **Logical Step** | Stable plan-node unit of work identified by `logicalStepId`. |
| **Step Attempt** | One semantic execution of a logical step. Attempts are scoped to `(workflowId, runId, logicalStepId, attempt)`. |
| **Retry** | Low-level re-run of the same idempotent Activity or provider call within the same Step Attempt. |
| **Reattempt** | Semantic re-execution of the same logical step as a new Step Attempt. |
| **Resume** | Product recovery action that creates a linked follow-up execution and starts new work at a reattempt of the failed logical step. |
| **Checkpoint** | Durable evidence sufficient to restore or validate state at a step boundary. |
| **Context Bundle** | Attempt-specific artifact describing instructions, prepared inputs, retrieval, memory, prior evidence, and runtime-visible refs. |
| **Workspace Policy** | Explicit rule controlling the workspace or git state used to start the next attempt. |
| **Disposition** | The attempt's side-effect outcome, such as accepted, candidate, discarded, superseded, or blocked. |
| **Gate** | Structured verification step whose verdict controls whether work advances, repeats, stops, or requires human attention. |

The terms must remain distinct:

```text
retry     = same attempt, transient or idempotent operation retry
reattempt = new attempt, same logical step
resume    = linked execution that begins by creating a reattempt
```

Broad Temporal workflow retries are not a substitute for Step Attempts when work may mutate a repository or external system.

---

## 4. Core Invariants

1. Logical step identity is stable within the resolved plan.
2. Attempt identity is explicit and monotonically increasing per `(workflowId, runId, logicalStepId)`.
3. A new semantic execution of a logical step must create a new Step Attempt.
4. Large attempt content must stay in artifacts; workflow state carries compact refs only.
5. Attempt evidence is append-only.
6. A repeated attempt must declare its source attempt and reason.
7. A repeated attempt must declare its workspace policy before execution begins.
8. Side effects must be classified before the workflow advances.
9. Failed attempt artifacts are retained as evidence even when their workspace changes are discarded.
10. Passing a gate is the only normal path from repeated implementation work to publication.
11. Publication, Jira movement, merge automation, and other external handoff steps must be gated by structured workflow state, not only by agent self-discipline.
12. Resume must not silently degrade to full rerun if checkpoint validation or restoration fails.
13. Runtime adapters may execute attempts, but MoonMind owns attempt identity, checkpoint policy, and durable evidence refs.
14. New attempt and checkpoint contracts that affect Temporal payloads must preserve in-flight compatibility or use an explicit versioned cutover plan.

---

## 5. Step Attempt Contract

A Step Attempt is a compact workflow-owned record backed by artifacts.

Representative shape:

```json
{
  "schemaVersion": "v1",
  "workflowId": "mm:task",
  "runId": "temporal-run-id",
  "logicalStepId": "implement-story-S004",
  "attempt": 3,
  "reason": "quality_gate_failed",
  "sourceAttempt": 2,
  "status": "running",
  "startedAt": "2026-05-16T18:00:00Z",
  "updatedAt": "2026-05-16T18:02:30Z",
  "inputSnapshotRef": "art_input_snapshot",
  "preparedInputRefs": ["art_prepared_context"],
  "contextBundleRef": "art_attempt_context",
  "workspace": {
    "baseline": {
      "kind": "git_commit",
      "commit": "abc123"
    },
    "policy": "continue_from_previous_attempt",
    "checkpointBeforeRef": "art_workspace_before_attempt"
  },
  "execution": {
    "kind": "agent_run",
    "childWorkflowId": "mm:task:agent:implement-story-S004:attempt-3",
    "childRunId": "child-run-id",
    "runtimeId": "codex_cli"
  },
  "outputs": {
    "summaryRef": null,
    "agentResultRef": null,
    "stdoutRef": null,
    "stderrRef": null,
    "diagnosticsRef": null,
    "diffRef": null
  },
  "checks": [],
  "sideEffects": {
    "git": {
      "disposition": "pending"
    },
    "artifacts": [],
    "memory": [],
    "retrieval": []
  },
  "terminalDisposition": null
}
```

The workflow ledger may expose a bounded projection of this record. The full context bundle, diffs, diagnostics, and verification reports remain artifact-backed.

### 5.1 Attempt reasons

Canonical attempt reasons should include:

| Reason | Meaning |
|--------|---------|
| `initial_execution` | First attempt for a logical step. |
| `quality_gate_failed` | A structured verifier or gate requested more work. |
| `tests_failed` | Test, typecheck, build, or lint evidence requires repair. |
| `runtime_recovered` | Runtime/session failure requires a fresh attempt. |
| `resume_from_failed_step` | A linked Resume execution is retrying the failed step. |
| `remediation_context` | A remediation action supplied corrective context. |
| `operator_requested` | An explicit human action requested a reattempt. |

Attempt reasons must be bounded metadata, not free-form transcripts. Rich explanation belongs in artifacts.

### 5.2 Attempt statuses

Step Attempt status should be compatible with the step ledger vocabulary while allowing attempt-specific finality.

Suggested statuses:

| Status | Meaning |
|--------|---------|
| `pending` | Attempt record exists but execution has not started. |
| `preparing` | Inputs, context, or workspace are being prepared. |
| `running` | Agent/tool execution is active. |
| `checking` | Quality gates or verification are active. |
| `succeeded` | Attempt execution succeeded and checks passed. |
| `failed` | Attempt execution failed or checks failed. |
| `blocked` | Attempt cannot continue without external prerequisites or approval. |
| `canceled` | Attempt was canceled. |
| `superseded` | A later attempt replaced this attempt as the current candidate. |

---

## 6. Checkpoint Contract

Checkpoints are durable artifacts that make reattempts and Resume truthful.

A checkpoint records enough information to restore or validate the state at a boundary without parsing terminal logs or reconstructing state from UI projections.

Representative checkpoint:

```json
{
  "schemaVersion": "v1",
  "checkpointKind": "step_boundary",
  "source": {
    "workflowId": "mm:task",
    "runId": "temporal-run-id",
    "logicalStepId": "implement-story-S004",
    "attempt": 2
  },
  "taskInputSnapshotRef": "art_original_task_input",
  "planRef": "art_plan",
  "planDigest": "sha256:...",
  "preparedInputRefs": ["art_prepared_inputs"],
  "workspace": {
    "kind": "git_worktree",
    "branch": "001-normalize-proposal-submissions",
    "baselineCommit": "abc123",
    "headCommit": null,
    "dirty": true,
    "diffRef": "art_attempt_2_diff"
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

### 6.1 Checkpoint boundaries

MoonMind should create or update checkpoint evidence at these boundaries:

1. after prepare succeeds;
2. before a mutating step attempt starts, when a restorable baseline exists;
3. after a step attempt completes;
4. after quality gates complete;
5. before publication or external state transitions;
6. before Resume restoration executes any new work.

Checkpoint writes must be idempotent because activities and workflow tasks may retry.

### 6.2 Checkpoint validation

Before a checkpoint can be used to start a new attempt or Resume execution, MoonMind must validate:

1. source `workflowId` and `runId`;
2. task input snapshot identity;
3. plan identity and digest;
4. logical step identity;
5. attempt provenance;
6. artifact existence and authorization;
7. workspace, branch, and commit consistency;
8. policy eligibility for replaying or preserving side effects.

If validation fails, MoonMind must fail explicitly before launching an agent or mutating the workspace.

---

## 7. Workspace and Git Policy

Repeated coding work is not purely functional. Each attempt may leave a changed workspace.

MoonMind must record a workspace policy before launching a repeated attempt.

Canonical policies:

| Policy | Meaning | Typical use |
|--------|---------|-------------|
| `continue_from_previous_attempt` | Keep the prior attempt's working tree and ask the next attempt to repair it. | Mostly good diff with failing tests. |
| `restore_pre_attempt` | Restore the workspace to the checkpoint from before the failed attempt. | Unsafe, messy, or broad failed attempt. |
| `apply_previous_diff_to_clean_baseline` | Reset to a clean baseline, then apply the previous attempt diff as an explicit patch artifact. | Preserve useful work while removing hidden drift. |
| `start_from_last_passed_commit` | Start from the latest committed accepted step state. | Autonomous story loop after a failed story attempt. |
| `fresh_branch_from_source` | Start a new branch/worktree from the source ref. | Full retry or unsafe workspace state. |

The selected policy must be visible in attempt metadata and operator diagnostics.

### 7.1 Git effect states

Every mutating attempt should classify its git effect.

| Disposition | Meaning |
|-------------|---------|
| `accepted` | Attempt changes are committed or otherwise durable and approved to advance the logical step. |
| `candidate` | Attempt produced useful changes but did not pass gates. Diff/checkpoint evidence is retained. |
| `discarded` | Attempt changes are intentionally not carried forward. Evidence remains. |
| `superseded` | A later attempt replaces this attempt's candidate state. |
| `none` | Attempt did not produce workspace changes. |

Representative git effect:

```json
{
  "baselineCommit": "abc123",
  "headCommit": null,
  "workingTreeDiffRef": "art_diff_attempt_2",
  "commitCreated": false,
  "workspaceCheckpointRef": "art_workspace_after_attempt_2",
  "disposition": "candidate"
}
```

After a passing attempt:

```json
{
  "baselineCommit": "abc123",
  "headCommit": "def456",
  "workingTreeDiffRef": "art_diff_attempt_3",
  "commitCreated": true,
  "disposition": "accepted"
}
```

MoonMind must not treat an uncommitted dirty workspace as an accepted state.

---

## 8. Gated Iteration

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
4. which downstream steps are skipped when gates fail;
5. final publication eligibility.

### 8.1 Gate verdict contract

Gate-producing steps should return structured verdicts.

Suggested verdicts:

| Verdict | Meaning |
|---------|---------|
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
  "remainingWorkRef": "art_remaining_work",
  "blockingEvidenceRefs": ["art_verify_report"],
  "recommendedNextAction": "reattempt_current_step",
  "recoverableInCurrentRuntime": true
}
```

The parent workflow must branch on structured gate results instead of parsing prose summaries.

### 8.2 Budgets and stop rules

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

## 9. Artifact, Retrieval, and Memory Side Effects

### 9.1 Artifacts

Attempt artifacts are append-only evidence.

Typical attempt artifacts:

1. attempt context bundle;
2. prepared input refs;
3. agent prompt or instruction refs;
4. agent result summary;
5. stdout, stderr, diagnostics, and event journals;
6. working tree diff or patch;
7. workspace checkpoint;
8. quality gate report;
9. browser or screenshot verification evidence;
10. retrieval manifest;
11. memory proposals.

The current logical step may point at the latest attempt, but prior attempt artifacts remain linked and inspectable.

### 9.2 Retrieval context

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

If two attempts see different retrieved context, the difference must be recorded through their context bundle or retrieval manifest refs.

### 9.3 Memory effects

Memory side effects must have promotion states.

Suggested states:

| State | Meaning |
|-------|---------|
| `proposed` | Attempt generated a possible memory update. |
| `accepted_for_run_context` | Memory is visible to later attempts in the same run. |
| `applied_to_repo` | Memory was committed or otherwise written to durable repo instructions. |
| `rejected` | Memory proposal was intentionally not used. |
| `superseded` | A later proposal replaced this one. |

Failed or abandoned attempts must not silently write durable repo memory. Run-local memory may be accepted for later attempts when policy allows it, but durable repo instruction changes require explicit policy and normal publication gates.

---

## 10. Resume Relationship

Failed-step Resume is one consumer of Step Attempts with Checkpointing.

Resume does not continue the old failed step in place. Resume creates a linked follow-up execution and starts new work by creating a new Step Attempt for the failed logical step.

Resume attempt properties:

1. `reason = resume_from_failed_step`;
2. `sourceAttempt` points to the failed attempt in the source run;
3. `sourceWorkflowId` and `sourceRunId` are pinned;
4. completed prior steps are imported as preserved progress from refs;
5. the workspace is restored from a validated checkpoint;
6. the failed step is the first newly executed logical step;
7. later steps execute normally after the failed step succeeds.

Resume must not:

1. allow silent task input edits in the failed-step Resume path;
2. silently fall back to full rerun;
3. re-execute preserved prior steps unless a future explicit mode requests it;
4. ignore missing, corrupted, unauthorized, or inconsistent checkpoint evidence.

---

## 11. Managed Runtime Relationship

Managed runtimes execute attempts; MoonMind owns attempt semantics.

For a managed agent runtime attempt, `MoonMind.Run` should:

1. create the Step Attempt record;
2. prepare and record the context bundle;
3. ensure workspace policy has been applied;
4. launch or reuse the appropriate `MoonMind.AgentRun` boundary;
5. pass compact refs and instructions to the runtime;
6. collect runtime output refs;
7. run or schedule quality gates;
8. classify side effects;
9. update the step ledger projection.

An agent may recommend another attempt, but it must not unilaterally create hidden attempts or mutate the attempt ledger.

External or coordinated agents may have weaker runtime control. In that mode, MoonMind should still record attempt identity, context refs, known side effects, and checkpoint evidence where available. Features that require direct workspace restoration may be unavailable or require a different policy.

---

## 12. Operator and API Surfaces

The default task detail view should stay simple:

1. each logical step shows the latest/current attempt;
2. attempt count is visible;
3. blocked or failed gate summaries are visible;
4. the latest evidence refs are accessible.

Expanded surfaces should allow operators to inspect attempt history:

1. attempt number;
2. reason;
3. source attempt;
4. runtime child refs;
5. workspace policy;
6. git disposition;
7. quality gate verdict;
8. output, diagnostics, and diff refs;
9. terminal disposition.

APIs should expose bounded attempt projections and artifact refs. They must not inline large transcripts, diffs, provider payloads, or verification reports.

---

## 13. Failure and Stop Semantics

When an attempt fails or a gate requests more work, MoonMind must classify the next state.

Suggested terminal dispositions:

| Disposition | Meaning |
|-------------|---------|
| `accepted` | Attempt passed gates and may advance the logical step. |
| `retryable` | Another attempt is allowed under budget and policy. |
| `blocked` | Missing prerequisite, credential, infrastructure, or approval prevents progress. |
| `needs_human` | Automated attempts are exhausted or unsafe. |
| `discarded` | Attempt evidence is retained but workspace effects are not reused. |
| `superseded` | A later attempt replaced this attempt. |

When a downstream step depends on a passing gate, the parent workflow must skip or block that downstream step if the gate fails. Publication and external state transitions must not rely only on the downstream agent noticing the failed gate.

---

## 14. Security and Side-Effect Guardrails

Step Attempts with Checkpointing must preserve MoonMind's security posture.

Rules:

1. Attempt context bundles must not include raw credentials.
2. Artifact refs must respect normal authorization checks.
3. Workspace restore operations must be scoped to the run workspace or approved worktree.
4. External side effects must be idempotent or guarded by durable keys.
5. Non-idempotent external work must not be reattempted without explicit policy.
6. Publication, Jira transition, merge, and provider-account actions require gate-approved state.
7. Failed attempt logs and diagnostics must be sanitized before display or publication.
8. Repo and local skill sources remain untrusted input; attempt context must respect skill source policy.

---

## 15. Examples

### 15.1 Jira Orchestrate gated implementation

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

If the post-remediation gate remains `ADDITIONAL_WORK_NEEDED` after budget exhaustion, `MoonMind.Run` stops before pull request creation and Jira movement. The final state includes the latest verification report, remaining work, attempted remediation evidence, and a recommended next action.

### 15.2 Failed-step Resume

Desired behavior:

```text
source run:
  step 1 succeeded
  step 2 succeeded
  step 3 failed attempt 1

resume run:
  step 1 preserved from source refs
  step 2 preserved from source refs
  step 3 attempt 2 executes from validated checkpoint
  step 4 executes normally after step 3 passes
```

The resumed run does not reconstruct progress from logs and does not silently rerun steps 1 and 2.

### 15.3 Autonomous story loop

Desired behavior:

```text
story S004 attempt 1:
  implementation produced candidate diff
  tests failed
  diff retained as artifact

story S004 attempt 2:
  starts with policy apply_previous_diff_to_clean_baseline
  fixes tests
  verifier passes
  changes are committed
  logical story step is marked passed
```

The failed attempt remains visible as evidence even though its workspace state is not accepted.

---

## 16. Non-Goals

This design does not require:

1. infinite autonomous loops;
2. hiding attempts inside a single agent transcript;
3. storing large diffs or logs in workflow state;
4. treating Temporal activity retries as semantic reattempts;
5. automatically replaying every possible external side effect;
6. committing memory proposals from failed attempts directly to the repo;
7. making failed-step Resume and autonomous loops separate recovery systems.

---

## 17. Constitution Alignment

Step Attempts with Checkpointing supports MoonMind's core principles:

1. **Orchestrate, Don't Recreate**: agents execute attempts; MoonMind owns orchestration, state, and evidence.
2. **Own Your Data**: attempt context, outputs, diffs, and checkpoints are operator-controlled artifacts.
3. **Resilient by Default**: repeated work is explicit, bounded, idempotency-aware, and recoverable.
4. **Spec-Driven Development**: gates and attempts preserve traceability from requirement to implementation to evidence.
5. **Continuous Improvement**: failed attempts produce structured evidence and improvement signals rather than disappearing into logs.

The key desired-state rule is simple:

> A repeated step is not the same execution happening again. It is a new attempt with explicit context, checkpoint, side-effect policy, evidence, and stop rules.

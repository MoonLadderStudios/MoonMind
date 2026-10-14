# Step Executions and Checkpointing

Owners: MoonMind Engineering
Covers: semantic step reattempts, checkpointed side-effect policy, gated iteration, the failed-step recovery primitive, autonomous story loops
Related: `docs/Steps/StepTypes.md`, `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/WorkflowRemediation.md`, `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`, `docs/Temporal/ActivityCatalogAndWorkerTopology.md`, `docs/Artifacts/ArtifactPresentationContract.md`

---

## 1. Purpose

MoonMind workflow executions often need more than one pass: an implementation step fails tests, a verifier reports `ADDITIONAL_WORK_NEEDED`, a pull request resolver needs another repair, an autonomous story loop needs a fresh agent context, or an operator resumes a failed execution from its failed step. All of these rest on one primitive:

> Re-execute a logical step as a new attempt while explicitly controlling which previous side effects are preserved, restored, ignored, invalidated, promoted, or superseded.

This document describes that primitive — **Step Executions with Checkpointing** — and the gated iteration and recovery behavior built on it. Step Executions are execution-plane records, not authoring steps or Step Types (`docs/Steps/StepTypes.md`). The operator-facing ledger lives in `docs/Temporal/StepLedgerAndProgressModel.md`; start, new-run, and recovery semantics live in `docs/Workflows/WorkflowArchitecture.md` and `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`. Activity retry policy, artifact storage internals, and provider launch internals are out of scope.

---

## 2. Layering and Source of Truth

A plan contains **logical steps**; each may have several **Step Executions** (for example, attempt 1 fails a quality gate, attempt 2 fails integration evidence, attempt 3 passes and is accepted).

| Layer | Authority |
| --- | --- |
| Resolved plan artifact | Planned steps, order, dependencies, tool/skill descriptors |
| Workflow state | Live compact step/attempt state, loop state, budgets, child refs |
| Step ledger query | Read projection of workflow state for operators |
| Step execution manifest artifact | Immutable full evidence for one attempt |
| Checkpoint artifact/read model | Evidence needed to restore or validate a step boundary |
| Git/repo state | Accepted code once committed/published |
| App DB projection | Repairable read model only |

Attempt evidence is append-only, and workflow state carries compact refs and bounded summaries so histories stay small and replayable. The ledger shows the latest attempt by default; history is reachable through manifest refs. App DB projections sit downstream of workflow state, artifact linkage, and git state, so they never invent step or attempt truth.

The two evidence content types are `application/vnd.moonmind.step-execution+json;version=1` (`STEP_EXECUTION_MANIFEST_CONTENT_TYPE`) and `application/vnd.moonmind.step-execution-checkpoint+json;version=1` (`STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE`).

### 2.1 Manifest vs checkpoint

The **manifest** is the complete immutable evidence of one semantic execution — identity, lineage, context refs, outputs, checks, side effects, disposition — written at start and at terminal classification and read by attempt history and audits. The **checkpoint** is the minimal durable evidence needed to restore or validate state at one boundary, consumed by workspace policy application, Step Re-execution, and RecoverFromFailedStep.

The manifest references checkpoints (`workspace.checkpointBeforeRef`, `workspace.checkpointAfterRef`); checkpoints never embed manifests. Recovery requires a valid checkpoint, so a manifest without checkpoints is evidence only and cannot satisfy a policy that demands restorable state.

### 2.2 Plane-aware checkpoints

Policy callers select session state, workspace state, or both. Session evidence supports reattach or cold-session decisions; workspace evidence supports repository continuation and must satisfy the artifact contract, digest, and boundary map in the frozen workspace capability. A session `external_state_ref` never represents files, git metadata, or uncommitted changes, so Omnigent workspace boundaries use MoonMind `worktree_archive` checkpoints and `workspace.apply_checkpoint`.

The workflow derives the runtime capability snapshot from its selected runtime before launch, and replay consumes the recorded snapshot rather than a mutable registry lookup, keeping replay deterministic. Authored node inputs and step result `runtimeCapabilities` fields cannot override it or supply checkpoint Activity bindings. Retained histories keep their recorded decisions behind Temporal patch markers.

### 2.3 Durable capture and reads

Checkpoint Activities adapt Temporal artifact IDs (`art_...`, `artifact:art_...`, `artifact://art_...`) to the durable URI form of Omnigent checkpoint identities, preserve captured bytes, and compute digests from those bytes. Malformed or unavailable capture keeps a negative recovery-capability projection.

New checkpoint and workspace artifacts link to the actual Activity's workflow and run, and reads carry that trusted scope through normal artifact authorization, so caller-supplied identities cannot select another workflow or run. Checkpoint discovery reads the owning run's step ledger and rejects a ledger from another execution. That live read is best-effort: if Temporal history is unavailable it falls back to checkpoints already persisted in the memo, parameters, or finish summary, while a mismatched or malformed ledger remains an error. These adapters never repair historical evidence; recovery still requires its original input, plan, integrity, capability, side-effect, and cleanup evidence.

---

## 3. Terminology

- **Logical Step** — stable plan node identified by `logicalStepId`.
- **Step Execution** — one semantic execution, keyed by `(workflowId, runId, logicalStepId, executionOrdinal)`.
- **Lineage Execution Ordinal** — optional cross-run ordinal for display, such as "recovered execution 2" when a recovery run starts local execution 1.
- **Checkpoint** — durable evidence sufficient to restore or validate state at a step boundary.
- **Context Bundle** — immutable per-execution artifact describing instructions, inputs, retrieval, memory, prior evidence, and policy.
- **Workspace Policy** — explicit rule for the workspace/git state the next execution starts from.
- **Gate** — structured verification whose verdict decides whether work advances, repeats, stops, or needs attention.
- **Preserved Step** — a completed prior step imported into a recovery execution as provenance and reusable output refs.
- **Invalidated Step** — a downstream step whose output is stale because an upstream accepted output changed.

```text
retry                 = same Step Execution, transient or idempotent operation retry
step re-execution     = new Step Execution, same logical step
recover failed step   = linked Workflow Execution that begins with a new Step Execution at the failed step
```

Payloads label these `retry`, `reexecute`, and `recover` (`StepExecutionSemanticOperation`). Broad Temporal workflow retries do not substitute for Step Executions when work may mutate a repository or external system, because a blind replay cannot account for effects already applied.

---

## 4. Core Invariants

1. Logical step identity is stable within the resolved plan; run-scoped execution ordinals increase monotonically per `(workflowId, runId, logicalStepId)`, and cross-run lineage never replaces them.
2. Every new semantic execution creates a new Step Execution, which declares its source execution, reason, and workspace policy before it begins.
3. The context bundle is digest-addressed and immutable once execution starts.
4. Evidence is append-only, large content stays in artifacts, and failed-attempt artifacts are kept even when their workspace changes are discarded.
5. Side effects are classified before the workflow advances.
6. An implementation step completes only with a committed/published code ref, an accepted artifact output, or an explicit accepted no-change disposition.
7. Publication, Jira movement, merge automation, and other handoffs are gated by structured workflow state, not agent self-assessment, because an agent can report success it has not verified.
8. Downstream steps that consumed a changed upstream output are invalidated or revalidated before reuse.
9. Resume never silently degrades to a full rerun when checkpoint validation or restoration fails.
10. Runtime adapters execute work; MoonMind owns execution identity, checkpoint policy, evidence refs, and advancement.
11. Every Checkpoint Branch turn is a new server-owned Step Execution: the caller supplies intent and MoonMind allocates identities before side effects. A continued or forked turn pins its parent turn's accepted output checkpoint and Step Execution, and replays reuse persisted identities rather than creating another execution.

### 4.1 Conformance harness

`moonmind.workflows.temporal.step_execution_conformance` runs fixture cases over the manifest, checkpoint, and gate contracts, alongside `tests/unit/workflows/temporal/test_step_executions.py`, `tests/unit/workflows/temporal/test_step_checkpoints.py`, and `tests/integration/workflows/temporal/test_step_execution_manifest_evidence.py`. Its degraded-input fixtures — old manifest and checkpoint rows, old gate verdict strings, legacy `stateCheckpointRef`-only rows, and future or unknown gate verdicts and checkpoint policy values — must each produce a typed fail-closed result rather than crashing replay or silently passing. The fixtures are extended when a change touches the checkpoint capture artifact, checkpoint-backed Resume, typed gate result, context bundle or retrieval manifest, provider-profile lease side effect, or Docker or skill-projection blocked environment. Manifest, checkpoint, gate, API, and projection surfaces stay artifact-backed or ref-only with bounded summaries; they never inline raw stdout, stderr, diffs, logs, provider payloads, or credentials.

---

## 5. Identity, Lineage, and Idempotency

### 5.1 Run-scoped identity

```text
stepExecutionId   = {workflowId}:{runId}:{logicalStepId}:execution:{executionOrdinal}
childWorkflowId   = {workflowId}:agent:{logicalStepId}:execution:{executionOrdinal}
checkpointId      = {stepExecutionId}:checkpoint:{boundary}
artifactLinkScope = step:{logicalStepId}:execution:{executionOrdinal}
```

A manifest or checkpoint whose ID does not match its identity fields is rejected at the model boundary rather than repaired downstream. Identifiers exposed externally are sanitized or hashed as needed.

### 5.2 Cross-run lineage

A recovery execution usually starts the failed step at local ordinal `1`, carrying a `lineage` object (`reason`, `sourceWorkflowId`, `sourceRunId`, `sourceLogicalStepId`, `sourceExecutionOrdinal`, `lineageExecutionOrdinal`, `relationship = recover_from_failed_step`). The UI can render "Step 3 — resumed attempt 2, local attempt 1 in this run". The local key remains authoritative, and both source ids are pinned so lineage cannot drift.

`lineage.source*` and `preservedFrom.*` are deliberately distinct: the first lives on a **newly executed** Step Execution and points at the attempt that caused it; the second lives on a **preserved step row** and points at the execution whose accepted output is reused (section 12).

### 5.3 Idempotency keys

Side-effecting Activities derive keys as `{stepExecutionId}:{operation}` — for example `:manifest`, `:checkpoint:{boundary}`, `:agent-run`, `:gate:{gateName}`, `:commit`, `:publish-pr`, `:jira-transition:{targetStatus}`. Builders reject a blank operation. Temporal activity attempt numbers may be logged but are not business keys, because they reset across semantic attempts and recovery runs.

---

## 6. Step Execution Manifest

The manifest model lives in `moonmind/schemas/step_execution_models.py`; the workflow keeps only a compact projection. It is written at start (status `executing`, or `blocked` with `terminalDisposition = "blocked"` when workspace-policy launch validation rejects the attempt) and at terminal classification. Each write is a new immutable artifact under the same identity, and the ledger tracks the latest ref and ref history. All writes go through `_record_step_execution_manifest(...)` in `moonmind/workflows/temporal/workflows/run.py`, so one identity never has competing manifests.

Fields cover identity and lineage; reason, status, and terminal disposition; input (task snapshot, plan ref and digest, prepared inputs); context (bundle ref and digest, builder version, retrieval and memory manifests); workspace (baseline, policy, before/after checkpoint refs); execution (kind, child workflow/run, runtime id, runtime context policy); output refs; checks; side effects (git, external, artifacts, memory, retrieval); dependency effects; and budget.

**Reasons** are bounded metadata: `initial_execution`, `quality_gate_failed`, `tests_failed`, `runtime_recovered`, `recover_from_failed_step`, `remediation_context`, `operator_requested`, `dependency_invalidated`, `policy_revalidation`. Rich explanation belongs in artifacts.

**Statuses**: `pending`, `preparing`, `executing`, `checking`, `completed`, `failed`, `blocked`, `canceled`, `superseded`.

**Terminal dispositions** (`StepExecutionTerminalDisposition`):

| Disposition | Meaning |
| --- | --- |
| `accepted` | Passed gates; may advance the logical step. |
| `retryable` | Another attempt is allowed under budget and policy. |
| `blocked` | Missing prerequisite, credential, infrastructure, or approval. |
| `needs_human` | Automated attempts are exhausted or unsafe. |
| `discarded` | Evidence kept; workspace effects not reused. |
| `superseded` | Replaced by a later attempt. |
| `failed_unrecoverable` | Permanent blocker or unsafe condition. |
| `failed_with_remaining_work` | Stopped under budget with known remaining work and published evidence. |

---

## 7. Context, Retrieval, and Memory

The context bundle is the immutable input envelope an agent, tool, or verifier sees: identity, reason, input and plan refs, the workspace policy and baseline, prior evidence refs (such as the previous gate report and diff), retrieval and memory manifests, runtime and skill selection, and quality gate profile. It holds refs and summaries, never raw credentials or large logs. A digest and builder version let operators diagnose context drift between attempts.

Retrieval inputs are attempt inputs, not ambient state. MoonMind does not ship a native retrieval index; attempts use explicit attachments, artifact refs, and scoped workspace access. The recorded retrieval context shape remains readable so existing attempts keep their evidence.

Memory side effects move through `proposed`, `accepted_for_run_context`, `applied_to_repo`, `rejected`, and `superseded`. Failed or abandoned executions do not write durable repo memory, and durable instruction changes require explicit policy and normal publication gates, so a failed attempt cannot quietly change how future runs behave. Run-local memory may be shared with later attempts when policy allows.

---

## 8. Checkpoints

A checkpoint records enough to restore or validate state at a boundary without parsing logs or reconstructing state from UI projections. It carries its id, kind, boundary, source identity, task input and prepared input refs, `planRef` or `planDigest` (one is required), a workspace section (kind, base commit, patch or archive refs), and step output refs. Inline `stdout`, `stderr`, `diff`, `logs`, `content`, or provider output is rejected at the model boundary, keeping large or binary evidence out of workflow history. Writes are idempotent because Activities and workflow tasks retry.

### 8.1 Boundaries

`StepExecutionCheckpointBoundary`: `after_prepare`, `before_execution` (when a restorable baseline exists), `after_execution`, `after_gate`, `before_publication`, and `before_recovery_restoration`.

Adjacent boundaries reuse workspace evidence only within one explicit workspace-mutation generation, and every boundary still gets its own checkpoint. Immediately before preflight or execution can mutate the authoritative workspace, the workflow advances the generation so the next checkpoint captures fresh evidence. Identical checkpoint bytes share a content-addressed blob while keeping per-boundary artifact rows and retention.

For Omnigent, the canonical writer attaches one versioned `omnigentCheckpoint` object to the Step Execution checkpoint. Capture happens after workspace preparation and before the first message, after each completed turn or step, before controlled drain/reset, before recovery or Checkpoint Branch creation, and during terminal harvest when recoverable state remains; each trigger maps to the nearest canonical boundary rather than an adapter-owned lane. The object keeps four authority planes separate:

- **Session** — `externalStateRef` and digest, bridge/session/host ids, idempotency key, committed bridge cursor, first-message id and digest, terminal and diagnostics refs.
- **Workspace** — `WorkspaceLocator`, baseline commit, head/diff/checkpoint refs and digests, branch and publication state.
- **Host realization** — endpoint, execution profile, launch policy and effective-launch snapshot, Provider Profile, lease and binding refs, capture manifests, patch capability.
- **Credentials** — reference and generation only, never a credential body, OAuth home, or volume content.

It also pins lineage, immutable instruction/context refs, capture time, and producer version. A capability is `true` only when every evidence class it needs is independently resolvable; partial capture records bounded reason codes and leaves the capability false.

### 8.2 Workspace checkpoint kinds

| Kind | Restore strength |
| --- | --- |
| `git_commit` | Strong for tracked code. |
| `git_patch` (against a base commit, optional untracked manifest) | Good for candidate work; weaker for binary, submodule, or ignored state. |
| `worktree_archive` | Stronger for dirty worktrees and untracked files; larger. |
| `ephemeral_workspace_ref` | Valid only within TTL/reachability. |
| `external_state_ref` | Adapter-specific; may not support direct restoration. |

Worktree archives store Git's index as a digested binary patch relative to the saved HEAD, so partially staged files keep both staged and unstaged bytes; index and patch bytes pass the same bounded secret scan as other evidence. A staged-path list without index content cannot authorize reconstruction from worktree bytes.

Capture and recovery select a child workspace; the shared `temporal_sandbox` store itself is never a capture or replacement target. A selected commit that touches a credential-sensitive path under the shared exclusion policy cannot be exported even with heuristic scanning disabled; capture rejects the bundle rather than rewriting history.

Resuming finalization validates a surviving directory against saved file digests, HEAD, index, and Git status, because a directory's existence does not prove candidate identity. After cleanup releases provider authority, durable turn receipts can resume verification and publication without another provider turn.

### 8.3 Policy requirements and validation

| Workspace policy | Minimum checkpoint evidence |
| --- | --- |
| `restore_pre_execution` | `git_commit`, `worktree_archive`, or equivalent from before the execution |
| `continue_from_previous_execution` | Live workspace ref or after-execution checkpoint |
| `apply_previous_execution_diff_to_clean_baseline` | `git_patch` plus base commit |
| `start_from_last_passed_commit` | Accepted commit SHA or published ref |
| `fresh_branch_from_source` | Source repo/ref and branch creation policy |

Missing evidence rejects the policy before launch. Before a checkpoint starts an attempt or Resume, MoonMind validates source ids, input and plan identity, logical step and attempt provenance, artifact existence and authorization, workspace/branch/commit consistency, kind-to-policy compatibility, and side-effect eligibility. Omnigent restore additionally digest-checks each artifact, checks repository baseline/head compatibility, and requires the current Provider Profile and credential generation to match; provider URLs, local paths, container ids, and provider-native ids are never artifact authority. Session reattach, workspace cold restore, and branch creation are evaluated independently with bounded denial reasons.

Checkpoint Branch launch runs this validation — plus branch/turn lineage, expected branch-head version, instruction digest, isolated git binding, and stored policy and snapshot checks — before acquiring a lease, mutating a host or repository, creating a session, or posting a message. The source workflow input and outcome stay immutable.

Failures are typed (`StepCheckpointValidationFailureCode`: `source_mismatch`, `task_input_mismatch`, `plan_mismatch`, `step_mismatch`, `execution_mismatch`, `artifact_missing`, `artifact_unauthorized`, `artifact_corrupted`, `workspace_mismatch`, `checkpoint_kind_incompatible`, `policy_incompatible`, `invalid_checkpoint`), and MoonMind fails with the code in diagnostics before launching an agent or touching the workspace.

---

## 9. Workspace and Git Policy

Each coding attempt may leave a changed workspace, so a policy is recorded and visible before a repeated attempt launches:

- `continue_from_previous_execution` — repair the prior working tree (mostly good diff, failing tests).
- `restore_pre_execution` — restore the pre-attempt checkpoint (unsafe or messy attempt).
- `apply_previous_execution_diff_to_clean_baseline` — reset, then apply the prior diff as an explicit patch, keeping useful work without hidden drift.
- `start_from_last_passed_commit` — start from the latest accepted commit (story loops).
- `fresh_branch_from_source` — new branch/worktree from the source ref.

Every mutating execution classifies its git effect as `accepted`, `candidate` (useful but ungated; diff retained), `discarded`, `superseded`, or `none`, with baseline/head commits, diff and patch refs, and checkpoint ref. An uncommitted dirty workspace is never accepted, because it cannot be reproduced or published reliably. A logical implementation step completes only with a commit SHA, pushed ref, gate-accepted non-git artifact, or explicit no-change disposition; in story loops a failed attempt yields only a diff artifact and a passing one yields a commit and advancement.

---

## 10. Side Effects and Activities

| Class | Reattempt posture |
| --- | --- |
| `workspace_mutation` | Needs workspace policy and checkpoint evidence. |
| `artifact_write` | Append-only; retry-safe through idempotent IDs or content hashes. |
| `external_idempotent` | Allowed when the durable key is stable and repetition is safe. |
| `external_non_idempotent` | Not repeated in autonomous reattempts without explicit policy. |
| `publication` | Branch/PR/merge handoff; requires gate-approved state. |
| `provider_account` | Requires provider-profile policy and release semantics. |
| `memory_update` | Requires promotion state and policy. |
| `retrieval_index_update` | No current producer; kept so recorded attempts retain their classification. |

A recorded side effect has a class, operation, idempotency key, target, and disposition. When a non-idempotent effect already occurred, a later attempt accounts for it rather than pretending the step can be reset, and compensation is explicit, idempotent, and observable.

`MoonMind.UserWorkflow` orchestrates; Activities own side effects. Workflow code does not read files, run git, inspect artifacts, call providers, or mutate memory, because those are nondeterministic and break replay. Activity results are compact and typed, with large outputs returned as artifact refs. Relevant Activities include `step_checkpoint.create`/`.validate`, `workspace.capture_checkpoint`, `workspace.apply_policy`, `workspace.apply_checkpoint`, `workspace.classify_git_effect`, and `memory.evaluate_proposals`/`.apply_policy`; queues are in `docs/Temporal/ActivityCatalogAndWorkerTopology.md`.

---

## 11. Gated Iteration

A gated loop is workflow-owned repetition: verify; while the verdict is `ADDITIONAL_WORK_NEEDED` and budget remains, run a new remediation attempt with the latest report and a new verification attempt; continue to publication on `FULLY_IMPLEMENTED`, otherwise stop with remaining work and evidence. Agent instructions govern what happens inside an attempt; the parent workflow decides whether another attempt is allowed, what repeats, how verdicts map to outcomes, which downstream steps are skipped or invalidated, and publication eligibility.

| Verdict | Meaning |
| --- | --- |
| `FULLY_IMPLEMENTED` | Passed; publication may proceed. |
| `ADDITIONAL_WORK_NEEDED` | More bounded work may be attempted within budget. |
| `NO_DETERMINATION` | Insufficient evidence; retried only when recoverable in the current runtime or when the verifier asks for `reattempt_current_step`. |
| `BLOCKED` | External or policy condition prevents progress. |
| `FAILED_UNRECOVERABLE` | Permanent blocker or unsafe condition. |

Gate results also carry confidence, validated and invalidated refs, remaining-work and blocking-evidence refs, `recommendedNextAction`, target step, workspace policy recommendation, and `recoverableInCurrentRuntime`; the workflow branches on these fields rather than prose.

Loops have explicit budgets (attempts, wall-clock time, spend, no-progress attempts, repeated failed commands, policy-denied attempts). Loop manifests record the attempt budget and at least one other stop dimension with limit, consumed, remaining, and exhausted state. On exhaustion MoonMind stops with `needs_human`, `blocked`, or `failed_with_remaining_work` and publishes the latest evidence and recommended next action.

### 11.1 Preset remediation loops

Issue Implement, GitHub Issue Orchestrate, and Jira Orchestrate declare one workflow-owned remediation loop (`annotations.remediationLoop`, controller role `moonspec-remediation-loop`, implemented in `moonmind/workflows/temporal/remediation_loop.py`) after their initial read-only verification, rather than open-ended agent instructions or copied repair steps. An initial `FULLY_IMPLEMENTED` materializes no attempt; each admitted attempt materializes one remediation and one read-only verification Step Execution, up to `hardMaxAttempts` (six by default). MoonSpec Orchestrate is a vendored projection ending with a single verification step. The run workflow still routes plans with pre-expanded steps annotated `jiraOrchestrateRole = "moonspec-remediation"` / `"moonspec-verification-gate"`; a plan cannot mix those with a loop declaration.

Loop tools are authored as `auto`, a planning-time sentinel meaning "inherit the run's runtime selection": each attempt names the run's resolved runtime, model, effort, and provider profile, so attempts route like the run's authored steps. A loop with no resolved controller runtime fails at admission rather than routing to a substitute.

On Omnigent, where each Step Execution starts in a fresh sandbox, post-loop steps restore the verified candidate (the loop head, or the latest implementation archive when no attempt ran). Issue Implement's pull-request handoff restores it directly; the Orchestrate presets run doc reconciliation from it and publish the reconciliation archive, or the candidate when reconciliation left none.

`ADDITIONAL_WORK_NEEDED` is retryable only while a later remediation step remains. Otherwise, in `pr` publish mode, the workflow publishes a draft pull request with `attention_required: true`, completes, and skips downstream promotion such as moving Jira. The same draft handoff applies to environment-class `BLOCKED` and malformed `NO_DETERMINATION`; verifier-declared `NO_DETERMINATION` and `FAILED_UNRECOVERABLE` stop before publication. The draft is a recoverability handoff carrying the latest report, remaining work, and recommended next action — not success evidence.

---

## 12. Dependency Invalidation and Preserved Outputs

A repeated attempt can change a step's accepted output, so downstream inputs resolve to a specific producing Step Execution rather than an ambiguous "latest". When a newly accepted execution changes semantic outputs, dependent steps become `pending`, `blocked`, `superseded`, or `requires_revalidation`, and the change is recorded as `changedOutputs`, `invalidatedLogicalStepIds`, and `revalidationRequired`. For example, if step 2 replaces API contract v1 with v2, step 3's UI built against v1 must reattempt or revalidate before verification can pass.

Preserved steps in recovery carry `preservedFrom.workflowId`, `.runId`, `.logicalStepId`, and `.executionOrdinal`, and are reused only when the checkpoint proves they still satisfy downstream contracts. A missing, unauthorized, corrupted, or plan-mismatched preserved output fails Resume before new work runs. Without these rules MoonMind could present downstream work as valid when it was built against a superseded upstream attempt.

---

## 13. RecoverFromFailedStep

"Resume" here means RecoverFromFailedStep (`docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`); RecoverFromSelectedStep follows the same rules from an operator-chosen step. Resume does not continue the failed step in place. It creates a linked execution whose first new work is the failed step as a local Step Execution with `reason = recover_from_failed_step`, pinned source ids, and lineage. Prior completed steps are imported as preserved progress, the workspace comes from a validated checkpoint, and later steps run normally subject to invalidation:

```text
Step 1: Prepare repo — completed, preserved from source run
Step 2: Implement API — completed, preserved from source run
Step 3: Run tests — resumed attempt 2, local attempt 1 executing now
```

Resume does not allow silent input edits, fall back to a full rerun, re-execute preserved steps, ignore bad checkpoint evidence, or conflate local identity with lineage; each would make the recovered run claim progress it cannot prove.

### 13.1 Recovery manifest

Every failed run emits a recovery manifest (`reports/recovery_manifest.json`, `application/vnd.moonmind.failed-run-recovery+json;version=1`, referenced from the finish summary) before terminal failure is reported. It names the last accepted step, the failed step and ordinal, checkpoint refs by boundary, the validation result (`valid`, `missing`, `corrupted`, `unauthorized`, `incompatible`, `invalid`, `not_evaluated`), side-effect dispositions (`accepted`, `discarded`, `blocked`, `needs_compensation`), and resume allowance with a blocked reason. `resumeAllowed` is true only for a `valid` result with a checkpoint ref; otherwise eligibility falls back to `full_retry` (or `environment_fix`) rather than presenting a rerun as resume. Restore-time re-validation still runs.

A workflow-owned check that rejects a step before launch still records it as a failed Step Execution with an allocated ordinal. Otherwise the manifest reports `no_failed_step_execution_to_resume` and the run loses checkpoint recovery even when an earlier accepted attempt captured valid checkpoints. Since such a step captured no workspace, the capability snapshot and checkpoint kind come from the step whose checkpoint is restored, chosen by the same failed-step-then-last-accepted-step rule; mixing steps leaves resume ineligible with `CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING`.

### 13.2 Incident reconstruction manifest

Every failed run also emits `reports/incident_reconstruction.json` (`application/vnd.moonmind.incident-reconstruction+json;version=1`, summarized as `incidentReconstruction`, memo key `incident_reconstruction_ref`), built after and reusing the recovery manifest. It lets an operator see what happened without reading worker internals by correlating the resilience policy ref, provider/profile/credential source and sanitized failure event (raw payloads only behind `rawErrorRef`), failed step and ordinal, progress, workspace change refs, side-effect dispositions and restore candidate, cost attribution and observed usage where reported, trace spans across API, workflow, Activity, provider, and artifact boundaries, and log and artifact refs. A replay-safe trace id derived from workflow/run identity joins these and appears on each step manifest as `traceRef`. Every category is present with a locator or named absent with a reason, and secrets and raw payloads are redacted or artifact-gated.

---

## 14. Managed Runtimes

For a managed agent attempt, `MoonMind.UserWorkflow` creates the Step Execution, prepares the context bundle, applies the workspace policy, launches or reuses `MoonMind.AgentRun` per runtime context policy, collects output refs, runs gates, classifies side effects, and updates ledger and manifest. An agent may recommend another attempt but does not create hidden attempts or edit the ledger.

`runtimeContextPolicy` (`moonmind/schemas/agent_runtime_models.py`) is separate from workspace policy: `fresh_agent_run` (new child workflow), `reuse_session_new_epoch` (reuse the managed session after resetting it), `reuse_session_same_epoch` (rare, explicit continuity), and `external_provider_continuation` (provider-controlled state). Clean-context loops usually combine `fresh_agent_run` with an epoch reset, implemented by the runtime as a new thread, cleared context, or replacement container. External agents allow weaker control, but MoonMind still records identity, context refs, known side effects, and available checkpoints.

---

## 15. Operator Surfaces, Security, and Failure Semantics

The default detail view shows each step's latest attempt, attempt count, gate summaries, latest evidence, and preserved/resumed provenance. Expanded views and the API expose bounded attempt history and refs, never large transcripts or diffs:

```http
GET /api/executions/{workflow_id}/steps
GET /api/executions/{workflow_id}/steps/{logical_step_id}/step-executions
GET /api/executions/{workflow_id}/steps/{logical_step_id}/step-executions/{execution_ordinal}
```

When a gate fails, the parent workflow skips, blocks, invalidates, or revalidates dependent steps itself rather than relying on a downstream agent to notice.

Security: bundles, manifests, checkpoints, and memory proposals contain no raw secrets; failed-attempt diagnostics are sanitized before display; artifact refs go through normal authorization; restores stay within the run workspace or approved worktree and reject path traversal; external effects are idempotent or key-guarded; publication, Jira, merge, deployment, and provider-account actions require gate-approved state; and repo and local skill sources are treated as untrusted input.

---

## 16. Versioning and Compatibility

Schemas are versioned through their content types. Writers fail fast on unsupported values, while read and replay boundaries turn degraded, blank, unknown, or future values from persisted payloads into typed invalid decisions instead of crashing workflow tasks, so in-flight histories stay replayable. Additive `v1` fields are optional or default-initialized and enum growth is additive. Renaming or removing a field, changing identity or key shapes, or changing disposition semantics requires a new content-type version, with the old version removed rather than aliased. Payload changes carried through workflow state are covered by compatibility or replay-style tests at the workflow boundary.

> A repeated step is not the same execution happening again. It is a new attempt with explicit context, checkpoint, side-effect policy, evidence, lineage, and stop rules.

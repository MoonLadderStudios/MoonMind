# MoonMind Workflow Run History and New Run Semantics

Last updated: 2026-10-17

**Document class:** Canonical declarative lifecycle contract.
**Status:** Normative
**Owner:** MoonMind Platform.
**Audience:** Backend, dashboard, workflow authors, and API owners.

## 1. Purpose

This document defines logical Workflow identity, run history, rerun, retry from a step, and Continue-As-New. These are different operations, not interchangeable meanings of Resume.

Retry from a step resets a closed run's Temporal history to the start of a chosen step. Earlier steps keep their recorded results and effects; the chosen step and everything after it run again on the installed code under the same `workflowId`, with the run's original authored input. Corrective instructions or changed execution authority instead require an edited fresh execution or an explicitly admitted Checkpoint Branch.

## 2. Related docs

- [Workflow Execution Product Model](WorkflowExecutionProductModel.md)
- [Temporal Architecture](TemporalArchitecture.md)
- [Source of Truth and Projection Model](SourceOfTruthAndProjectionModel.md)
- [Visibility and UI Query Model](VisibilityAndUiQueryModel.md)
- [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md)
- [Checkpoint Branch System](../Workflows/CheckpointBranchSystem.md)
- [Workflow Remediation](../Workflows/WorkflowRemediation.md)
- [Workflow Runs API](../Workflows/WorkflowRunsApi.md)
- [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md)

## 3. Scope and non-goals

### 3.1 In scope

This contract owns operation meaning, source/destination lineage, retry admission, and truthful UI/API outcome presentation. Checkpoint bytes, filesystem restoration, provider sessions, approvals, publication effects, and cleanup remain with their existing owners.

### 3.2 Out of scope

No universal recovery coordinator, duplicate workspace store, second runtime registry, mandatory per-run navigation, or guarantee of exactly-once external effects is introduced. A model or endpoint existing in code is not evidence that an operation is executable.

## 4. Runtime and product ownership

MoonMind owns durable workflow/step identity, immutable input and plan, artifact/workspace authority, provider capacity, policy, publication, and cleanup. Omnigent owns live runtime mechanics through the generic execution plane; Codex, Claude Code, and OpenCode work shares those MoonMind contracts.

Temporal owns run history, so retry from a step is a Temporal reset that MoonMind admits, not a MoonMind-side copy of earlier progress into a new execution. Runtime/realizer and workspace owners perform any reattachment or restoration a step needs. Checkpoint Branch turns keep their existing durable execution owner, and publication-only work uses the existing publisher.

## 5. Canonical identifiers

### 5.1 Workflow ID

`workflowId` is the logical product route key. Continue-As-New and retry from a step retain it. A fresh rerun has a new Workflow ID and a relationship to its source.

### 5.2 Run ID

`runId` identifies an exact Temporal run instance. It is required for historical evidence and effect targeting even when the normal product route follows the latest run. It must come from the actual durable execution boundary, not a fabricated replacement in a projection.

### 5.3 Console routing identity

`/workflows/{workflowId}` follows the current logical execution by default, including the run a retry creates. Operators need not understand Temporal rollover to follow work. An explicitly selected historical result remains pinned and cannot be silently replaced by a later current run.

### 5.4 Naming caution

Keep logical Workflow identity, Temporal run ID, AgentRun identity, provider session, semantic Step Execution, and branch turn distinct. Retained historical wire names are decoded only under their supported version. Do not rename persisted fields or reinterpret digests merely to simplify UI wording.

## 6. Run history model

### 6.1 v1 decision

The normal detail view is one logical Workflow page following its current run. A latest-run projection is not a complete immutable run-history store.

### 6.2 What “run history” means in v1

Temporal histories and immutable artifact/Step Execution evidence supply historical provenance. The current projection may expose the latest run and logical timestamps. A Continue-As-New counter does not, by itself, count only user-requested reruns or prove business work was repeated.

Default artifact queries resolve the current run from current detail, not a stale list row. Source-linked repair artifacts instead retain their exact source and destination identities. A new current view cannot reconstruct unobserved past transitions.

### 6.3 What is not promised in v1

A browsable immutable history list, arbitrary historical-run routes, and per-run database snapshots are not implied by the default detail route. Preserve the historical evidence needed by actual recovery and retention contracts even where richer navigation is unavailable.

### 6.4 Future extension point

A history drawer or explicitly authorized run-history API may add navigation without changing the default logical route. It consumes existing evidence and cannot become a second lifecycle authority.

## 7. New run semantics

### 7.1 Meaning of `RequestRerun`

`RequestRerun` requests re-execution, not checkpoint restoration. Its valid lifecycle behavior depends on the supported workflow type and whether the source is still active.

| Operation | Source and destination | Guarantee |
| --- | --- | --- |
| Resume paused Workflow | Same supported live execution | Continue paused orchestration, not restore lost workspace |
| Continue session | Exact supported live canonical session/turn | Explicit same-session interaction under unchanged session authority |
| Active-run `RequestRerun` | Supported live owner may Continue-As-New | New run under that workflow's recorded protocol, not universally supported by every type |
| Terminal rerun / explicit fresh rerun | New Workflow ID linked to immutable source | Re-execute admitted source inputs from the beginning |
| Retry from a step | Same Workflow ID; Temporal reset creates a new run | Earlier steps are replayed, not repeated; the chosen step and its successors run again |
| Corrective Checkpoint Branch | New admitted semantic branch/turn | Isolated changed instructions or authority from authorized source content |
| Publication-only recovery | Existing publisher's admitted operation | Finish/reconcile publication without repeating completed compute |

The existing terminal `RequestRerun` service path creates fresh execution; it must not be documented as an impossible terminal update or as Continue-As-New performed by a closed run.

### 7.2 Required behavior

A supported active Continue-As-New is a real Temporal lifecycle transition. The result identifies the accepted operation and actual destination. API acceptance, projected state, and physical runtime completion remain separate.

A terminal rerun starts a fresh execution through normal admission, leaves the source closed, and returns its exact destination identity. The caller follows the returned identity rather than assuming `workflowId` stayed unchanged. An unsupported active-run control returns an explicit reason instead of manufacturing new projection IDs.

### 7.3 Input changes allowed with a new run

Exact rerun retains original input, selected Skill snapshot, and execution choices, subject to current permission, revocation, deployment, and support validation. It does not silently replace an unavailable Profile, model, configuration, or image.

Edited retry is explicit new authored work. Resolve its Runtime + one Profile and subordinate configuration through ordinary admission, freeze its new input/plan, and retain source lineage. Changed source/attachments/retrieval/publication authority is revalidated. Do not call this retry from a step, which never changes authored input.

### 7.4 State after a new run

The destination enters the actual planning/execution state appropriate to its supported contract. Rerun acceptance is not completed work. Link source and destination and preserve any pending preparation, capacity, or admission reason.

### 7.5 Terminal-state rule

Closed Temporal executions do not process ordinary updates. Terminal rerun is the service's explicit fresh-start operation, and retry from a step is a Temporal reset that starts a new run; neither mutates the closed run. Cancel/reconciliation may still finish outstanding owned resource cleanup without relabeling the original result. Never clear a source failure just to make a later repair look successful.

## 7A. Retry from a step

### 7A.1 Meaning

Retry from a step re-executes a failed, canceled, terminated, or timed-out `MoonMind.UserWorkflow` run from the start of a chosen step. MoonMind resets the run's Temporal history to the workflow task that started that step. Temporal replays the earlier history on the installed workflow code, so completed steps, their recorded results, and their confirmed effects are kept rather than repeated. The chosen step and every step after it run again, with the run's original task-input snapshot and admitted Omnigent execution plan. The failed run stays in Temporal history and the reset run becomes the workflow's current run.

Retry is distinct from rerun (a fresh execution of the authored input), Continue-As-New (history rollover), Pause/Resume, and same-session continuation. It never changes instructions, inputs, or execution choices; those need an edited rerun or a Checkpoint Branch.

### 7A.2 Choosing the step

The run workflow records the step's execution position and logical identity in memo when it starts. Ledger order can differ after dynamic loop expansion, so retry binds the boundary by logical identity; older histories supply that identity through the recorded AgentRun request. By default MoonMind restarts the earliest failed step named by the finish summary or ledger, else the last step that started. A reset after an unresolved earlier failure would replay that failure. The operator may choose any earlier step that started; an unstarted step cannot be chosen.

### 7A.3 Admission

```text
POST /api/executions/{workflowId}/retry-from-step
```

Before resetting, MoonMind requires that:

- the latest run is the run the operator saw (`sourceRunId`, refused as `run_changed`) and is closed as failed, canceled, terminated, or timed out (`run_not_failed`);
- the run is not a child owned by a parent workflow (`child_workflow`), because the parent owns its lifecycle;
- a GitHub issue claim remains unreleased and GitHub confirms ownership of an open issue with no blocking status or hold (`issue_claim_released`, `issue_claim_unavailable`). An expired lease additionally requires no later attempt before reacquisition; live claims retain normal contender ordering. Retry confirms `status: in-progress`, restoring it after expiry cleanup if needed, preserves attempt identity and retry allowance, and holds the local claim lock through reset;
- the history up to the chosen step replays against the installed workflow code (`history_not_replayable`), checked with the Temporal replayer before any reset, so an incompatible code change refuses the retry and leaves the failed run untouched;
- the history is still retained (`history_unavailable`).

The request's `idempotencyKey` is the reset's request id and is named in the reset reason, so repeating a request returns the same new run, even when Temporal accepted the reset before MoonMind saved its response. Signals and updates received after the reset point are not reapplied to the new run.

Issue leases cross remediation Continue-As-New boundaries. Before dispatch, older continuations that omitted the lease recover its identity through the Activity's recorded ancestry and require a confirmed renewal; they cannot revive an expired reservation automatically.

### 7A.4 What is not repeated

Steps before the chosen step are not re-executed: their activity and child-workflow results come from replayed history. Effects the chosen step or a later step performed before the failure, such as a pushed branch or an opened pull request, are not undone, and those steps meet that state when they run again. A run can be retried only while Temporal retains its history.

## 8. Continue-As-New outside a requested new run

History management may rotate a Temporal run without user-requested re-execution. Preserve the actual unfinished state, accepted steps, immutable selection, pending action/approval/restore/publication identities, budgets, candidate head, and cleanup obligations. Do not replay completed business work simply because the orchestration history rolled over.

Only the supporting workflow protocol may authorize major reconfiguration. Use deliberate versioning and representative recorded-history replay for changed command order, child/Activity identities, routing, or serialized decisions.

## 9. When to start a fresh Workflow ID instead

Use a new identity for terminal rerun, copied/edited work, and independently admitted authority. Retry from a step keeps the workflow's identity because it continues the same authored work. A Checkpoint Branch retains its own graph/turn identity and existing execution owner. Keep explicit source/result relations rather than implying all operations are the same logical run.

Same-session continuation and recovery into a fresh session remain different even if both preserve workspace content. A new runtime/account choice never silently attaches to old session authority.

## 10. UI and API contract

### 10.1 Detail routing

The normal page follows `workflowId`; historical evidence and action targets pin `runId`. Reject stale asynchronous results after navigation, logout, or target changes. An explicit historical selection must not follow latest automatically.

### 10.2 Detail rendering

Distinguish Continue session, Resume paused workflow, Retry from a step, Rerun, Edit for retry, Create repair branch, and Publish saved work when each is actually available. Do not render an action enabled from the presence of a checkpoint ref alone.

Use one backend eligibility/intent boundary across Workflow Detail, row actions, Create drafts, schedules, presets, API/MCP, and remediation. Revalidate when the action is submitted. Normal destination authoring remains Runtime + one Profile; subordinate configuration is resolved and frozen, not another required wizard.

### 10.3 List rendering

Continue-As-New and retry from a step retain the logical list row. Fresh reruns get their own linked row. Logical `startedAt`, current run timing, and last observation time must not be conflated. A projection outage or stale row is not permission to resubmit execution.

### 10.4 Execution API posture

`GET /api/executions/{workflowId}` supplies the current authorized view. The rerun and retry endpoints return their actual operation and destination run. Callers follow that response rather than infer Continue-As-New or success from an accepted flag.

Every operation has bounded typed rejection for stale identity, unavailable input or history, denied authority, or conflicting idempotency. Authenticated source visibility alone does not grant raw content restoration or model/publication authority.

## 11. Projection and audit implications

Keep source failure, destination semantic work, restore attempt, action delivery, review/repair verification, save, publication, and cleanup separately inspectable. The latest-run projection is not the only evidence of an earlier successful step or an earlier failed run.

Durable operation identity and independently readable artifacts reconcile ambiguous starts and completions. Preserve exact source/destination linkage and observed evidence. Unknown or partial observations must not be rewritten into successful zero-work or no-op outcomes.

## 12. Acceptance criteria

Retry from a step is exercised against a real Temporal server through the public route: the chosen step and its successors run again, earlier steps do not, a repeated request returns the same run, the reset history replays on installed code, and a history that diverges from installed code is refused without touching the failed run. Rerun and Continue-As-New keep their own lifecycle tests. Hermetic tests, live provider evidence, and promotion are reported separately; a passing helper does not satisfy an end-to-end claim.

## 13. Summary

Logical routes follow workflows. Effects and evidence pin runs. Terminal reruns create fresh work. Retry from a step resets the failed run's history so completed steps stay done and the chosen step runs again on installed code. Corrective branches acquire their own authority. The failed run remains in history, and qualification follows end-to-end evidence rather than issue bookkeeping.

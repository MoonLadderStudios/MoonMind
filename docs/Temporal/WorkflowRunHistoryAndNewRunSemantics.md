# MoonMind Workflow Run History and New Run Semantics

**Document class:** Canonical declarative lifecycle contract.
**Owner:** MoonMind Platform.
**Audience:** Backend, dashboard, workflow authors, and API owners.

## 1. Purpose

This document defines logical Workflow identity, run history, rerun, failed-step recovery, and Continue-As-New. These are different operations, not interchangeable meanings of Resume.

A failed-step recovery creates a linked execution from validated durable evidence, preserves eligible completed work, and resumes only the unfinished phase. It leaves the source failure and original authored input unchanged. Corrective instructions or changed execution authority instead require an explicitly admitted Checkpoint Branch or edited fresh execution.

## 2. Related docs

- [Workflow Execution Product Model](WorkflowExecutionProductModel.md)
- [Temporal Architecture](TemporalArchitecture.md)
- [Source of Truth and Projection Model](SourceOfTruthAndProjectionModel.md)
- [Visibility and UI Query Model](VisibilityAndUiQueryModel.md)
- [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md)
- [Checkpoint Branch System](../Workflows/CheckpointBranchSystem.md)
- [Workflow Remediation](../Workflows/WorkflowRemediation.md)
- [Remediation Verification Cadence](../Workflows/RemediationVerificationCadence.md)
- [Workflow Runs API](../Workflows/WorkflowRunsApi.md)
- [Primary Runtime Provider Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md)
- [Runtime Provider Rollout](../Omnigent/RuntimeProviderRollout.md)
- [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md)

## 3. Scope and non-goals

### 3.1 In scope

This contract owns operation meaning, source/destination lineage, phase-aware recovery admission, and truthful UI/API outcome presentation. Checkpoint bytes, filesystem restoration, provider sessions, approvals, publication effects, and cleanup remain with their existing owners.

### 3.2 Out of scope

No new universal recovery coordinator, duplicate workspace store, second runtime registry, mandatory per-run navigation, or guarantee of exactly-once external effects is introduced. A model or endpoint existing in code is not evidence that every advertised recovery phase is executable.

## 4. Runtime and product ownership

MoonMind owns durable workflow/step identity, immutable input and plan, recovery decisions, artifact/workspace authority, provider capacity, policy, publication, and cleanup. Omnigent owns live runtime mechanics through the generic execution plane. New qualified Codex, Claude Code, and OpenCode work shares those MoonMind contracts.

The root Workflow and existing recovery service orchestrate the restore-to-step handoff. Runtime/realizer and workspace owners perform actual reattachment or restoration. Checkpoint Branch turns have their existing durable execution owner. Publication-only work uses the existing publisher/recovery owner. Shared schema and decision helpers do not create a second scheduler.

Direct and profile-bound compatibility remains explicit and limited to its actual supported/history obligations. An `omnigent` runtime string, installed binary, or handler registration cannot qualify every nested harness, image, configuration, materializer, model, and host mode for recovery.

## 5. Canonical identifiers

### 5.1 Workflow ID

`workflowId` is the logical product route key. Continue-As-New retains it. Fresh rerun and failed-step recovery have a new Workflow ID and a relationship to the source.

### 5.2 Run ID

`runId` identifies an exact Temporal run instance. It is required for historical evidence and effect targeting even when the normal product route follows the latest run. It must come from the actual durable execution boundary, not a fabricated replacement in a projection.

### 5.3 Console routing identity

`/workflows/{workflowId}` follows the current logical execution by default. Operators need not understand Temporal rollover to follow work. An explicitly selected historical result or recovery source remains pinned and cannot be silently replaced by a later current run.

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
| Terminal rerun / explicit fresh rerun | New Workflow ID linked to immutable source | Re-execute admitted source inputs without claiming preserved-step restoration |
| Recover from failed/selected step | New linked recovery Workflow | Restore validated state and resume an explicitly supported phase |
| Corrective Checkpoint Branch | New admitted semantic branch/turn | Isolated changed instructions or authority from authorized source content |
| Publication-only recovery | Existing publisher's admitted operation | Finish/reconcile publication without repeating completed compute |

The existing terminal `RequestRerun` service path creates fresh execution; it must not be documented as an impossible terminal update or as Continue-As-New performed by a closed run.

### 7.2 Required behavior

A supported active Continue-As-New is a real Temporal lifecycle transition. The result identifies the accepted operation and actual destination. API acceptance, projected state, and physical runtime completion remain separate.

A terminal rerun starts a fresh execution through normal admission, leaves the source closed, and returns its exact destination identity. The caller follows the returned identity rather than assuming `workflowId` stayed unchanged. An unsupported active-run control returns an explicit reason instead of manufacturing new projection IDs.

### 7.3 Input changes allowed with a new run

Exact rerun retains original input, selected Skill snapshot, and execution choices, subject to current permission, revocation, deployment, and support validation. It does not silently replace an unavailable Profile, model, configuration, or image.

Edited retry is explicit new authored work. Resolve its Runtime + one Profile and subordinate configuration through ordinary admission, freeze its new input/plan, and retain source lineage. Changed source/attachments/retrieval/publication authority is revalidated. Do not call this unchanged-input checkpoint recovery.

### 7.4 State after a new run

The destination enters the actual planning/execution state appropriate to its supported contract. Rerun acceptance is not completed work. Link source and destination and preserve any pending preparation, capacity, or admission reason.

### 7.5 Terminal-state rule

Closed Temporal executions do not process ordinary updates. Terminal rerun is the service's explicit fresh-start operation, not a mutation of the closed run. Cancel/reconciliation may still finish outstanding owned resource cleanup without relabeling the original result. Never clear a source failure just to make a linked repair look successful.

## 7A. Failed-step recovery semantics

### 7A.1 Meaning of Recover from Failed Step

Recover from Failed Step means: create a linked execution with unchanged authored work, restore valid progress from the pinned source, and execute only the unfinished phase permitted by that checkpoint.

`RecoverFromFailedStep`, checkpoint recovery, and the UI's fully qualified recovery label are distinct from Pause/Resume, same-session continuation, full retry, and automatic history rollover. A before-execution checkpoint retries the failed step. A later checkpoint may require only review, downstream work, or publication. Do not rerun completed implementation merely because an auxiliary phase failed.

### 7A.2 Required behavior

Reuse the existing public recovery routes:

```text
POST /api/executions/{workflowId}/recover-from-failed-step
POST /api/executions/{workflowId}/recover-from-selected-step
```

The API contract owns request fields and error transport. Before starting linked work, it must resolve and pin the source run, actual failed/selected semantic step, original input and plan, checkpoint and validation artifacts, preserved-step outputs, side-effect disposition, exact destination capabilities, and supported continuation phase.

An intentionally selected earlier step requires its own compatible checkpoint and side-effect decision. It cannot reuse the failed step's workspace merely because the earlier step appears in the preserved-step list. Preserve only eligible predecessors of the selected boundary.

Commit the recovery intent and deterministic destination/restore identity before effects. Repeated identical requests reconcile that operation, including after source status or defaults later change. Changed source/run/checkpoint/phase/selection under the same key is a conflict. A deliberately new recovery attempt requires explicit admission and conflict control.

### 7A.3 Recovery input changes

Unchanged-input recovery preserves instruction, Skill, attachment, plan, runtime/harness, Profile, model/effort, configuration/policy, source/retrieval, and publication intent. Changed immutable choices require an authorized branch or edited retry. The recovery endpoint must not accept hidden instruction overrides.

Separate source evidence from destination use authority. Cold restoration may acquire a currently authorized credential generation for the same explicitly selected Profile and compatible recorded execution intent. That is not account substitution or restoration of old credentials. A rotated generation invalidates old live reattachment; it does not automatically invalidate non-sensitive checkpoint bytes. If compatibility or current permission cannot be established, stop and offer explicit re-admission/branch guidance.

Do not copy the source's live binding, session, host, lease, approval, or mutable runtime handles into a new destination. A fresh recovery plan records source-plan lineage and validates the intended immutable selection without pretending an old attempt's live authority belongs to the new one.

### 7A.4 Recovery checkpoint requirements

Require positive, independently validated evidence of:

- source namespace/workflow/run/logical step/semantic execution and boundary;
- original immutable input snapshot and plan identity/digest;
- checkpoint kind/schema, required content refs, digests, completeness and read permission;
- preserved predecessor outputs and acceptance provenance, not just their labels;
- canonical source workspace/repository baseline and captured candidate identity;
- exact operation, continuation phase, destination identity, workspace owner and registered worker route;
- immutable capability/support snapshot for the actual selected combination;
- side-effect safety or explicitly admitted reconciliation/compensation;
- current destination permissions, required policy and release admission.

An artifact reference, branch name, surviving container, global boundary map, or capture-only capability is insufficient. Large state remains artifact-backed; workflow history carries bounded validated references and decisions.

**Boundary-to-phase contract**

| Checkpoint boundary | Permitted continuation when separately supported | Work not repeated |
| --- | --- | --- |
| `before_execution` | `rerun_failed_step` | Accepted predecessor steps |
| `after_execution` | `continue_to_gate` | Successful implementation whose candidate was captured |
| `after_gate` | `continue_after_gate` | Accepted implementation and gate |
| `before_publication` | `resume_publication` | Completed implementation and verification |
| `before_recovery_restoration` | `retry_restoration` | Already completed semantic work and committed restore sub-effects |

An explicitly policy-selected `continue_to_remediation` additionally requires a new bounded remediation budget and the relevant verified candidate. Unknown or unsupported boundary/phase combinations are rejected. A global enum or pure policy helper is not proof that the destination workflow has implemented that phase.

Intersect the boundary mapping with the exact capability's boundary support, worker routing, source content validation, and side-effect disposition at API admission and independently at workflow entry. Preserve the frozen decision during replay. Mutable readiness is checked at controlled use boundaries, not read nondeterministically from workflow code.

**Recovery mode is a separate decision from continuation phase.**

- `live_reattach`: the original canonical session/turn, first-message identity, host, workspace, provider ownership, credential generation, policy, and event continuity are all valid. Reconcile original delivery rather than repost the first message.
- `cold_restore`: validated MoonMind-owned content is materialized and verified in a new authorized workspace/session/host as required. The original host is optional. New credentials/capacity are acquired through their existing owners.
- `branch_required`: requested immutable intent differs. Preserve the draft and expose explicit branch/new-admission choices.
- `resume_unavailable`: required evidence, route, authority, or exact support is missing/invalid/unsupported, with a bounded reason. A known temporary capacity wait is separate from unsupported restoration.

Cold restore must use the workspace owner encoded by the validated locator/capability. Do not send every generic Omnigent workspace to a sandbox archive Activity or confuse a provider session snapshot with workspace content. Prove restoration integrity and the destination handle before launching the agent or proceeding to review/publication.

### 7A.5 Detail routing and related runs

Each recovery has its own detail route and explicit relationship to the pinned source. Show preserved steps as reused provenance, not freshly executed steps. Show checkpoint boundary, continuation phase, recovery mode, destination, restoration evidence, and why the original host was reused/replaced/rejected.

The source failure remains visible. Verification follows the linked recovery/branch candidate and exact objective, not a requirement that the historical source row change to success. Expired or unavailable evidence yields an honest disabled reason and permitted alternatives. It never silently converts a recovery request to full retry.

### 7A.6 Finalization, interruption, and recoverable output

Persist independently verified compute and capture/save evidence before failure-prone publication or auxiliary reporting. Preserve it if checkpoint presentation or later capture fails; do not replace the only successful result envelope with a generic error. Report failed required saving/publication separately and retain bounded recoverable content under the existing owner.

Capture occurs while the actual workspace owner can still supply it. Verify the required durability handoff before deleting the sole copy. Host removal does not necessarily delete a separately owned workspace, so enumerate actual cleanup ownership rather than assume either safety or loss.

Cancel/fence active credential consumers, reconcile ambiguous effects, and release their capacity only under verified ownership/release rules. Retained non-sensitive work and optional reports do not require holding model capacity indefinitely. Every relevant janitor follows the same persisted preservation decision across restart and late cleanup.

Repository-independent artifacts, patches, bundles, local commits, and archives follow the saved-work contracts as implemented and qualified. Any change to what satisfies a required remote recovery checkpoint must first be reconciled with the owning durability policy. A documentation example cannot waive that gate.

### 7A.7 Historical compatibility and explicit unsupported paths

Retained historical payloads/histories use their supported decoder and command semantics. Do not rewrite their hashes or silently fill missing recovery proof with today's defaults. Reader compatibility is not permission to start new unsafe recoveries.

A typed service that currently rejects a phase, a restore callback without its workspace implementation, or a helper tested in isolation is incomplete product support. Keep the operation unavailable with an actionable reason until the existing production path is implemented and qualified. Removing the rejection without completing the handoff is not a fix.

## 8. Continue-As-New outside a requested new run

History management may rotate a Temporal run without user-requested re-execution. Preserve the actual unfinished state, accepted steps, immutable selection, pending action/approval/restore/publication identities, budgets, candidate head, and cleanup obligations. Do not replay completed business work simply because the orchestration history rolled over.

Only the supporting workflow protocol may authorize major reconfiguration. Use deliberate versioning and representative recorded-history replay for changed command order, child/Activity identities, routing, or serialized decisions.

## 9. When to start a fresh Workflow ID instead

Use a new identity for terminal rerun, copied/edited work, independently admitted authority, and linked failed-step recovery. A Checkpoint Branch retains its own graph/turn identity and existing execution owner. Keep explicit source/result relations rather than implying all operations are the same logical run.

Same-session continuation and recovery into a fresh session remain different even if both preserve workspace content. A new runtime/account choice never silently attaches to old session authority.

## 10. UI and API contract

### 10.1 Detail routing

The normal page follows `workflowId`; historical evidence and action targets pin `runId`. Reject stale asynchronous results after navigation, logout, or target changes. An explicit historical selection must not follow latest automatically.

### 10.2 Detail rendering

Distinguish Continue session, Resume paused workflow, Recover from failed/selected step, Retry from source, Edit for retry, Create repair branch, and Publish saved work when each is actually available. Do not render enabled recovery from the presence of a checkpoint ref alone.

Use one backend eligibility/intent boundary across Workflow Detail, row actions, Create drafts, schedules, presets, API/MCP, and remediation. Revalidate when the action is submitted. Normal destination authoring remains Runtime + one Profile; subordinate configuration is resolved and frozen, not another required wizard.

### 10.3 List rendering

Continue-As-New retains the logical list row. Fresh reruns/recoveries get their own linked row. Logical `startedAt`, current run timing, and last observation time must not be conflated. A projection outage or stale row is not permission to resubmit execution.

### 10.4 Execution API posture

`GET /api/executions/{workflowId}` supplies the current authorized view. The existing rerun and recovery endpoints return their actual operation/destination. Callers follow that response rather than infer Continue-As-New or success from an accepted flag.

Every operation has bounded typed rejection for stale identity, unavailable input/plan, invalid checkpoint, unsupported restore kind/phase/route, capability mismatch, denied authority, unsafe side effect, or conflicting idempotency. Authenticated source visibility alone does not grant raw content restoration or model/publication authority.

## 11. Projection and audit implications

Keep source failure, destination semantic work, restore attempt, action delivery, review/repair verification, save, publication, and cleanup separately inspectable. The latest-run projection is not the only evidence of an earlier successful step or failed recovery.

Durable operation identity and independently readable artifacts reconcile ambiguous starts and completions. Preserve exact source/destination linkage and observed evidence. Unknown or partial observations must not be rewritten into successful zero-work or no-op outcomes.

## 12. Acceptance criteria

Exercise the real public request, recovery service, workflow entry, Step Execution ledger, AgentRun/realizer, workspace capture/restore, artifact store, verifier/publisher, and cleanup wiring with controlled external dependencies.

A required source-destroying journey runs `prepare -> implement -> verify`: preserve accepted prepare, fail implementation, capture/validate the applicable state, remove source host/session/workspace/in-memory continuity, restore in a different destination, and complete only the intended unfinished phases. Assert exact file/digest/mode/Git state where required, new destination identity, preserved-step provenance, and invocation counts across source and recovery.

Add composed before-execution, after-execution, after-gate, publication-only, restoration-retry, and bounded continue-to-remediation cases for every claimed phase. Test selected earlier-step recovery with its own checkpoint. Unsupported cases remain explicit negative tests, not skipped passes.

Faults cover partial capture/manifest commit, source loss, credential rotation, stale/denied/corrupt content, lost create/send/restore/publication acknowledgments, duplicate requests, late results, cancellation, worker restart, competing recovery, publication/report failure, and janitor reconciliation. None may repeat accepted upstream work or silently change account, runtime, model, source, or publish intent.

Required CI must actually select and collect the production-boundary regressions. Hermetic tests, exact-artifact/container qualification, protected-live provider/operator evidence, and default promotion are separately reported. A passing helper or closed issue does not satisfy the full recovery claim.

## 13. Related documents and backlog

Implementation disposition, dated source findings, missing qualification, and issue ownership belong in the roadmap execution tracker and GitHub issues. Preserve unresolved roadmap acceptance identifiers **5.1**, **5.4**, and **5.5** and remediation **6.2** when moving or consolidating documentation. This contract does not mark them complete.

## 14. Summary

Logical routes follow workflows. Effects and evidence pin runs. Terminal reruns create fresh work. Failed-step recovery preserves unchanged intent and valid progress, restores through the correct owner, and resumes the supported unfinished phase. Corrective branches acquire their own authority. The source failure remains immutable, and qualification follows end-to-end evidence rather than issue bookkeeping.

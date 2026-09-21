# Workflow Remediation

**Document class:** Canonical declarative system / feature design.  
**Status:** Accepted desired behavior, not a claim of complete implementation.  
**Owners:** MoonMind Platform and dashboard.  
**Updated:** 2026-09-21

## 1. Purpose

Workflow Remediation lets an ordinary MoonMind Workflow diagnose a pinned failure, attempt an authorized repair, verify the resulting candidate, and produce separate prevention work. The source failure remains immutable. Successful repair is recorded on the relationship and resulting execution or Checkpoint Branch, not by turning the failed source into success.

[AGENTS.md](../../AGENTS.md) and the [single-user design](../SingleUserApplicationDesign.md) govern simplicity and admission. This document owns cross-workflow remediation and result interpretation. [Verification Cadence](RemediationVerificationCadence.md) owns in-workflow repair attempts, [Run History and New Run Semantics](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7a-failed-step-recovery-semantics) unchanged-input recovery, and [Checkpointing](../Steps/StepExecutionsAndCheckpointing.md) and [Checkpoint Branches](CheckpointBranchSystem.md) their mechanics.

## 2. Why a separate system is required

The separate concept is a remediation relationship, not another runtime or scheduler. Unlike `dependsOn`, it can inspect failed work without waiting for that failure to disappear. Relationship A → B → C grants no transitive evidence, credential, spending, or mutation authority.

## 3. Design goals

The default journey provides useful diagnosis, the smallest safe intervention, preserved cumulative content, exact-result verification, and an understandable next action. Unavailable evidence is handled through bounded recovery and honest limits, not fabricated success or an unbounded wait. A narrow repair does not depend on every harness, optional integration, or release-matrix row.

## 4. Non-goals

No new repair engine, session coordinator, checkpoint store, approval service, action ledger, or duplicate verifier policy. Remediation grants no raw host shell, Docker socket, arbitrary SQL, unrestricted network/mounts, storage keys, or secret reads. It does not automatically merge prevention changes, promote branches, or enable autonomous administration.

## 5. Architectural stance

### 5.1 Remediation Workflow Executions remain `MoonMind.UserWorkflow`

Use normal create/admission, Step Executions, artifacts, controls, and finalization. The execution schema owns the request envelope and historical decoding. Do not add a remediation-only serializer or rewrite stored input digests.

### 5.2 Remediation is a relationship, not a dependency

Persist accepted forward/reverse linkage to the source workflow, pinned run, and selected steps/checkpoint through the existing owner. The source can stay failed while its linked repair succeeds.

### 5.3 Control remains separate from observation

Logs, native chat, issue text, and diagnostics are untrusted observations. Actual actions use authenticated typed operations. A model proposal or report cannot grant itself permission or declare an external effect complete.

### 5.4 Source of truth remains unchanged

Temporal owns execution lifecycle. Immutable inputs, Step Execution/checkpoint records, and canonical session/turn owners retain their responsibilities. Database and UI projections do not become another execution authority. Refreshing a row is not proof of a fresh provider, host, or historical-run observation.

### 5.5 Remediation reuses existing repair and evidence substrates

Use the [primary-runtime strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md), shared Omnigent implementation, workspace, artifact, and publisher owners. Record actual attempt provenance and preserve meaningful harness/Profile/model/cost/privacy/source/publication choices. Compatibility follows required behavior, not equal SHAs, patch versions, or another all-fields fingerprint.

Saved source content and destination execution authority are separate. Same-session work retains its live session constraints. A fresh authorized branch receives normal destination admission. An automatic child cannot widen parent authority. These distinctions do not require three new execution systems.

## 6. Core invariants

- Source failure, original input, recorded plan, and accepted-step evidence remain unchanged. A new result has explicit lineage.
- Saved bytes, provider-session continuity, credentials, approvals, and publication authority are different things. Restoring content restores none of the others.
- Reconcile uncertain effects before retrying. Preserve confirmed compute, save, and publication through later failures and resume only the unfinished phase.
- A pending repair is not no-change or failure merely because an observation window ended. Verification follows the exact result, not the latest source row.
- Keep actual access, resource, and side-effect safeguards. Capacity waits never select another account or silently expand authority.
- Verify the required save before deleting the only recoverable workspace. Readable retained results must not depend on a live host or indefinitely held model credentials.

## 7. Submission contract

### 7.1 Canonical create path

Use ordinary `POST /api/executions`. The nested remediation object identifies the target, mode, and policy references within that owner's schema. Its illustrative shape remains:

```json
{
  "target": {
    "workflowId": "mm:target",
    "runId": "source-run",
    "stepSelectors": [{"logicalStepId": "implement", "attempt": 1}]
  },
  "mode": "snapshot_then_follow",
  "authorityMode": "approval_gated",
  "actionPolicyRef": "admin_healer_default",
  "lockPolicy": {"scope": "target_execution", "mode": "exclusive"},
  "trigger": {"type": "manual"}
}
```

This is not a second full request schema. Policy names and caller-supplied readiness fields do not confer authority.

### 7.2 Canonical normalized field

The providing execution schema owns normalized fields and retained `task.remediation` interpretation. Resolve conflicting new-write aliases at that boundary. Do not add duplicate normalization or a migration merely to change prose.

### 7.3 Field semantics

Resolve an omitted source run once at admission and persist it. Selected steps and AgentRuns must belong to that run. Existing `snapshot`, `live_follow`, and default `snapshot_then_follow` behavior remains, but live follow is optional. `observe_only` and `approval_gated` retain their policy meanings. `admin_auto` remains separately closed. Trigger origin is provenance, not permission.

### 7.4 Create-time validation

Use existing operator/machine admission, source/evidence permissions, policy, spending limits, and nested-remediation checks. Reject self-targeting, stale draft expectations, or unauthorized scope before effects. Persist one idempotent relationship rather than another admission registry.

### 7.5 Convenience API

Any supported `/api/executions/{workflowId}/remediation` convenience route expands through the same admission contract. It does not bypass policy or create an independently interpreted launch path.

### 7.6 Future automatic self-healing policy

Automatic diagnosis and automatic mutation are different grants. Additional automation requires explicit supported policy with bounded scope, budget, depth, and cancellation. Passing operator-initiated tests does not enable autonomous mutation or authorize extra paid work.

### 7.7 Create-page first remediation flow

The operator's Remediate action opens the existing visible Create draft, not a hidden execution. Keep Runtime and one Profile with meaningful optional overrides. Internal harness/configuration/Host Class/materializer/realizer identities are not extra required selectors. Preserve explicit edits under delayed discovery and capacity changes.

Retain current tab-scoped, versioned draft handling, including the existing two-hour import lifetime and complete-import/discard behavior. Missing, malformed, expired, or cross-tab drafts do not partially submit. Keep the target workspace, repair destination, and prevention repository distinct. Existing admitted API/automation flows remain separate from the browser interaction requirement.

## 8. Identity, linkage, and read models

### 8.1 Why both `workflowId` and `runId` are required

Logical identity locates the detail page. Evidence and actions identify the actual run, step, and attempt. A later rerun or Continue-As-New does not silently retarget a pinned repair.

### 8.2 Link directions

Both source and remediator link the exact resulting recovery execution, branch/turn, or operational resource. A link grants no additional read or mutation permission.

### 8.3 Durable linkage requirements

Reuse existing link/action/result fields for source, destination, candidate, current phase, relevant approval/operation references, and pending verification. Add only missing data to that owner, not a parallel result database or mandatory full-record copy.

### 8.4 Read model expectations

Show launch acceptance, compute, saving, verification, publication, promotion, and cleanup according to their actual owners. Separate source failure from repair result. A process exit or persisted branch graph is not proof of the coding objective.

### 8.5 Reverse lookup API

Existing inbound/outbound remediation reads project authorized links and compact state. Keep list/query work bounded and use the existing projection repair path. A missing projection cannot justify rerunning the action.

### 8.6 Approval and Workflow operator APIs

Reuse current approval and Workflow control endpoints. Pause/Resume of orchestration is not failed-step recovery or physical host quiescence. Invoke the supported Update/Signal/command protocol of the actual target, not an inferred managed-session ID.

## 9. Evidence and context model

### 9.1 Evidence sources

Read admitted step/checkpoint/branch results, runtime/session evidence, and durable logs through their owners. A provider URL, host pathname, or opaque session ID is not artifact access authority. No native RAG or Manifest system is recreated for remediation.

### 9.2 Remediation Context Builder

Reuse the bounded context builder and artifact readers. The context is an index into existing evidence, not a new store. Prefer actual result and recovery records over log-derived hypotheses. Materialize the bytes a Skill needs outside workflow history.

### 9.3 Context artifact shape

The providing schema owns source/candidate identity, observation times, evidence availability, refs, and relevant policy. Missing, partial, denied, and unavailable data stay distinct. Do not invent a new schema to restate that distinction.

### 9.4 Boundedness rule

Apply existing count/byte/token/time/page/retry limits before expanding evidence. Record truncation and exclusions. Optional enrichment failure must not erase the usable core evidence or start another full implementation attempt.

### 9.5 Evidence access surface for remediation Workflow Executions

Reuse existing context/read tools and typed action dispatch. UI and tools consume the same readiness projection. A registered action/classifier is not proof of actual execution and verification wiring. Do not add another capability catalog or promote an unsupported mutation.

### 9.6 Live follow semantics

Use bounded existing cursor/reconnect behavior and fall back to authorized retained observations when the stream is unavailable. Never restart business work to repair its display. A stream is not the sole source of terminal evidence.

### 9.7 Evidence freshness before action

Check actual target/run, resource generation, policy/revocation, expected state, and required approval at the effect boundary. A failed inspection is unknown, not absence. Advisory status and unrelated release-monitoring health must not become duplicate authorization checks.

### 9.8 Immediate repair and prevention workflow

Verify the smallest actual postcondition. Operational recovery may be useful without completing the coding objective. Prevention is independently scoped and verified. A prevention PR does not prove immediate repair, and missing requested publication remains an unmet effect even when saved work is valid.

### 9.9 Checkpoint-backed repair

Unchanged-input recovery uses the existing failed/selected-step path. Corrected instructions or materially changed authority use normal new branch/execution admission. Do not insert hidden corrective text into an unchanged-input retry.

Validate saved source and exact predecessor separately from destination authority. Fresh restoration may use the current authorized generation of the same selected Profile and a compatible installed runtime. It does not revive an old lease or qualify live reattachment. Actual revocation, denied content, or incompatible behavior still blocks the affected action.

Continue/fork from the committed predecessor candidate, not the root baseline. Expected-head conflict protection prevents late results from overwriting progress. Compare is read-only. A derived work branch is not another publication grant. Publish, promote, and archive retain their distinct existing authorization and retention rules.

### 9.10 Omnigent-backed remediation

Use one existing generic host/session/turn lifecycle and the correct workspace owner. Reconcile duplicate create/send acknowledgments before another provider effect. Do not wrap the generic realizer in an additional supervisor or infer fresh execution authority from a source plan.

## 10. Security and authority model

### 10.1 Authority modes

`observe_only` allows scoped diagnosis, not mutation. `approval_gated` permits actions only under the existing enforced policy. `admin_auto` remains unavailable until its separately authorized implementation and safeguards exist. A draft, schedule, model response, or passing test cannot enable it.

### 10.2 Execution principal

One human operator may use several accounts and concurrent workflows. An agent or machine capability remains limited to its admitted target and operations. A policy principal is not a second human account or model Profile.

### 10.3 Permission model

Preserve separate evidence, raw-artifact, spending, action, approval, and repository-role permissions under single-user admission. Do not implement human tenancy or admin/member inheritance to satisfy predecessor language. Explicitly configured stronger policy and existing enforcement require a deliberate migration, never an implicit bypass.

### 10.4 Secret handling

Keep reusable credentials, OAuth homes, signed download URLs, approval grants, and sensitive configuration out of ordinary context, logs, archives, and workflow history. Use existing secret resolution, redaction, and outbound controls. A content digest does not make secret-bearing bytes safe to disclose.

### 10.5 Artifact and log access mediation

Preview rights do not grant raw restore or publication access. Use the authorized reader and validate required content/identity. Denial is not permission to read a local path or use a broader storage credential.

### 10.6 High-risk actions

Destructive cleanup, forced termination, session replacement, and replay of external effects need their actual policy, approval, ownership, and postcondition. Missing logs or capacity pressure is not sufficient justification. Preserve necessary consumer fencing and save-before-delete behavior.

### 10.7 Visibility and redaction posture

Use operator admission plus scoped machine/resource access, not a human-role matrix. Unauthorized identifiers do not disclose target existence. Cache and asynchronous-response identity must preserve the admitted target/evidence scope.

## 11. Remediation action registry

### 11.1 Rationale

Reuse the existing typed operations and adapters. A small useful subset is better than another administrator-action platform. Native plumbing does not duplicate portable Skill reasoning.

### 11.2 Canonical action kinds

Keep currently supported execution/session controls, targeted resource reconciliation, and branch/recovery handoffs. Optional unsupported destructive actions stay unavailable. A required useful capability cannot be declared optional merely to close an issue. The historical corrective-retry name does not create another repair engine.

### 11.3 Action semantics notes

Resume means resume paused orchestration, not checkpoint restoration. Terminal rerun uses a linked fresh execution. Generic session commands resolve actual bindings rather than guessing direct-managed IDs. Cleanup and lease actions use their real bounded resource owners. Branch creation is delivery, not verified repair.

### 11.4 Action request contract

Persist normalized intent and its existing operation identity before effects. Bind target/run/resource, relevant expected state, policy/approval, and observed baseline. Changed intent under the same key is a conflict. Use existing schemas instead of a new universal action envelope.

### 11.5 Action result contract

Keep accepted/queued delivery, confirmed effect, no-op, denied/unknown/failure, and pending verification distinct through existing result fields. A lost acknowledgment is not denied delivery or permission to retry from scratch. Resolve the original operation first.

### 11.6 Risk tiers and verification

Verification scope follows the action's actual postcondition. A pause can be operationally verified without proving the original business objective. A coding repair needs the intended candidate acceptance evidence. Reuse the existing verifier and valid matching results rather than automatically running another paid full check.

### 11.6.1 Trusted post-action verification phase

Retain the existing outcomes: `verified_resolved`, `verified_no_change`, `still_failed`, `regressed`, `evidence_unavailable`, `approval_required`, `verification_failed`, and `canceled`. Pending/waiting is a lifecycle fact, not a terminal no-change outcome.

Persist the action result and verification obligation through the existing action/link/orchestration owner. Bind exact resulting workflow/run/branch/turn/candidate and objective scope, with the source pin separate. Enforce the run in the actual reader. Follow a successor only through supported recorded linkage. A latest row or unrelated later success is not evidence for this repair.

A source can remain failed while its linked candidate verifies successfully. Graph persistence and branch compute are insufficient without required objective evidence. Conversely, source failure is not proof that the branch failed. Keep operational verification distinct from full objective verification and physical cleanup.

Use an existing result notification or bounded durable wait. The repair lifetime is not the HTTP/Activity polling window. Browser closure, worker restart, delayed result, or report-upload failure must leave the same obligation recoverable without redelivering the action. Deadline expiry retains actual evidence and a truthful incomplete disposition, not invented completion. No new polling daemon or verifier scheduler is required.

Keep before/after observations as actually collected. Never fabricate a missing baseline from later state. Reuse a verdict only for the same candidate/scope/policy. A new candidate needs matching evidence. Cancellation of observation does not undo the underlying effect or discharge its remaining reconciliation.

## 12. Locking, idempotency, and loop prevention

### 12.1 Why locks are required

Concurrent workflows can contend for the same credential, workspace, host, or destination. Reuse existing resource ownership and action/link records. Do not introduce a lock service for every descriptive field.

### 12.2 Lock scopes

Use only the relevant existing target/resource scope. Mutation is exclusive where necessary. Bounded parallel read-only diagnosis does not automatically need the same mutation lock.

### 12.3 Lock contract

Preserve holder, target, fence/generation, and release semantics through the existing owner. Expiry prevents new writes but does not prove a consumer stopped. Stale completion cannot release or modify a replacement's resources.

### 12.4 Action idempotency

Reconcile intent, original effect, and persisted result at the actual provider/subsystem boundary. An idempotency-key string alone does not guarantee exactly-once external effects or billing. Do not create another ledger to compensate for a missing consumer handoff.

### 12.5 Retry budget and cooldowns

Use the existing admitted action/attempt/time/model bounds. Preserve consumed budget, latest report, candidate, and pending operation through restart. Infrastructure recovery does not grant a fresh semantic budget. No-progress uses meaningful candidate/evidence changes, not timestamps or reordered report text. Cadence and supported checkpointless-source rules remain with their existing owners.

### 12.6 Nested remediation defaults

No self-targeting or automatic remediation of a remediator by default. Explicit nested use has bounded depth and independent scope. Relationships never grant transitive privileges.

### 12.7 Target-change guard

A changed target, candidate, or action intent requires the existing conflict/new-admission path. Do not confuse a meaningful authority change with incidental installed patch provenance. Preserve strict live-session and resource fencing.

## 13. Runtime lifecycle

### 13.1 Remediation phases

Use the ordinary Workflow lifecycle and existing phase metadata for diagnosis, action, verification, and finalization. Waiting for capacity, an action result, or evidence is explicit, not another top-level Workflow type.

### 13.2 Recommended lifecycle

Read evidence, diagnose, authorize the smallest required action, execute/reconcile, verify its exact result, and retain repair/prevention outcomes. Diagnosis-only can complete without mutation. Exhaustion preserves the candidate and unresolved obligations.

### 13.3 Recommended step structure

Resolved Skills own diagnosis, coherent semantic repairs, and objective verification. Native orchestration owns admission, bounded artifact delivery, durable scheduling, and result consumption. Full semantic verification is attempt-scoped; independently risky operations keep their targeted checks. Do not create another native copy of the Skill or a full verifier for each helper action.

### 13.4 Cancellation semantics

Canceling the remediator does not automatically cancel the target or undo accepted effects. Preserve verified compute/save/publication independently. Before destructive cleanup, verify the required durable copy through the actual workspace owner. Failed saves retain bounded recoverable work under the existing finalizer/janitor decision.

Credential consumers must stop and their safe release must be recorded before reuse. Retaining non-sensitive content or publishing optional diagnostics does not require indefinitely held model capacity. [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md) and [Workflow Publishing](WorkflowPublishing.md) own save-only versus remote-publication semantics. Verified artifact-backed saving does not require a new GitHub credential; requested publication remains separate. A pathname or incomplete upload is not durability evidence.

### 13.5 Rerun semantics

Resume only the unfinished supported phase: restore, failed semantic step, gate, downstream work, or publication through its existing owner. Accepted predecessor outputs must actually be loaded into the destination, not merely listed as reused. Corrected instructions use branch/new admission. Never silently replace failed recovery with a full paid rerun.

### 13.6 Continue-As-New state integrity

Preserve source/result pins, accepted progress, current candidate/report, operation/approval identity, budgets/deadline, and pending verification/cleanup. Actual command or payload changes require relevant retained-history replay or a controlled migration. Historical hashes are not rewritten to fit new defaults.

## 14. Artifacts, summaries, and audit

### 14.1 Required remediation artifacts

Reuse current context, plan, action request/result, verification, summary, decision-log, and approval artifacts. Their existing schemas own exact labels and filenames. Keep required evidence durable without requiring every possible report channel for a diagnosis-only result. Optional preview failure cannot erase committed work. [Artifact Presentation](../Artifacts/ArtifactPresentationContract.md) governs safe access.

### 14.2 Target-side artifacts

Link authoritative subsystem evidence instead of copying it into a second lifecycle store. Target annotations preserve the original outcome.

### 14.3 Remediation summary block

Separate source failure, action delivery, exact repair result, verification scope/status, prevention, saving, publication, and pending work. A branch, `NO_COMMIT`, or prevention PR is not itself a repair verdict. Use existing fields without another summary schema.

### 14.4 Target-side linkage summary

Show compact inbound links and the latest actual repair state with observation freshness. Missing projections remain repairable read-model gaps, not permission to repeat execution.

### 14.5 Control-plane audit events

Keep bounded actor/operation/target/result linkage, relevant authority refs, observed timestamps, and original errors. Detailed records stay in protected artifacts. Do not add an audit platform or high-cardinality metric inventory as a prerequisite to a narrow repair.

## 15. Dashboard UX

### 15.1 Create flow

Use existing Remediate-to-Create authoring with pinned target, repair intent, Runtime/Profile, limits, and actual unavailable-action reasons. GitHub is not required for scoped diagnosis or supported save-only work.

### 15.2 Target Workflow detail

Show immutable failure and a linked repair panel. Pending/verified repair is a separate fact from source status. Reuse current panels and read models rather than another dashboard.

### 15.3 Remediation Workflow detail

Show target, exact candidate, current phase/wait, evidence limitations, and a valid next action. Technical details belong in existing diagnostics. A disabled action should explain the missing capability, not trigger an alternate runtime.

### 15.4 Evidence presentation

Use existing authorized artifact renderers. Generated content is inert untrusted content. Never substitute a broader raw read or upstream URL after denial.

### 15.5 Live follow behavior

Label freshness/gaps and keep retained evidence available independently of the stream. Browser presence is never the lifetime owner of accepted work or pending verification.

### 15.6 Operator handoff

Reuse the existing persisted approval owner. Bind decisions to exact intent, target, expected state, policy, expiry, and single-use semantics. An agent/machine request cannot issue its own approval. The admitted human operator may approve the agent's proposal through that owner; do not add a second-person account model to a single-user application. Preserve explicitly configured stronger requirements and current enforcement until a deliberate migration handles them.

A reference or caller flag is not approval. Re-resolve the decision before effects and retain deny/stale/expired/conflict behavior. Automated tests can exercise the ordinary operator-authorized path. This does not authorize an implementation agent to approve real production actions or waive an explicit user approval.

Pending verification and ordinary missing-tool/evidence recovery are automation-owned work, not automatically `remainingOperatorWork`. Follow the [existing continuation contract](StepReviewGateSystem.md): preserve candidate, concrete gap, evidence, original error, consumed budget, and next authorized action. Do not claim that continuation is scheduled until its owner accepted it. Genuine user decisions remain explicit.

## 16. Failure modes and edge cases

Invisible targets, unavailable artifacts, busy capacity, stale approvals, changed resource ownership, and missing implementation have different bounded outcomes. A failed lookup is unknown, not a verified no-op. Force termination is never a generic fallback.

For ambiguous legacy issue/PR evidence, use the existing authorized reads and reconciliation before requesting a human decision. Preserve work and stop unsafe mutation when authority remains unresolved. Human-authored labels, repository content, or a report cannot authorize broader work. [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md) owns actual historical migration, not a new remediation reconciliation system.

## 17. Recommended v1

A useful first journey is ordinary diagnosis, unchanged-input recovery or an explicitly admitted corrective branch, exact-result verification, and preserved output. Optional destructive actions, static hosts, unrelated connectors, screenshots, and a complete release matrix are not prerequisites to fixing that journey. Required supported behavior is not silently dropped to close an issue.

## 18. Future extensions

Additional actions and bounded automation extend existing owners only when a concrete use requires them. They do not justify a new readiness catalog, status taxonomy, approval chain, or permanent migration framework.

## 19. Acceptance criteria

Reuse real current-candidate product and integration tests for accepted-step recovery, source-host-independent restore, branch continuation, cumulative candidate progress, exact-result verification, delayed completion, and failure preservation. Assert bytes and invocation counts, not only IDs and terminal labels. Keep meaningful wrong-target, approval, credential, cancellation, and replay tests.

A served browser/API journey proves the authoring/interaction claim. Real storage/Temporal/process tests prove their respective boundaries. Common mechanisms can share evidence; genuinely different credential/protocol/storage behavior needs focused coverage, not an independently repeated full cross-product. Targeted local tests support development and broader affected suites run in GitHub Actions.

[Omnigent verification](../Omnigent/ConformanceAndLiveSmoke.md) and existing #3626/#3832 owners distinguish implementation evidence, packaged artifacts, authorized live observations, and any explicitly strict certification policy. Missing mandatory proof is not a pass, but unrelated protected-runner/report outages must not disable basic authorized diagnosis by default. Current enforcement must be migrated coherently, not bypassed because this document changes. Autonomous mutation remains separately closed.

These requirements describe the target, not implemented recovery. #3510, #3621, #3622, and #3512 retain the actual remaining work. No new registry or mandatory manual test execution is required. Documentation-only changes are reviewed, not unit-tested for wording, headings, counts, or metadata.

## Appendix A. Example action policy

A bounded operator-initiated policy permits only supported scoped actions, preserves explicit approvals, verifies their actual effects, and prevents uncontrolled nesting. The name `admin_healer_default` does not enable `admin_auto` or every registered operation.

## Appendix B. Example remediation summary

A useful summary can say: source failed; corrective branch accepted; candidate verification pending under the existing action owner; prevention not attempted. That is not a successful repair claim or a demand for human review. The actual wire fields remain owned by the existing schema.

## Appendix C. Design rule summary

One ordinary Workflow, pinned source, exact result, and existing owner for every effect. Preserve cumulative content and authority. Separate delivery from verification, repair from prevention, and saving from publication. Recover the unfinished phase without restarting completed work.

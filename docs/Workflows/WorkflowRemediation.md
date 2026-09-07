# Workflow Remediation

**Document class:** Canonical declarative system / feature design.
**Owners:** MoonMind Platform and dashboard.

## 1. Purpose

Workflow Remediation lets a normal MoonMind Workflow investigate another Workflow, attempt an authorized repair, verify the resulting work, and propose separately reviewable prevention changes. The target's original failure remains immutable. A successful repair is recorded on the remediation relationship and the resulting recovery execution or Checkpoint Branch, not by changing the failed source into a success.

A target is identified by workflow and pinned run. The remediator is a separate `MoonMind.UserWorkflow`. Immediate repair addresses the target objective or a specific operational condition. Prevention changes MoonMind, a preset, configuration, documentation, or a portable Skill to reduce recurrence. Either output may be useful independently, but neither may be mislabeled as the other.

This document owns cross-workflow remediation, action authority, and repair-result interpretation. [Remediation Verification Cadence](RemediationVerificationCadence.md) owns bounded in-workflow repair attempts. [Workflow Run History and New Run Semantics](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7a-failed-step-recovery-semantics) owns unchanged-input recovery. [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md) and [Checkpoint Branches](CheckpointBranchSystem.md) own checkpoint and branch mechanics.

## 2. Why a separate system is required

A remediation relationship is not `dependsOn`. Ordinary dependencies wait for prerequisite success. Remediation often starts because the target failed, stalled, or requested attention, and must be able to inspect that failure without waiting for it to disappear.

The relationship grants no transitive authority. If B remediates A and C remediates B, C does not gain access to A. Target visibility, evidence access, model spending, operational mutation, approval, and repository publication are separate permissions.

## 3. Design goals

The normal product journey must provide understandable authoring, pinned evidence, bounded diagnosis, the smallest safe intervention, durable action and verification evidence, cumulative progress, and an actionable terminal result. Missing evidence must lead to a bounded degraded diagnosis, explicit unavailability, or escalation, never fabricated success or an infinite wait.

Security, resilience, and observability apply equally to Codex, Claude Code, and OpenCode through the generic Omnigent plane. Equivalent lifecycle guarantees do not imply identical provider-session capabilities or blanket support for every combination.

## 4. Non-goals

Remediation does not grant host shell, Docker socket, arbitrary SQL, unrestricted mounts or networking, storage keys, raw secret reads, or redaction bypass. It does not guarantee that every failure is repairable. It does not automatically merge prevention changes, promote repair branches, or spawn an administrator agent for every failure.

It must not introduce a second runtime coordinator, session model, checkpoint store, publication engine, approval owner, or verification policy implemented alongside the existing portable Skills.

## 5. Architectural stance

### 5.1 Remediation Workflow Executions remain `MoonMind.UserWorkflow`

Remediation uses the ordinary Workflow create/admission path, Step Execution ledger, artifacts, cancellation, and finalization. Its nested remediation object marks the relationship. The execution API and Workflow schema own the outer request envelope and historical `task` versus `workflow` decoding. A remediation-only serializer must not become a second authority or silently reinterpret retained input snapshots.

### 5.2 Remediation is a relationship, not a dependency

Persist both directions of the relationship before exposing accepted remediation. Pin target workflow, run, selected steps, and relevant checkpoint identity. The target may remain failed throughout a successful linked repair.

### 5.3 Control remains separate from observation

Logs, native chat, and event streams are observations. Actions use authenticated typed control boundaries. Untrusted issue text, repository content, transcripts, diagnostics, or model proposals cannot change policy, choose credentials, authorize actions, or declare their own verification success.

### 5.4 Source of truth remains unchanged

Temporal owns execution lifecycle. The plan and immutable input artifacts own authored work. Step Execution and checkpoint owners own progress and restoration evidence. Canonical session/turn owners govern live interaction. Result, publication, cleanup, and remediation read models project those owners without replacing them.

A refreshed database row is not automatically a fresh observation of Temporal, a provider process, or a host. Each operational claim identifies its actual owner and observation freshness.

### 5.5 Remediation reuses existing repair and evidence substrates

[Primary Runtime Provider Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md) and [Harness Platform Design](../Omnigent/OmnigentHarnessPlatformDesign.md) apply to diagnosis, recovery, repair branches, verification, and prevention work.

New qualified coding-agent work uses `agentKind=external`, `agentId=omnigent`, with the exact harness, configuration, model, Host Class, materializer, and realizer recorded in its immutable execution plan. Runtime-specific differences remain small capability adapters. Direct and profile-bound paths are explicit, independently qualified compatibility paths, not silent recovery fallbacks.

A new repair branch has independently admitted execution authority. Its source checkpoint supplies content and lineage, not an active lease, old credential generation, approval, or an execution plan that may be blindly reused for changed work. Same-session continuation instead uses its existing canonical session/turn owner and cannot silently change immutable session dimensions.

## 6. Core invariants

- Original source input, plan, failure, and accepted-step evidence remain immutable. New work has explicit lineage and semantic Step Execution identity.
- A workspace checkpoint, provider-session continuity, a Git output branch, and publication evidence are different things. One does not prove another.
- Source preservation and destination admission are checked independently. Valid saved bytes do not authorize model spending, a different account, or repository mutation.
- Every mutation is policy-bound, expected-state-bound, conflict-controlled, idempotently identified, and auditable. An ambiguous acknowledgment requires reconciliation before repeat mutation.
- Successful compute, saved work, action delivery, verified repair, prevention, publication, and cleanup have separate outcomes.
- Missing support, changed authority, or invalid evidence is visible before mutation. Temporary capacity waits do not substitute another Profile, model, harness, host mode, or billing route.
- No checkpointless path may infer authority from a local pathname, a surviving container, a branch name, or an agent's statement that files were saved.
- Artifacts and historical results remain readable under their authorization and retention rules after hosts disappear. Recovery does not require keeping model credentials leased merely to retain non-sensitive work.

## 7. Submission contract

### 7.1 Canonical create path

Use ordinary `POST /api/executions` admission. The following is an illustrative **nested remediation object**, not an independent complete create-request schema:

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

The server resolves the authorized immutable policy version. A policy name or browser-provided readiness flag is not approval or execution authority.

### 7.2 Canonical normalized field

The nested remediation contract travels with the normal immutable Workflow input. Existing `task.remediation` consumers and retained payloads must be reconciled through the providing execution-schema owner. New authoring must follow that owner's canonical envelope, reject conflicting aliases, and preserve source bytes/digests needed for history. This document does not create a second envelope or require a schema migration merely to repair the feature.

### 7.3 Field semantics

`target.workflowId` is required. An omitted `target.runId` is resolved once at creation and persisted. Step selectors and AgentRun refs are bounded and must belong to that pinned run.

Modes are `snapshot`, `live_follow`, and `snapshot_then_follow`; the last is the normal default. Live observation remains optional and cannot be the only evidence path.

Ordinary manual authority modes are `observe_only` and `approval_gated`. `admin_auto` is reserved for separately qualified autonomous rollout and is rejected by ordinary authoring while that gate is closed. `evidencePolicy`, approval rules, locks, budgets, and triggers are validated hints or references under server-owned policy, never self-issued privileges.

Trigger origin distinguishes manual, failure, attention, stuck, policy, and promoted-proposal origins where supported. An origin label cannot enable automatic mutation.

### 7.4 Create-time validation

Resolve visibility, target type, pinned run, selected steps/AgentRuns, checkpoint linkage, policy and principal permissions, evidence/data-use scope, and nested-remediation limits. Reject self-targeting, conflicting identity, unsupported authority, and stale draft expectations before paid or mutating work. Persist an idempotent forward/reverse remediation link.

### 7.5 Convenience API

A supported `POST /api/executions/{workflowId}/remediation` convenience route expands into the same create/admission contract. It cannot bypass visible authoring, policy, budget, or target validation.

### 7.6 Future automatic self-healing policy

Automatic creation is an independently authorized, bounded layer. Separate diagnosis-only automation from mutating automation. Freeze trigger, allowed action set, budget, concurrency, cooldown, maximum depth, and release-policy version. Keep manual diagnosis usable without enabling autonomous mutation.

### 7.7 Create-page first remediation flow

`Remediate` opens `/workflows/new?intent=remediate&draftId=…`; it never immediately submits a hidden run. The operator sees immutable target identity and editable repair intent before submission.

Normal execution authoring exposes **Runtime and one Profile**. Execution configuration, harness, Host Class, materializer, launch policy, and realizer are subordinate resolved authority, not another required selector chain, including behind Advanced mode. Genuinely optional supported model/effort or policy overrides remain available. Existing Codex and Claude OAuth Provider Profiles are reused rather than cloned into Omnigent-specific accounts.

The draft may suggest the target's compatible selection, but must preserve explicit edits and disclose unavailable historical choices. Displayed selection, submitted request, and admitted plan must agree. Incompatible or stale choices remain recoverable drafts with focused remedies, not silent defaults. These rules also apply to schedules, reruns, branch continue/fork, API, and MCP consumers.

Keep the target repository/workspace, repair destination, and prevention repository distinct. MoonMind's repository may be selected for platform prevention work, but is not a substitute for the failed target's source. Publication remains a separate explicit choice.

Drafts remain tab-scoped, schema-versioned, timestamped, and bounded to the existing two-hour import lifetime. Successful complete import or explicit discard removes the stored draft. Missing, malformed, expired, and cross-tab drafts have distinct safe errors and do not partially import. Presence markers contain no repair content. Draft import, metadata refresh, and immediate submission must preserve explicit input and current request identity.

## 8. Identity, linkage, and read models

### 8.1 Why both `workflowId` and `runId` are required

The workflow detail route anchors on logical identity. Evidence and actions pin exact run/step/attempt identity. Continue-As-New, rerun, or another actor's recovery cannot silently retarget a remediation.

### 8.2 Link directions

Both target-to-remediators and remediator-to-target views link the pinned source, selected evidence, and resulting recovery workflow, branch, turn, or operational resource.

### 8.3 Durable linkage requirements

Persist the source identity, admitted repair intent, phase, action/approval/lock refs, cumulative attempt/head identity, resulting execution/branch/turn identity, verification target and scope, repair outcome, prevention outcome, and unresolved cleanup/operator work. A ref names evidence but does not grant access.

### 8.4 Read model expectations

Project graph persistence, launch acceptance, running/terminal repair execution, verification pending/terminal, publication, promotion, archive, and cleanup independently. The immutable source may be `failed` while a linked result is verified to satisfy the repaired objective. Do not derive repair failure solely from the source row, or repair success solely from a branch's process exit.

### 8.5 Reverse lookup API

Existing inbound/outbound remediation reads under `/api/executions/{workflowId}/remediations` project authorized links, compact evidence availability, action capabilities, approvals, branch state, and result lineage. They do not expose arbitrary runtime bindings or implement lifecycle decisions in the browser.

### 8.6 Approval and Workflow operator APIs

Approval decisions use the existing `/api/executions/{remediationWorkflowId}/remediation/approvals/{requestId}` contract with approved/rejected decision and optional bounded comment. The persisted owner may use `denied` internally; transport spelling is normalized by that owner.

Takeover uses supported ordinary Workflow controls. Pause/Resume of orchestration is not failed-step recovery and is not proof that every external process stopped. Cancellation uses `/api/executions/{workflowId}/cancel`. Use the supported Update/Signal protocol of the actual target type rather than an action-name-derived universal protocol.

## 9. Evidence and context model

### 9.1 Evidence sources

Read authorized execution and step evidence, recovery/incident/Step Execution manifests, checkpoint and branch records, adapter capture/resource/terminal evidence, managed or canonical-session diagnostics and continuity, and durable logs. Native Omnigent interactions remain on the bound Workflow Chat surface. A provider URL or session ID is metadata, not artifact authority.

### 9.2 Remediation Context Builder

Reuse the existing builder and artifact services. `reports/remediation_context.json` is a bounded index, not another evidence store. Prefer authoritative recovery and Step Execution manifests before log-derived hypotheses. Include the selected evidence scope, availability/freshness, source and candidate identity, policy/approval/lock snapshot, and live cursor when supported.

### 9.3 Context artifact shape

The context records a schema version, generation and observation times, remediator and pinned target identities, selected steps, artifact refs, bounded diagnosis hints, per-class available/partial/unavailable/denied results, retrieval/data-use scope, and policy refs. Exact schema serialization belongs to its providing model. Missing data is not a successful empty result.

### 9.4 Boundedness rule

Enforce count, byte, token, time, page, and retry bounds before expansion. Large evidence remains artifact-backed outside workflow history. Referenced content must actually be materialized through authorized readers when a Skill or verifier needs its bytes. Record residual truncation and exclusions rather than claiming complete inspection.

### 9.5 Evidence access surface for remediation Workflow Executions

Reuse `remediation.get_context`, bounded target artifact/log/event/checkpoint readers, `remediation.list_allowed_actions`, and typed `remediation.execute_action` through the existing API/Activity/MCP boundary. Live follow is optional. No dashboard scraping or broad proxy is required.

`actionCapabilities` distinguishes `requestable`, `dryRunSupported`, `executionBackendReady`, `approvalBackendReady`, `verificationBackendReady`, exact runtime/host support, required evidence, and bounded blocked reasons. `allowedActions` contains only requestable rows. Catalog membership, a registered classifier, or the presence of a worker handler is necessary evidence where applicable, not proof of a qualified end-to-end operation.

### 9.6 Live follow semantics

Follow only authorized active targets whose canonical stream supports it. Persist cursors, bound reconnect/replay, distinguish gaps and epoch boundaries, and degrade to retained evidence without restarting business work. Projection/stream failure cannot change canonical execution outcome.

### 9.7 Evidence freshness before action

Revalidate target run, resource ownership/generation, expected state, policy/revocation, lock, approval, candidate head, and current action readiness at the trusted effect boundary. A stale page or earlier diagnosis is insufficient. Read failures remain unavailable rather than proof of absence or safe cleanup.

### 9.8 Immediate repair and prevention workflow

Choose the smallest safe action, verify its exact postcondition, then determine whether prevention work is warranted. A restored lease or resumed orchestration proves only that operational postcondition, not necessarily completion of the coding objective. A reviewable prevention PR does not prove the immediate repair worked.

Record attempted/skipped/denied/unsafe decisions, action and verification refs, resulting work identity, root-cause hypothesis and confidence limits, prevention branch/PR and its independent verification, and remaining operator work. Required publication is not silently changed to save-only success when credentials are missing.

### 9.9 Checkpoint-backed repair

Unchanged-input failed-step recovery preserves the original specification and resumes at a checkpoint-defined phase. Corrected instructions, changed model/effort, Profile, policy, retrieval, repository branch, publication choice, or other immutable authority requires a separately admitted Checkpoint Branch or fresh execution. Do not overload `RecoverFromFailedStep` with corrective text.

A repair branch records source workflow/run/step/ordinal/boundary and checkpoint digest, immutable new instruction refs/digest, destination selection and plan, workspace policy, isolated work branch where applicable, remediation provenance, and idempotency identity. Validate source content separately from destination authority. A supported fresh branch can use a currently authorized credential generation for the explicitly selected Profile; that does not revive the old lease or qualify same-session reattachment.

Create, continue, and fork use the existing branch-turn owner. Continuation starts from the selected predecessor's committed candidate, not repeatedly from the root baseline. Compare is read-only. Publish, promote, and archive are separately authorized effects. Publication is not promotion, and archive cannot destroy content retained by another consumer.

### 9.10 Omnigent-backed remediation

Use the generic realizer, canonical workspace owner, session/turn commands, and shared finalization. A semantic branch or cold destination gets fresh session/host ownership as required; retries reconcile the same admitted attempt instead of launching another one.

Explicit supported same-session interaction uses the canonical session supervisor and turn command boundary. Remediation never sends hidden corrective messages into a parent session or treats the existence of a supervisor as blanket resume support. Native chat readiness and historical evidence access remain independently observable.

## 10. Security and authority model

### 10.1 Authority modes

`observe_only` allows scoped reads and diagnosis, not mutation. `approval_gated` allows proposals and supported dry runs and executes only according to enforced approval policy. Future `admin_auto` requires server-owned release admission and a narrow authorized action set. It is not enabled by examples, a draft, a schedule, or an LLM decision.

### 10.2 Execution principal

Audit the requesting actor and the named principal/security policy used by the trusted action owner. A model runtime may propose privileged actions without receiving the owner's host credentials or unrestricted operational permissions. A security-policy reference is not a second model-account Profile.

### 10.3 Permission model

Check target visibility, evidence and raw-artifact access, remediator creation/model spending, action execution, approval, audit access, and each repository role independently. Source, collaboration, and publication credentials follow their providing connection/binding owners. Restoring a workspace restores none of these permissions.

### 10.4 Secret handling

Secret-reference, scanning, redaction, and data-use rules apply before model disclosure and at outbound effects. No raw credentials, OAuth homes, approval grants, signed download URLs, or reusable capability secrets enter durable context, histories, logs, or archives. Retain only necessary bounded authority metadata under authorization.

### 10.5 Artifact and log access mediation

Use authorized artifact/default-read projections. Safe preview access does not grant raw restore/publication permission. Denied raw content is not an invitation to read a local path or fall back to unrestricted storage. Validate digests, source identity, schema, completeness, retention, and freshness at use time.

### 10.6 High-risk actions

Force termination, destructive cleanup, session replacement, and reruns with external effects require their explicit policy/approval and verified ownership. They are not automatic responses to capacity waits, missing logs, or an uncertain result. Stop/fence credential consumers before release and before destructive operations on their resources.

### 10.7 Visibility and redaction posture

Non-admin visibility remains owner-scoped. Unauthorized direct refs must not leak target existence. Administrative scope does not bypass artifact policy. Cache and asynchronous response identity include principal and exact target/result scope.

## 11. Remediation action registry

### 11.1 Rationale

Extend the existing typed registry and subsystem owners. An action is a scoped primitive, not another native implementation of portable Skill reasoning.

### 11.2 Canonical action kinds

Keep existing execution pause/resume/cancel/force-terminate/rerun, session controls, provider/host/lease reconciliation, helper-container, targeted cleanup, and Checkpoint Branch families. Do not promise every registered family is executable. Enable only operations with real owning adapters, authorization, evidence, and the required verifier.

The historical `execution.retry_failed_step_with_remediation_context` name must not create a competing corrective retry engine. New corrective authoring uses explicit Checkpoint Branch intent. Any retained historical request is either deliberately translated under its recorded contract to a separately authorized branch or rejected before mutation. Keep compatibility only for demonstrated persisted consumers.

### 11.3 Action semantics notes

`execution.resume` resumes paused orchestration; it is not checkpoint recovery. A supported active-run rerun may Continue-As-New; terminal rerun creates a linked fresh execution and cannot update a closed Temporal run. Session operations use exact canonical session/turn/epoch capability rather than constructing a managed-session route from a runtime label for every harness.

Lease eviction or host/helper cleanup invokes its owning reconciliation mechanism. Age, workflow terminality, or an unresponsive process is not sufficient proof that a credential resource can be released or an unrelated workspace deleted. Targeted janitor actions cannot silently become global cleanup.

`checkpoint_branch.create_from_remediation_context` shares the public branch-turn execution owner. Its response distinguishes persisted graph, accepted launch, active/terminal turn, and verification. It never reports verified repair merely because creation succeeded.

### 11.4 Action request contract

Persist action ID and kind, authenticated actor/remediator, exact target workflow/run/resource, expected state/generation, normalized parameter digest, selected policy/approval/lock authority, stable idempotency key, dry-run meaning, before evidence, and verification contract. Bound all payloads. An unchanged key with changed semantic input is a conflict, not a replay.

### 11.5 Action result contract

Distinguish accepted/queued, applied, no-op, approval-required, denied, precondition-failed, delivery-unknown, timed-out, failed, and canceled states according to the providing schema. Include the canonical operation and exact resulting workflow/run/branch/turn/resource identity, before/after refs, and unfinished verification/cleanup obligations.

Transport acceptance is not effect completion. A lookup failure or lost acknowledgment cannot be turned into a safe no-op or a fresh mutation request. Reconcile the original operation identity first.

### 11.6 Risk tiers and verification

Every mutating action declares low/medium/high risk, preconditions, an evidence owner, postcondition, stabilization/deadline policy, and required verification scope. A contract may prove operational control completion without proving the original business objective. Expose that distinction.

### 11.6.1 Trusted post-action verification phase

Preserve the existing typed verification phase and outcome vocabulary: `verified_resolved`, `verified_no_change`, `still_failed`, `regressed`, `evidence_unavailable`, `approval_required`, `verification_failed`, and `canceled`. A separate lifecycle field represents verification that is pending or waiting for a result; do not overload a terminal outcome to mean that future work is still running.

Persist the action result before verification. Read fresh owner-backed evidence, never an adapter-supplied `verification` mapping or the pre-action cache. Bind the verifier to exact action, source, resulting execution/branch/turn, candidate digest, specification scope, and policy. Enforce identity even when a later run shares the same workflow ID.

For a repair branch or recovery workflow, verify its exact completed candidate and required downstream objective evidence. Do not require the immutable failed source row to become completed. Conversely, a later unrelated success of the source workflow cannot prove this repair. Branch process success alone is not objective verification, and an operational pause/cancel acknowledgment is not global host quiescence.

Long-running repair verification has a durable pending obligation under the existing orchestration/action owner. Bounded polls may observe progress, but an HTTP/Activity polling window is not the lifetime of the repair. A terminal event or bounded durable reconciliation resumes verification without repeating the action. Browser disconnect, worker loss, delayed terminal evidence, and lost publication of a verification artifact preserve this obligation and its deadline.

Record before, immediate-after, and stabilized observations when actually observed. Do not reconstruct a missing pre-action snapshot after retry, treat a failed later read as fresh earlier success, or invent no-change evidence for an ambiguous action. Verification-only retry does not repeat completed compute or mutation. A committed verdict is reused only for the same evidence and policy; a changed candidate requires a new verification attempt.

## 12. Locking, idempotency, and loop prevention

### 12.1 Why locks are required

Use the existing remediation link/action ledger and actual resource owners, not a second global scheduler. Several workflow IDs may contend for the same host, credential, workspace, or publication destination.

### 12.2 Lock scopes

Retain target-execution, AgentRun, managed/canonical session, provider-profile lease, and workload-container scopes as appropriate. The normal target mutation lock is exclusive. Read-only parallel diagnosis is permitted only under its evidence and budget policy.

### 12.3 Lock contract

Persist holder and exact target run/resource, generation/fence, expiry, and release/reconciliation state. A lost or expired lock prevents new mutation. Its expiry alone is not proof that the old consumer stopped. Stale completion cannot overwrite or release a replacement owner's resources.

### 12.4 Action idempotency

Bind the action key to normalized intent, target, policy, and expected state. Commit intent before effect, persist result before verification, and reconcile ambiguous acknowledgments through the effect owner. A narrow execution-update cache is not the remediation action ledger. Do not promise exactly-once external effects or billing when the external system cannot prove them.

### 12.5 Retry budget and cooldowns

Persist maximum actions, per-kind attempts, branch count, full-verifier attempts, elapsed time, model budget, cooldown, and escalation disposition. A typical policy may allow three actions and two per kind, but examples do not override admitted limits. Count repeated requests separately from newly admitted semantic attempts.

Cumulative repair attempts follow [Verification Cadence](RemediationVerificationCadence.md). No-progress uses validated candidate/evidence changes, not repeated verdict labels or changing timestamps. Checkpointless admission is limited to that document's explicit remotely verified exact-head contract and is not cold checkpoint recovery.

### 12.6 Nested remediation defaults

No self-targeting or automatic remediation of a remediator by default. Any explicitly enabled nested use has bounded depth and independently admitted scope, not inherited transitive privileges.

### 12.7 Target-change guard

Changed run, checkpoint, candidate, policy, credential/resource generation, or expected state requires an explicit conflict, new diagnosis/admission, or policy-permitted no-op. An approval cannot bless a different target. Changes to a live target do not retarget a pinned historical repair result.

## 13. Runtime lifecycle

### 13.1 Remediation phases

Use existing Workflow lifecycle plus bounded remediation phases such as collecting evidence, diagnosing, awaiting approval, acting, verifying, resolved, escalated, and failed. Waiting for capacity, target action, or verification is explicit and does not require another top-level workflow engine.

### 13.2 Recommended lifecycle

Collect evidence, diagnose, propose, acquire/revalidate mutation authority and approval, execute, verify, record repair/prevention, and release/reconcile. A diagnosis-only result may complete without mutation. Terminal escalation retains evidence and uncompleted obligations.

### 13.3 Recommended step structure

Skills own diagnosis, coherent repairs, and objective-verification semantics. Native orchestration owns scope, admission, artifact delivery, execution scheduling, and result validation. Full objective verification is attempt-scoped; each independently risky administrative effect still gets its own targeted verification.

### 13.4 Cancellation semantics

Canceling the remediator does not implicitly cancel or mutate the target. Canceling the target does not automatically cancel the remediator. Already accepted actions remain independently reconcilable. Requesting cancellation is not proof that an agent stopped.

Finalization preserves verified compute and saved evidence before failure-prone publication/reporting. Verify the required durability handoff before deleting the sole useful workspace. Stopping credential consumers and releasing model capacity is separate from retaining non-sensitive content. Failed capture has a bounded fenced retention/reconciliation disposition, not unconditional cleanup or indefinite credential retention. All janitors honor the same persisted decision.

Required checkpoint/durability policy remains owned by checkpoint, publication, and [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md). Proposed artifact-only or credentialless recovery requires the owning policy reconciliation and implementation; it is not permission to bypass a currently required remote checkpoint.

### 13.5 Rerun semantics

A new recovery execution or branch is a new result owner linked to the pinned failure. Exact rerun, edited retry, failed-step recovery, publication-only retry, and pause/resume remain distinct. Never silently turn failed recovery into fresh paid compute.

### 13.6 Continue-As-New state integrity

Preserve pinned target, source/candidate refs, immutable selection, action/approval/lock and pending verification identities, attempt/action/model budgets, cooldown/deadline, live cursor, and cleanup obligations. Retained histories use their recorded commands and digests; changes require deliberate versioning and replay evidence.

## 14. Artifacts, summaries, and audit

### 14.1 Required remediation artifacts

Reuse the existing artifact contracts and stable per-action/attempt labels:

- `reports/remediation_context.json` (`remediation.context`) and `reports/remediation_plan.json` (`remediation.plan`).
- `logs/remediation_decision_log.ndjson` (`remediation.decision_log`).
- `reports/remediation_action_request-<n>.json` and `reports/remediation_action_result-<n>.json` (`remediation.action_request` / `remediation.action_result`).
- `reports/remediation_verification-<n>.json` (`remediation.verification`) and `reports/remediation_summary.json` (`remediation.summary`).
- Immutable `remediation.approval_request` and `remediation.approval_decision` artifacts where approval is required.

Missing optional preview/report output must not erase a committed valid result. Required evidence remains an unmet obligation when absent. Preview and raw access follow [Artifact Presentation](../Artifacts/ArtifactPresentationContract.md).

### 14.2 Target-side artifacts

Link subsystem-native control, continuity/reset, diagnostics, and resource evidence rather than copying it into a new lifecycle store. Target annotations link the repair without rewriting the original outcome.

### 14.3 Remediation summary block

The summary separates source failure, action delivery, repair execution, verification target/scope/outcome, resulting workflow/branch/turn, prevention and its verification, publication, save/retention, cleanup, and remaining operator work. A final `resolved_after_action` requires the relevant verified postcondition, with operational versus objective scope explicit.

Retain diagnosis-only, no-action-needed, escalated, unsafe-to-act, lock-conflict, evidence-unavailable, and failed dispositions. `NO_COMMIT` or a prevention PR is not by itself a repair verdict.

### 14.4 Target-side linkage summary

Show inbound remediation count, latest safe status, action, verification and result links, and observation freshness. Unknown projection state is not a reason to rerun work.

### 14.5 Control-plane audit events

Record requester, action principal, source/remediator/result identities, exact policy/approval and expected-state refs, intent/result digests, timestamps, idempotency, observed before/after evidence, verification, and release disposition. Keep metadata bounded, secret-safe, and authorized. Artifacts supply detailed evidence; audit records provide compact queryable linkage.

## 15. Dashboard UX

### 15.1 Create flow

Offer Remediate from failure/attention/detail surfaces. Show pinned target, selected evidence, diagnosis versus approved repair intent, normal Runtime + Profile, publication choice, budget, and exact unavailable action reasons. Do not make a connection to GitHub a prerequisite for diagnosis where authorized evidence and runtime support suffice.

### 15.2 Target Workflow detail

Show immutable failure and a separate Remediation Workflows panel with pinned lineage, action/approval/lock state, linked candidate, pending/terminal verification, repair/prevention, and cleanup. A verified linked repair is visible even though the source remains failed.

### 15.3 Remediation Workflow detail

Show the Remediation Target panel, authored repair contract, evidence availability, current phase/wait, cumulative attempts and candidate head, full action authority chain, exact result identity, and required operator work. Advanced diagnostics explain execution configuration without creating extra ordinary account selectors.

### 15.4 Evidence presentation

Use existing authorized artifact renderers and server-selected default read refs. Generated content is inert untrusted content, not an executable control surface. Never fall back from a denied preview/raw read to local storage or an upstream provider URL.

### 15.5 Live follow behavior

Label live observations, reconnect/gap states, and cursor/epoch changes. Historical repair evidence remains usable independently of stream or native chat availability.

### 15.6 Operator handoff

Reuse `execution_remediation_links.approval_state` and its providing owner. Bind a request to exact action digest, target run/step/checkpoint/resource generation, immutable policy/security principal, reviewer class, expiry, and single-use state. Persist only redacted parameters and their digest. Requesters cannot approve their own request; high-risk actions require their stronger reviewer rule.

Publish request/decision artifacts idempotently, link both through action, audit, and verification, and re-resolve the persisted decision immediately before dispatch. An opaque `approvalRef` is a lookup key, not proof. Changed input under the same identity is a conflict. Expired, stale, denied, or consumed authority cannot be replayed for a new effect. Display proposal, preconditions, blast radius, approve/deny, stale reasons, and supported cancellation/takeover.

## 16. Failure modes and edge cases

Reject invisible/missing targets without leaking existence. Preserve pinned identity when the target reruns. Diagnose historical partial logs with disclosed limits. Missing artifacts, stream outage, lock contention, stale approvals, removed Profiles, and unavailable action owners have distinct bounded outcomes.

An already-released lease or absent container is a verified no-op only when the owning subsystem proves the relevant identity and disposition. A failed lookup is unknown. Force termination is never a generic fallback. If the remediator fails, persist what was delivered, what still requires verification/cleanup, and how the existing reconciler can resume it without another mutation.

## 17. Recommended v1

Manual creation, pinned targets, artifact-first context, bounded evidence tools, `observe_only` / `approval_gated`, a small **actually qualified** action subset, exclusive mutation authority, independent verification, and full audit form the minimum useful product.

For coding repair, prioritize unchanged-input recovery and fresh-session Checkpoint Branch repair through the generic runtime. Do not require every optional administrator action, static host topology, unrelated connector feature, or legacy retirement stage before fixing those journeys. Missing capabilities stay truthfully unavailable until their own owners are ready.

## 18. Future extensions

Narrow automation, additional action adapters, finer conflict scopes, safe parallel diagnosis, and richer history are extensions of the existing owners. They cannot weaken target, credential, checkpoint, approval, publication, or evidence boundaries. A simpler UI does not remove distinctions required for correct execution.

## 19. Acceptance criteria

Acceptance requires actual normal authoring to admission to runtime to result wiring, not merely models, registered handlers, an iframe, or successful graph persistence.

Required journeys include unchanged-input recovery with preserved earlier steps, source-host-destroyed cold restore, corrected-instruction branch repair, explicitly changed Profile/configuration admission, cumulative multi-attempt repair, durable pending verification, cancellation/restart/ambiguous acknowledgments, stale authority, missing evidence, denied approvals, and save/publication/cleanup failure without repeated completed effects.

Verify each claimed Codex OAuth, Claude OAuth, keyed OpenCode, and credentialless OpenCode combination under its exact plan/image/materializer/host/policy evidence. Reuse shared qualification infrastructure and scenario evidence. Optional static support is separate. Source implementation, hermetic tests, real infrastructure, exact-artifact/protected-live qualification, and promotion are distinct claims.

The existing `moonmind.omnigent.remediation_matrix` and `operator-remediation-support-matrix/v1` are the release-gate owners. `tools/run_omnigent_live_conformance.py --mode remediation` and `tools/build_operator_remediation_release_evidence.py` consume independently resolvable observations; extend those owners rather than create another matrix service. Repo-verifiable Skill completion does not waive required live/operator evidence. Autonomous mutation remains closed until its own exact evidence, permission, observability, cancellation, threshold, and rollback gates pass.

Dated source observations, implementation gaps, issue disposition, and evidence links belong in the execution tracker and GitHub issues. Canonical acceptance is not completed by issue closure alone.

## Appendix A. Example action policy

An illustrative manual policy enables only the exact supported subset, requires approvals according to immutable risk rules, verifies every effect, bounds actions/elapsed time, holds exclusive mutation authority, and disables nesting. Policy names such as `admin_healer_default` do not imply `admin_auto` or availability of every registered action.

## Appendix B. Example remediation summary

```json
{
  "sourceOutcome": "failed",
  "repair": {
    "deliveryStatus": "accepted",
    "verificationStatus": "pending",
    "resultingIdentity": {
      "branchId": "repair-branch",
      "branchTurnId": "repair-turn"
    },
    "remainingOperatorWork": "Await verification of the exact repair candidate."
  },
  "prevention": {"status": "not_attempted"}
}
```

This is an explanatory result projection, not a replacement wire schema. No successful repair is asserted while verification is pending.

## Appendix C. Design rule summary

One generic execution plane. One pinned source. One explicit result owner. Separate content from authority, delivery from verification, repair from prevention, and saved work from publication/cleanup. Preserve cumulative progress. Reuse existing owners and portable Skills. Keep missing support visible. Never recover by silently changing what the user authorized.

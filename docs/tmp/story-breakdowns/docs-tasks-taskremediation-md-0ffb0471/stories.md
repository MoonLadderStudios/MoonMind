# Task Remediation Story Breakdown

- Source design: `docs/Tasks/TaskRemediation.md`
- Original source reference path: `docs/Tasks/TaskRemediation.md`
- Story extraction date: `2026-04-22T07:17:35Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

Task Remediation defines a first-class MoonMind.Run relationship for one task to troubleshoot or repair another target execution. It uses pinned workflow/run identity, artifact-first evidence, optional live observation, typed policy-bound administrative actions, named authority profiles, exclusive mutation locks, idempotent action ledgers, durable audit artifacts, Mission Control visibility, graceful degradation for partial historical evidence, and bounded future self-healing policy. The design explicitly excludes raw host shell, unrestricted Docker, arbitrary SQL, direct secret access, unbounded history import, cross-task session reuse, and treating logs as the source of truth.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Dedicated remediation relationship** — Task Remediation must model troubleshooting/repair as a first-class directed relationship that starts because a target failed, stalled, timed out, or requested attention, rather than overloading dependency success gates. Source: 1. Purpose; 2. Why a separate system is required; 5.2 Remediation is a relationship, not a dependency.
- `DESIGN-REQ-002` (requirement): **Canonical task-shaped submission contract** — Remediation tasks remain normal top-level MoonMind.Run executions and normalize create payloads into initialParameters.task.remediation / payload.task.remediation. Source: 5.1 Remediation tasks remain MoonMind.Run; 7. Submission contract.
- `DESIGN-REQ-003` (state-model): **Pinned target identity** — Each remediation targets a logical workflowId and resolves/persists a concrete runId at create time so evidence does not silently drift across reruns or continue-as-new. Source: 3. Design goals; 6. Core invariants; 7.3 Field semantics; 8.1 Why both workflowId and runId are required.
- `DESIGN-REQ-004` (integration): **Create-time validation and convenience route normalization** — The platform must validate target visibility, self-reference, target workflow support, taskRunIds, authority mode, policy compatibility, nested remediation limits, and initialize durable links; convenience APIs must expand to the canonical create contract. Source: 7.4 Create-time validation; 7.5 Convenience API.
- `DESIGN-REQ-005` (state-model): **Forward and reverse remediation read models** — The platform must support remediator-to-target and target-to-remediator lookup with pinned run identity, status, lock holder, action summary, final outcome, and Mission Control/API rendering including inbound/outbound APIs. Source: 8. Identity, linkage, and read models.
- `DESIGN-REQ-006` (artifact): **Artifact-first evidence context bundle** — A Remediation Context Builder must resolve execution detail, step ledger, observability, artifacts, continuity data, policy snapshots, and live-follow cursor state into reports/remediation_context.json with artifact_type remediation.context. Source: 3. Design goals; 9.1 Evidence sources; 9.2 Remediation Context Builder; 9.3 Context artifact shape.
- `DESIGN-REQ-007` (security): **Bounded evidence and server-mediated access** — Large evidence remains behind artifact refs or typed APIs; remediation tasks receive refs/capabilities/redacted views, not presigned URLs, raw storage keys, local paths, or unbounded bodies. Source: 6. Core invariants; 9.4 Boundedness rule; 9.5 Evidence access surface; 10.5 Artifact and log access mediation.
- `DESIGN-REQ-008` (observability): **Optional live follow is best-effort observation** — Live follow may be used only when target activity and policy support it, persists resume cursors, resumes after restarts where possible, falls back to durable logs/artifacts, and never becomes authoritative or a control channel. Source: 5.3 Control remains separate from observation; 9.6 Live follow semantics.
- `DESIGN-REQ-009` (constraint): **Fresh target health before action** — Before a side-effecting action, remediation must re-read bounded current target health and compare pinned run, current run, state, summary, and session identity to avoid stale actions. Source: 9.7 Evidence freshness before action; 12.7 Target-change guard.
- `DESIGN-REQ-010` (security): **Authority modes and security profile binding** — observe_only, approval_gated, and admin_auto define authority boundaries; elevated remediation uses a named admin remediation principal/security profile and records both requester and execution principal. Source: 10.1 Authority modes; 10.2 Execution principal.
- `DESIGN-REQ-011` (security): **Permission boundaries and redaction** — Viewing a task, creating remediation, requesting admin authority, approving high-risk actions, and inspecting audit history are separate permissions; raw secrets never appear in contexts, payloads, summaries, logs, diagnostics, or display artifacts. Source: 10.3 Permission model; 10.4 Secret handling; 10.7 Visibility and redaction posture.
- `DESIGN-REQ-012` (integration): **Typed action registry** — Side effects must go through a MoonMind-owned typed action registry with allowlisted action families, declared target type, inputs, risk tier, preconditions, idempotency, verification, audit payload shape, and explicitly unsupported raw admin operations. Source: 11. Remediation action registry.
- `DESIGN-REQ-013` (artifact): **Action request/result contracts** — Action requests and results have schemaVersion, action IDs, target identity, risk, dry-run, idempotency key, statuses, before/after refs, verification requirements, side effects, and risk-specific verification procedures. Source: 11.4 Action request contract; 11.5 Action result contract; 11.6 Risk tiers and verification.
- `DESIGN-REQ-014` (state-model): **Exclusive locks for mutation** — Mutating remediation requires explicit locks with canonical scopes, exclusive target_execution default in v1, idempotent acquisition, expiry/recovery, and explicit lock-loss handling. Source: 12.1 Why locks are required; 12.2 Lock scopes; 12.3 Lock contract.
- `DESIGN-REQ-015` (state-model): **Remediation action ledger idempotency** — Every action has a stable logical idempotency key and the remediation action ledger, not a generic execution update cache, is the canonical idempotency surface. Source: 12.4 Action idempotency.
- `DESIGN-REQ-016` (constraint): **Loop prevention and action budgets** — Remediation must prevent unbounded loops through max actions, per-kind attempts, cooldowns, terminal escalation, no self-targeting, nested remediation off by default, and automatic self-healing depth defaulting to one. Source: 6. Core invariants; 12.5 Retry budget and cooldowns; 12.6 Nested remediation defaults.
- `DESIGN-REQ-017` (state-model): **Observable remediation lifecycle** — Remediation uses existing mm_state plus bounded remediationPhase values and observable phases for lock, evidence, diagnosis, proposal, approval, action, verification, summary, release, cancellation, rerun, and continue-as-new state preservation. Source: 13. Runtime lifecycle.
- `DESIGN-REQ-018` (artifact): **Required artifacts and summaries** — Remediation publishes context, plan, decision log, action request/result, verification, summary, and run_summary remediation block with stable resolution fields. Source: 14.1 Required remediation artifacts; 14.3 Remediation summary block.
- `DESIGN-REQ-019` (observability): **Target-side artifacts and control-plane audit** — Target subsystems still produce their native continuity/control artifacts, while remediation adds a parallel audit trail and compact queryable audit events recording actor, principal, target, action, risk, approval, timestamps, and metadata. Source: 14.2 Target-side artifacts; 14.5 Control-plane audit events.
- `DESIGN-REQ-020` (requirement): **Mission Control creation and relationship UX** — Mission Control exposes create-remediation entry points, lets operators select target run/steps/mode/policy/evidence, and shows forward/reverse remediation panels with target, authority, lock, actions, approvals, and evidence links. Source: 15.1 Create flow; 15.2 Target task detail; 15.3 Remediation task detail.
- `DESIGN-REQ-021` (requirement): **Mission Control evidence, live follow, and approvals UX** — Operators can inspect context, logs, diagnostics, decision/action/verification artifacts; live follow is labeled with reconnect/cursor/epoch state; approval-gated or high-risk actions show proposal, preconditions, blast radius, and audit decisions. Source: 15.4 Evidence presentation; 15.5 Live follow behavior; 15.6 Operator handoff.
- `DESIGN-REQ-022` (constraint): **Graceful degradation and edge-case handling** — The system handles missing/not-visible targets, target reruns, historical merged-log-only evidence, partial artifacts, live-follow unavailability, lock conflicts, failed preconditions, no-op actions, inappropriate force termination, and remediator failure with structured outcomes. Source: 3. Design goals; 16. Failure modes and edge cases.
- `DESIGN-REQ-023` (migration): **Recommended v1 scope** — A practical v1 should prioritize manual creation, pinned target run, artifact-first context, evidence tools, observe_only/admin_auto modes, small action registry, exclusive target lock, and full audit artifacts. Source: 17. Recommended v1.
- `DESIGN-REQ-024` (non-goal): **Future self-healing remains policy-driven** — Automatic remediation and richer future capabilities are later layers and must remain policy-driven, bounded, artifact-first, typed, locked, audited, redaction-safe, and free of raw root-shell authority. Source: 7.6 Future automatic self-healing policy; 18. Future extensions; Appendix C. Design rule summary.

## Ordered Story Candidates

### STORY-001: Accept canonical task remediation submissions with pinned target linkage

- Short name: `remediation-submission`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 1. Purpose, 2. Why a separate system is required, 5. Architectural stance, 6. Core invariants, 7. Submission contract, 8. Identity, linkage, and read models
- Coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005
- Description: As an operator, I can create a remediation task for a target MoonMind execution and have the platform persist an explicit non-dependency relationship to the target workflow and pinned run snapshot.
- Why: This story establishes the durable contract all later remediation evidence, actions, UI, and audits depend on.
- Independent test: Submit remediation creation requests through POST /api/executions and the convenience route, then verify normalized payload shape, pinned run persistence, validation failures, forward lookup, and reverse lookup without invoking action execution.
- Dependencies: None
- Scope:
  - Normalize task-shaped create payloads into payload.task.remediation / initialParameters.task.remediation.
  - Resolve and persist target.workflowId plus concrete target.runId at create time.
  - Validate target visibility, workflow type, self-reference, selected taskRunIds, authority mode, action policy compatibility, nested limits, and convenience route normalization.
  - Persist forward and reverse remediation link data and expose inbound/outbound lookup API/read-model fields.
- Out of scope:
  - Building evidence bundles, executing actions, or adding Mission Control UI beyond API-visible link data.
- Acceptance criteria:
  - Given a valid remediation request, the created MoonMind.Run contains the canonical task.remediation object and starts without waiting for target success.
  - When runId is omitted, the backend resolves and stores the current target runId before the run starts.
  - Malformed self-targets, unsupported targets, invalid authority modes, invisible targets, invalid taskRunIds, or incompatible policies are rejected with structured errors.
  - The remediation relationship is visible from remediation-to-target and target-to-remediation read paths including pinned run identity and status fields.
  - POST /api/executions/{workflowId}/remediation expands to the same canonical create contract as POST /api/executions.
- Requirements owned:
  - Remediation is modeled separately from dependsOn and never as a success gate.
  - Canonical payload storage is nested under task.remediation.
  - The link record supports forward lookup, reverse lookup, current remediation status, lock holder, action summary, final outcome, and Mission Control/API rendering.
- Needs clarification: None

### STORY-002: Build bounded artifact-first remediation evidence bundles and tools

- Short name: `remediation-evidence`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 9. Evidence and context model, 5.3 Control remains separate from observation, 6. Core invariants
- Coverage: DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-022, DESIGN-REQ-023
- Description: As a remediation runtime, I receive a bounded MoonMind-owned evidence bundle and typed evidence tools so I can diagnose a target execution without scraping UI pages or embedding unbounded logs in workflow history.
- Why: Remediation quality depends on trustworthy evidence that remains bounded, durable, redaction-safe, and resumable.
- Independent test: Create a remediation context for a target with selected steps and observability records, then assert the artifact shape, refs, bounded excerpts, live-follow cursor behavior, fallback behavior, and no secret/path/URL leakage.
- Dependencies: STORY-001
- Scope:
  - Introduce a Remediation Context Builder activity/service that writes reports/remediation_context.json with artifact_type remediation.context.
  - Include target identity, selected steps, artifact refs, observability refs, bounded summaries, diagnosis hints, policy snapshots, lock policy snapshot, and optional live-follow cursor state.
  - Expose MoonMind-owned evidence tools for context, target artifacts, target logs, live follow, allowed actions, action execution handoff, and target verification.
  - Implement boundedness and server-mediated access rules, including no presigned URLs, raw storage keys, raw filesystem paths, or unbounded log bodies in durable context.
  - Implement optional live-follow cursor persistence, resume behavior, and durable fallback paths.
  - Require fresh target health re-read before side-effecting action handoff.
- Out of scope:
  - Defining action registry semantics beyond tool handoff and allowed-action listing.
  - Mission Control visual presentation of evidence artifacts.
- Acceptance criteria:
  - A remediation run receives a reports/remediation_context.json artifact containing the specified v1 schema fields and artifact_type remediation.context.
  - Full logs and diagnostics remain behind refs or typed read APIs; durable context contains only bounded summaries/excerpts.
  - Evidence tools can read referenced artifacts/logs through normal artifact and task-run policy checks.
  - Live follow is available only when target state, taskRunId support, and policy allow it; cursor state survives retries where possible.
  - When live follow is unavailable, the remediator can still diagnose from merged/stdout/stderr logs, diagnostics, summaries, and artifacts with evidence degradation recorded.
  - Before any side-effecting action request is submitted, the runtime re-reads current target health and target-change guard inputs.
- Requirements owned:
  - The context builder is the stable entrypoint for target evidence.
  - Live logs are observation only and never the source of truth or control channel.
  - Missing evidence degrades the task rather than causing unbounded waits.
- Needs clarification: None

### STORY-003: Enforce remediation authority modes, permissions, and redaction boundaries

- Short name: `remediation-authority`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 10. Security and authority model, 6. Core invariants, 4. Non-goals
- Coverage: DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-024
- Description: As a platform security owner, I can configure remediation authority through explicit modes, permissions, and named security profiles so privileged troubleshooting never implies raw host access or secret disclosure.
- Why: Administrative remediation is useful only if privilege boundaries are explicit, auditable, and redaction-safe.
- Independent test: Exercise create-time and action-time authorization cases for observe_only, approval_gated, and admin_auto using users with different permissions, and scan generated artifacts/payloads for prohibited secret or storage/path material.
- Dependencies: STORY-001
- Scope:
  - Implement observe_only, approval_gated, and admin_auto authorization semantics at the control-plane boundary.
  - Bind elevated remediation to named securityProfileRef/actionPolicyRef instead of ordinary user runtime identity.
  - Separate permissions for target viewing, remediation creation, admin profile request, high-risk approval, and remediation audit history inspection.
  - Enforce no raw secrets in context artifacts, workflow payloads, run summaries, logs, diagnostics, or display artifacts.
  - Prevent durable exposure of presigned URLs, storage backend keys, absolute filesystem paths, decrypted secrets, and raw secret-bearing config bundles.
  - Ensure future self-healing policy cannot bypass these authority and redaction rules.
- Out of scope:
  - Defining the concrete action registry implementation; this story only gates whether actions can be requested/executed.
- Acceptance criteria:
  - observe_only remediation can read evidence and produce diagnoses but cannot execute side-effecting actions.
  - approval_gated remediation can propose or dry-run actions but requires recorded approval before side effects.
  - admin_auto remediation can execute only allowlisted actions within policy and still respects high-risk approval rules.
  - Audit records include both requesting user/workflow and the execution principal used for privileged actions.
  - A user with target view permission alone cannot launch admin remediation or approve high-risk actions.
  - Generated context, logs, summaries, diagnostics, and artifacts do not contain raw secrets or durable raw storage/file access material.
- Requirements owned:
  - Authority is policy/profile based, not implicit root or ordinary runtime access.
  - Unauthorized direct fetches do not leak execution existence.
  - Secret redaction rules apply even to admin remediation.
- Needs clarification: None

### STORY-004: Provide a typed remediation action registry with audited request and result contracts

- Short name: `remediation-actions`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 11. Remediation action registry, 10.6 High-risk actions, 17. Recommended v1
- Coverage: DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-023, DESIGN-REQ-024
- Description: As a remediation task, I can request only typed, allowlisted administrative actions and receive durable request/result artifacts with risk, precondition, idempotency, verification, and audit data.
- Why: The design explicitly rejects raw admin consoles; operational repairs must be typed, validated, auditable, and routed through owning control planes.
- Independent test: List allowed actions for a policy, dry-run and execute representative low/medium actions through mocked owning control-plane adapters, verify action request/result artifact shape, and assert unsupported raw operations are rejected.
- Dependencies: STORY-002, STORY-003
- Scope:
  - Implement the action registry metadata model for action kind, target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, and audit payload shape.
  - Support v1 action kinds for execution pause/resume/request_rerun_same_workflow, session interrupt/clear, provider_profile.evict_stale_lease, and session.restart_container only if backed by the owning plane.
  - Represent high-risk actions such as force termination and session restart with policy approval/disable behavior.
  - Produce remediation action request and result contracts with supported statuses, before/after refs, verification hints, and side-effect summaries.
  - Reject explicitly unsupported actions such as arbitrary shell, SQL, Docker image run, volume mount, network egress changes, decrypted secret reads, and redaction bypass.
- Out of scope:
  - Implementing Mission Control approval UX or global lock ledger behavior.
- Acceptance criteria:
  - list_allowed_actions returns only policy-compatible typed action kinds with risk and input metadata.
  - execute_action validates action kind, target class, inputs, risk policy, preconditions, and idempotency key before invoking owning control-plane code.
  - Action request artifacts include schemaVersion, actionId, actionKind, requester, target workflow/run/resource, riskTier, dryRun, idempotencyKey, and bounded params.
  - Action result artifacts include status, appliedAt when applicable, before/after refs, verificationRequired, verificationHint, and bounded sideEffects.
  - Unsupported raw host, SQL, Docker, volume, network, secret-reading, or redaction-bypass operations fail fast and are audited as rejected.
  - V1 registry scope matches the recommended small action set unless a supported action is unavailable, in which case it is omitted rather than exposed as raw access.
- Requirements owned:
  - Administrative actions are MoonMind-owned typed capabilities.
  - Managed-session and Docker workload actions go through their owning control planes.
  - Every side-effecting action declares risk and verification requirements.
- Needs clarification: None

### STORY-005: Guard remediation mutations with locks, idempotency, budgets, and loop prevention

- Short name: `remediation-guards`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 12. Locking, idempotency, and loop prevention, 6. Core invariants, 9.7 Evidence freshness before action
- Coverage: DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023
- Description: As the orchestration platform, I prevent conflicting or runaway remediation by requiring mutation locks, action-ledger idempotency, cooldowns, retry budgets, nested remediation limits, and target-change checks.
- Why: Admin remediation can harm targets if duplicate, stale, or concurrent mutations are allowed.
- Independent test: Run two remediation tasks against the same target and verify only one can mutate; replay duplicate action requests and verify ledger idempotency; exceed budgets/cooldowns/nesting rules and verify structured escalation or rejection.
- Dependencies: STORY-001, STORY-004
- Scope:
  - Implement canonical remediation lock scopes and v1 exclusive target_execution mutation lock.
  - Make lock acquisition idempotent, expiring/recoverable, and explicitly surfaced on loss.
  - Implement a remediation action ledger keyed by stable logical idempotency keys.
  - Enforce max actions per target, max attempts per action kind, cooldowns, and terminal escalation conditions.
  - Block self-targeting, automatic nested remediation, unauthorized remediation of remediation tasks, and automatic self-healing depth above one by default.
  - Compare pinned run, current run, current state, summary, and session identity before action; no-op, re-diagnose, or escalate when materially changed.
- Out of scope:
  - Rendering lock state in Mission Control panels.
- Acceptance criteria:
  - Only one active remediator can hold the default exclusive target_execution mutation lock for a target.
  - Duplicate logical action requests with the same idempotency key return the canonical ledger result rather than repeating side effects.
  - Lock expiration/recovery and lock loss are explicit; a remediator does not silently continue mutating after losing a lock.
  - Retry budgets and cooldowns prevent repeated destructive actions and produce terminal escalation when exhausted.
  - Self-targeting and automatic nested remediation are rejected by default.
  - If the target materially changed since the pinned snapshot, action execution no-ops, re-diagnoses, or escalates according to policy and records the reason.
- Requirements owned:
  - The remediation action ledger is the canonical idempotency surface for actions.
  - Concurrent diagnosis may be allowed later, but concurrent mutation is not allowed by default.
  - Loop prevention is mandatory for manual and future automatic remediation.
- Needs clarification: None

### STORY-006: Publish remediation lifecycle phases, artifacts, summaries, and audit events

- Short name: `remediation-audit`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 13. Runtime lifecycle, 14. Artifacts, summaries, and audit, 16. Failure modes and edge cases
- Coverage: DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-022, DESIGN-REQ-023
- Description: As an operator or reviewer, I can inspect a remediation run from evidence collection through verification because each phase, decision, action, result, and final outcome leaves durable artifacts and queryable audit evidence.
- Why: Remediation must be explainable and recoverable even when it fails, cancels, continues as new, or mutates target subsystems.
- Independent test: Drive a remediation run through diagnosis-only, action-applied, approval-required, canceled, failed, and continue-as-new paths using fakes; assert phase transitions, artifacts, summaries, audit rows, lock release, and preserved state.
- Dependencies: STORY-002, STORY-005
- Scope:
  - Expose bounded remediationPhase values alongside existing mm_state without adding a new top-level workflow state machine.
  - Make common plan nodes observable: lock, evidence bundle, diagnose, proposal, approval, action, verification, summary, release.
  - Implement cancellation, rerun, target run rollover, and continue-as-new preservation semantics for remediation state.
  - Publish required remediation artifacts: context, plan, decision log, action requests/results, verification, summary.
  - Add stable remediation block to remediation run_summary.json.
  - Ensure target-side continuity/control artifacts remain subsystem-native and add structured control-plane audit events for remediation actions.
  - Publish final summaries and release locks on remediation failure where possible.
- Out of scope:
  - Mission Control layout for these artifacts and summaries.
- Acceptance criteria:
  - remediationPhase values reflect collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed as the run progresses.
  - Required remediation artifacts are published with expected artifact_type values and obey artifact preview/redaction metadata rules.
  - run_summary.json includes the remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lockConflicts, approvalCount, evidenceDegraded, and escalated fields.
  - Target-managed session or workload mutations continue to produce native continuity/control artifacts in addition to remediation audit artifacts.
  - Control-plane audit events record actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
  - Cancellation or remediation failure does not mutate the target except for already-requested actions and attempts final summary publication and lock release.
  - Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.
- Requirements owned:
  - Existing MoonMind.Run state remains the top-level state source.
  - Artifacts remain operator-facing deep evidence; audit rows remain compact queryable trail.
  - Target-side linkage summary metadata is available for downstream detail views.
- Needs clarification: None

### STORY-007: Show task remediation creation, evidence, locks, approvals, and links in Mission Control

- Short name: `remediation-ui`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 15. Mission Control UX, 8.5 Reverse lookup API, 14.4 Target-side linkage summary
- Coverage: DESIGN-REQ-005, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023
- Description: As an operator in Mission Control, I can create remediation tasks from relevant target surfaces and inspect target/remediator relationships, evidence, live observation, lock state, actions, approvals, and outcomes.
- Why: Operators need remediation to be visible and controllable through the product surface, not only through backend artifacts.
- Independent test: Use UI tests and API-backed fixtures to create a remediation task, view inbound/outbound panels, inspect evidence artifacts, simulate live follow reconnect/fallback, and approve/reject a high-risk action proposal.
- Dependencies: STORY-001, STORY-002, STORY-006
- Scope:
  - Add Create remediation task entry points from task detail, failed banners, attention-required surfaces, stuck surfaces, and provider-slot/session problem surfaces where applicable.
  - Let operators choose pinned target run, all vs selected steps, troubleshooting-only vs admin remediation, live-follow mode, action policy, and preview evidence attachment.
  - Show Remediation Tasks panel on target detail with links, status, authority mode, last action, resolution, and active lock badge.
  - Show Remediation Target panel on remediation detail with target link, pinned run id, selected steps, current target state, evidence bundle link, allowed actions, approval state, and lock state.
  - Provide direct access to remediation context, target logs/diagnostics, decision log, action request/result artifacts, and verification artifacts.
  - Label live follow as observation, show reconnect state, preserve sequence position on reload, show managed-session epoch boundaries, and fall back to artifacts when streaming is unavailable.
  - Support approval-gated handoff with proposed action, preconditions, blast radius, approve/reject controls, and audit decision recording.
- Out of scope:
  - Implementing backend action execution or evidence bundle generation beyond consuming their APIs.
- Acceptance criteria:
  - Operators can start remediation from target task/problem surfaces and the submission payload matches the canonical contract.
  - Target detail shows inbound remediators and active lock/action/resolution metadata.
  - Remediation detail shows target identity, pinned run, selected steps, current state, evidence, action policy, approval, and lock information.
  - Evidence links open through artifact/log APIs and honor redaction and visibility rules.
  - Live follow UI is clearly non-authoritative, resumes sequence position where possible, and falls back to durable artifacts.
  - Approval decisions for gated/high-risk actions are captured and visible in the audit trail.
- Requirements owned:
  - Mission Control makes remediation relationships visible in both directions.
  - UI cannot imply raw host/admin shell access; action surfaces are typed and policy-bound.
  - Partial evidence and live-follow unavailability must be visible without deadlocking the user flow.
- Needs clarification: None

### STORY-008: Document and enforce bounded v1 rollout and future self-healing policy constraints

- Short name: `remediation-rollout`
- Source reference: `docs/Tasks/TaskRemediation.md`; sections: 4. Non-goals, 7.6 Future automatic self-healing policy, 16. Failure modes and edge cases, 17. Recommended v1, 18. Future extensions, 19. Acceptance criteria, Appendix C. Design rule summary
- Coverage: DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024
- Description: As a product/platform owner, I can ship a constrained manual v1 and leave future automatic self-healing behind explicit bounded policy so remediation remains safe as capabilities expand.
- Why: The design includes clear non-goals and rollout constraints that must be testable so future automation does not quietly become unbounded or unsafe.
- Independent test: Run configuration/contract tests that assert v1 defaults expose only recommended capabilities, non-goals fail closed, automatic self-healing remains disabled by default, and documented edge cases map to structured degraded/escalated/no-op outcomes.
- Dependencies: STORY-001, STORY-003, STORY-004, STORY-005, STORY-006
- Scope:
  - Encode v1 feature flags/config/defaults for manual creation first, pinned target run, artifact-first context, evidence tools, observe_only/admin_auto modes, small registry, exclusive target lock, and full audit artifacts.
  - Reject or hide non-goal capabilities by default: raw host shell, unrestricted Docker daemon, arbitrary SQL, direct DB edits, unbounded workflow history imports, cross-task session reuse, automatic admin healer on every failure, secrets bypass, and treating Live Logs as source of truth.
  - Represent future automatic self-healing policy fields as disabled-by-default configuration or documented contract only, with maxActiveRemediations, templateRef, authorityMode, createMode, and trigger constraints when later enabled.
  - Add structured handling expectations for all listed edge cases so downstream implementation/test stories can assert safe terminal states.
  - Maintain acceptance criteria traceability to the original design rule summary.
- Out of scope:
  - Building the future automatic policy engine unless selected explicitly in a later story.
- Acceptance criteria:
  - Default v1 behavior supports manual remediation only and does not automatically spawn admin healers.
  - Unsupported raw admin capabilities are absent from APIs, tools, and UI and fail closed if requested.
  - Future self-healing policy fields, if accepted by schemas, remain inert unless explicitly enabled and bounded by policy.
  - All documented failure/edge cases have structured output expectations such as validation failure, evidenceDegraded, no_op, precondition_failed, lock_conflict, escalated, unsafe_to_act, or failed.
  - The implementation acceptance checklist can be traced back to each design rule without relying on future-work language for current v1 guarantees.
- Requirements owned:
  - V1 must stay useful but constrained.
  - Future extensions preserve artifact-first evidence, typed actions, explicit locks, strict audit, redaction, and no raw root shell.
  - Non-goals are enforced as guardrails, not merely documentation.
- Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` → STORY-001
- `DESIGN-REQ-002` → STORY-001
- `DESIGN-REQ-003` → STORY-001
- `DESIGN-REQ-004` → STORY-001
- `DESIGN-REQ-005` → STORY-001, STORY-007
- `DESIGN-REQ-006` → STORY-002
- `DESIGN-REQ-007` → STORY-002
- `DESIGN-REQ-008` → STORY-002
- `DESIGN-REQ-009` → STORY-002, STORY-005
- `DESIGN-REQ-010` → STORY-003
- `DESIGN-REQ-011` → STORY-003
- `DESIGN-REQ-012` → STORY-004
- `DESIGN-REQ-013` → STORY-004
- `DESIGN-REQ-014` → STORY-005
- `DESIGN-REQ-015` → STORY-005
- `DESIGN-REQ-016` → STORY-005, STORY-008
- `DESIGN-REQ-017` → STORY-006
- `DESIGN-REQ-018` → STORY-006
- `DESIGN-REQ-019` → STORY-006
- `DESIGN-REQ-020` → STORY-007
- `DESIGN-REQ-021` → STORY-007
- `DESIGN-REQ-022` → STORY-002, STORY-005, STORY-006, STORY-007, STORY-008
- `DESIGN-REQ-023` → STORY-002, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008
- `DESIGN-REQ-024` → STORY-003, STORY-004, STORY-008

## Dependencies Between Stories

- `STORY-001` depends on no prior stories.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-001.
- `STORY-004` depends on STORY-002, STORY-003.
- `STORY-005` depends on STORY-001, STORY-004.
- `STORY-006` depends on STORY-002, STORY-005.
- `STORY-007` depends on STORY-001, STORY-002, STORY-006.
- `STORY-008` depends on STORY-001, STORY-003, STORY-004, STORY-005, STORY-006.

## Out-of-Scope Items and Rationale

- No `spec.md` files or `specs/` directories are created during breakdown; specify owns that step.
- Raw host shell access, unrestricted Docker daemon access, arbitrary SQL/direct database editing, raw secret reads, durable presigned URLs/storage keys/local paths, unbounded workflow history imports, and cross-task managed-session reuse are non-goals because remediation must stay typed, policy-bound, audited, and redaction-safe.
- Automatic self-healing is future work and remains disabled/bounded by policy in v1 so failure does not silently spawn admin healers.
- Live Logs are not a source of truth or control channel; they are only optional observation with durable artifact fallback.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.

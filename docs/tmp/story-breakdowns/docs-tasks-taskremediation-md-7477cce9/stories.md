# Story Breakdown: Task Remediation

- Source design: `docs/Tasks/TaskRemediation.md`
- Original source document reference path: `docs/Tasks/TaskRemediation.md`
- Story extraction date: `2026-04-21T21:26:09Z`
- Requested output mode: `jira`

## Design Summary

Task Remediation defines a first-class MoonMind.Run relationship that lets one task troubleshoot, observe, and, when policy allows, intervene on another task or run. It keeps the target execution source of truth unchanged, pins evidence to workflowId plus runId, builds bounded artifact-backed context, and treats live logs as optional observation rather than authority. Privileged remediation is exposed through named policies, server-mediated evidence tools, typed action registry entries, exclusive locks, idempotency ledgers, durable audit artifacts, and Mission Control surfaces that make both target and remediator views inspectable. The design explicitly excludes raw host shell, unrestricted Docker, arbitrary SQL, secret bypass, unbounded history imports, cross-task session reuse, and unbounded automatic self-healing loops.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **First-class remediation relationship** - A remediation task targets another execution for investigation or repair without behaving like a success-gated dependency. Source: 1. Purpose; 2. Why a separate system is required; 5.2 Remediation is a relationship, not a dependency.
- `DESIGN-REQ-002` (integration): **Task-shaped execution contract** - Remediation remains a normal top-level MoonMind.Run with nested task.remediation semantics normalized through the existing execution create path. Source: 5.1 Remediation tasks remain MoonMind.Run; 7. Submission contract.
- `DESIGN-REQ-003` (state-model): **Durable target identity and pinned run** - The target workflowId is required and a concrete target runId is resolved and persisted so evidence cannot silently drift across reruns or continue-as-new. Source: 3. Design goals; 6. Core invariants; 7.3 Field semantics; 8.1 Why both workflowId and runId are required.
- `DESIGN-REQ-004` (requirement): **Create-time validation and policy compatibility** - The platform validates target visibility, self-reference, workflow type, selected task runs, authority mode, action policy, nested remediation limits, and durable link initialization before the run starts. Source: 7.4 Create-time validation.
- `DESIGN-REQ-005` (state-model): **Forward and reverse remediation linkage** - The system supports remediator-to-target and target-to-remediators lookup, status, lock holder, action summary, outcome, and Mission Control rendering through canonical data and derived read models. Source: 8. Identity, linkage, and read models.
- `DESIGN-REQ-006` (artifact): **Artifact-first evidence bundle** - A Remediation Context Builder creates a bounded reports/remediation_context.json artifact containing target identity, selected steps, observability refs, summaries, policies, and live-follow cursor state. Source: 9. Evidence and context model.
- `DESIGN-REQ-007` (security): **Server-mediated evidence tools** - Remediation runtimes access evidence through MoonMind-owned APIs or tools using refs and capabilities, never by scraping Mission Control or receiving storage URLs, raw keys, or local paths. Source: 9.5 Evidence access surface; 10.5 Artifact and log access mediation.
- `DESIGN-REQ-008` (observability): **Optional live follow semantics** - Live follow is allowed only when supported and policy permits it, persists resume cursors, falls back to durable artifacts, and is never the only evidence path. Source: 9.6 Live follow semantics; 15.5 Live follow behavior.
- `DESIGN-REQ-009` (constraint): **Freshness check before mutation** - Before side effects, remediation must re-read current target health and compare pinned and current state to avoid stale or silently retargeted actions. Source: 9.7 Evidence freshness before action; 12.7 Target-change guard.
- `DESIGN-REQ-010` (security): **Authority modes and named security profiles** - observe_only, approval_gated, and admin_auto authority are controlled by named policies and securityProfileRef/actionPolicyRef, with distinct permissions and audited principals. Source: 10. Security and authority model.
- `DESIGN-REQ-011` (security): **Secrets and redaction remain enforced** - Privileged remediation does not bypass secrets, artifact redaction, workflow payload hygiene, log redaction, or ownership-scoped visibility. Source: 10.4 Secret handling; 10.7 Visibility and redaction posture.
- `DESIGN-REQ-012` (integration): **Typed remediation action registry** - Administrative interventions are explicit registry actions with target type, inputs, risk tier, preconditions, idempotency, verification, and audit payload shape. Source: 11. Remediation action registry.
- `DESIGN-REQ-013` (non-goal): **No raw admin console actions** - The system does not grant arbitrary shell, SQL, Docker daemon, volume, network, secret, or redaction-bypass access to the agent. Source: 4. Non-goals; 11.7 Explicitly unsupported actions; Appendix C.
- `DESIGN-REQ-014` (state-model): **Exclusive mutation locks** - Mutating remediation actions require scoped locks, defaulting to exclusive target_execution locks, with recoverable expiry and explicit lock-loss behavior. Source: 12. Locking, idempotency, and loop prevention.
- `DESIGN-REQ-015` (state-model): **Action idempotency ledger and retry budgets** - Each side effect has a stable idempotency key, remediation-specific ledger, max action counts, per-action retry limits, cooldowns, and terminal escalation conditions. Source: 12.4 Action idempotency; 12.5 Retry budget and cooldowns.
- `DESIGN-REQ-016` (constraint): **Nested remediation and loop prevention** - Self-targeting and automatic nested remediation are disabled by default, and future automatic self-healing must be policy-driven and bounded. Source: 6. Core invariants; 12.6 Nested remediation defaults; 7.6 Future automatic self-healing policy.
- `DESIGN-REQ-017` (state-model): **Remediation lifecycle phases** - Remediation exposes bounded phases inside summaries and read models while reusing top-level MoonMind.Run state, and its common plan nodes are observable. Source: 13. Runtime lifecycle.
- `DESIGN-REQ-018` (migration): **Cancellation, rerun, and continue-as-new safety** - Cancellation affects only the remediation task unless an explicit action was requested; rerun and continue-as-new preserve pinned identity, context refs, locks, action ledger, approvals, budgets, and cursors. Source: 13.4 Cancellation semantics; 13.5 Rerun semantics; 13.6 Continue-As-New safety.
- `DESIGN-REQ-019` (artifact): **Durable remediation artifacts and summaries** - Remediation emits context, plan, decision log, action request/result, verification, and summary artifacts plus a stable remediation block in run_summary.json. Source: 14. Artifacts, summaries, and audit.
- `DESIGN-REQ-020` (observability): **Structured control-plane audit events** - Queryable audit events record actor, execution principal, remediator, target, action, risk, approval decision, timestamps, and bounded metadata. Source: 14.5 Control-plane audit events.
- `DESIGN-REQ-021` (requirement): **Mission Control create and detail UX** - Mission Control exposes creation entrypoints, evidence preview, target and remediation panels, links, status, locks, approvals, live follow state, and operator handoff. Source: 15. Mission Control UX.
- `DESIGN-REQ-022` (constraint): **Graceful degradation and edge cases** - Missing target, rerun drift, partial evidence, unavailable live follow, lock conflict, stale preconditions, already-resolved resources, unsafe force termination, and remediator failure all produce bounded outcomes. Source: 16. Failure modes and edge cases; 3. Design goals.
- `DESIGN-REQ-023` (migration): **Practical v1 scope and future extension boundary** - V1 focuses on manual creation, pinned targets, artifact-first context, owned evidence tools, observe_only/admin_auto, a small registry, exclusive locks, and audit artifacts while preserving extension paths. Source: 17. Recommended v1; 18. Future extensions.
- `DESIGN-REQ-024` (requirement): **Acceptance criteria for implementation readiness** - The design is complete when canonical contract, pinned run, context bundle, evidence tools, action registry, policy binding, locks/idempotency, audit, UI links, degradation, and bounded automation are covered. Source: 19. Acceptance criteria.

## Ordered Story Candidates

### STORY-001: Accept remediation create requests and persist target links

- Short name: `remediation-create-link`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 5.1 Remediation tasks remain MoonMind.Run, 5.2 Remediation is a relationship, not a dependency, 7. Submission contract, 8. Identity, linkage, and read models
- Dependencies: None
- Why: This is the root contract that lets downstream evidence, action, audit, and UI work reference a durable target relationship.
- Independent test: Create remediation executions through POST /api/executions and the convenience route, then assert the stored initialParameters.task.remediation, pinned runId, validation failures, and inbound/outbound link reads without requiring any action registry implementation.
- Scope:
  - Normalize task.remediation through the existing execution create path.
  - Require target.workflowId and resolve/persist target.runId at create time.
  - Validate target visibility, malformed self-reference, workflow type, taskRunIds, authority mode, policy compatibility, and nested remediation limits.
  - Persist enough link data for forward and reverse lookup plus Mission Control rendering.
- Out of scope:
  - Generating evidence bundles.
  - Executing remediation actions.
  - Automatic self-healing triggers.
- Acceptance criteria:
  - POST /api/executions accepts a task.remediation payload and stores it as initialParameters.task.remediation before MoonMind.Run starts.
  - target.workflowId is required and a concrete target.runId is resolved and persisted when omitted by the caller.
  - Malformed self-reference, unsupported target workflow types, invalid taskRunIds, unsupported authorityMode values, incompatible actionPolicyRef, and disallowed nested remediation are rejected with structured errors.
  - A remediation link record supports remediator-to-target and target-to-remediator lookup including mode, authorityMode, current status, pinned run identity, lock holder, latest action summary, and outcome fields.
  - The convenience route expands into the same canonical create contract and does not introduce a second durable payload shape.
- Requirements:
  - Remediation tasks remain MoonMind.Run executions with additional nested task.remediation semantics.
  - Remediation links are relationships, not dependsOn gates, and start independently of target success.
  - The system exposes inbound and outbound remediation lookup APIs for execution detail surfaces.
  - Canonical source data remains upstream of any derived read model.
- Source design coverage:
  - `DESIGN-REQ-001`: This story owns first-class remediation relationship through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-002`: This story owns task-shaped execution contract through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-003`: This story owns durable target identity and pinned run through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-004`: This story owns create-time validation and policy compatibility through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-005`: This story owns forward and reverse remediation linkage through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-002: Build bounded remediation context artifacts

- Short name: `remediation-context-bundle`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 9. Evidence and context model, 14.1 Required remediation artifacts, 16. Failure modes and edge cases
- Dependencies: STORY-001
- Why: A stable artifact entrypoint lets agents diagnose reliably while preserving workflow-history boundedness and artifact redaction rules.
- Independent test: Run the context builder against target executions with full, partial, and historical merged-log-only evidence and assert the generated remediation.context artifact shape, refs, boundedness, redaction posture, and degraded evidence flags.
- Scope:
  - Introduce a Remediation Context Builder activity or service.
  - Resolve execution detail, selected step ledger data, task-run observability summaries, selected artifacts, continuity artifacts, policy snapshots, and live-follow cursor seed data.
  - Write reports/remediation_context.json with artifact_type remediation.context.
  - Represent missing evidence explicitly and mark degraded evidence when needed.
- Out of scope:
  - Live streaming implementation.
  - Action execution.
  - Mission Control UI beyond artifact availability.
- Acceptance criteria:
  - A remediation task produces reports/remediation_context.json with artifact_type remediation.context before diagnosis begins.
  - The artifact includes target workflowId/runId, selected steps, observability refs, bounded summaries, diagnosis hints, policy snapshots, lock policy snapshot, and live-follow cursor state when applicable.
  - Large logs, diagnostics, provider snapshots, and evidence bodies remain behind artifact refs or observability refs rather than being embedded unbounded in the context artifact.
  - Missing artifact refs, unavailable diagnostics, and historical merged-log-only runs produce explicit degraded evidence metadata without deadlocking the remediation task.
  - The context builder never places presigned URLs, raw storage keys, absolute local filesystem paths, raw secrets, or secret-bearing config bundles in durable context.
- Requirements:
  - Evidence access is artifact-first and bounded.
  - The context bundle is the stable entrypoint for the remediation runtime.
  - Partial evidence is represented as a bounded degradation, not an infinite wait.
  - Artifact presentation and redaction contracts apply to context metadata and bodies.
- Source design coverage:
  - `DESIGN-REQ-006`: This story owns artifact-first evidence bundle through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-011`: This story owns secrets and redaction remain enforced through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-019`: This story owns durable remediation artifacts and summaries through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-022`: This story owns graceful degradation and edge cases through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-023`: This story owns practical v1 scope and future extension boundary through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-003: Expose typed evidence tools and live follow for remediators

- Short name: `remediation-evidence-tools`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 9.5 Evidence access surface for remediation tasks, 9.6 Live follow semantics, 9.7 Evidence freshness before action, 15.5 Live follow behavior
- Dependencies: STORY-002
- Why: Remediators need a controlled evidence boundary that supports active troubleshooting while preserving source-of-truth and access-policy rules.
- Independent test: Use a remediation task with a generated context artifact to call the evidence tools against active and inactive targets, simulate reconnects, and verify cursor persistence, fallback behavior, authorization checks, and fresh-health precondition reads.
- Scope:
  - Provide remediation.get_context, read_target_artifact, read_target_logs, follow_target_logs, list_allowed_actions, execute_action dry-run boundary, and verify_target evidence reads as typed capabilities or equivalent internal APIs.
  - Persist live-follow resume cursors and resume from them where possible.
  - Fall back to durable logs, diagnostics, summaries, or artifact tailing when live follow is unavailable.
  - Require a fresh bounded target health read before side-effecting action requests.
- Out of scope:
  - Implementing concrete side-effecting action handlers.
  - Rendering Mission Control live UI controls.
- Acceptance criteria:
  - Remediation runtimes can retrieve the parsed remediation.context bundle and read referenced target artifacts through normal artifact policy.
  - Target logs are readable or tail-able through typed task-run observability APIs with bounded tailLines and cursor inputs.
  - Live follow starts only when the target run is active, the selected taskRunId supports it, and policy permits it.
  - Disconnects, worker restarts, and task retries resume from durable sequence cursor state when possible.
  - When structured live history is unavailable, the system falls back to merged logs, stdout/stderr logs, diagnostics, summaries, or artifact tailing.
  - Before any side-effecting action is requested, the remediator re-reads current target health and records the precondition evidence.
- Requirements:
  - Evidence access is server-mediated through typed MoonMind-owned surfaces.
  - Live follow is additive and best effort, never the only evidence path.
  - Fresh target health must be checked before mutation.
  - Runtime capabilities do not include raw storage, filesystem, or Mission Control scraping access.
- Source design coverage:
  - `DESIGN-REQ-007`: This story owns server-mediated evidence tools through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-008`: This story owns optional live follow semantics through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-009`: This story owns freshness check before mutation through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-011`: This story owns secrets and redaction remain enforced through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-022`: This story owns graceful degradation and edge cases through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-004: Enforce authority policies and typed remediation actions

- Short name: `remediation-action-policy`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 10. Security and authority model, 11. Remediation action registry, Appendix A. Example action policy
- Dependencies: STORY-003
- Why: The system must support real intervention while preserving privilege separation, approval gates, redaction, and auditable action semantics.
- Independent test: Register the v1 action policies and actions, then exercise observe_only, approval_gated, and admin_auto requests to assert allowed, approval_required, rejected, dryRun, no-op, and precondition_failed outcomes without granting raw shell, SQL, Docker, or secret access.
- Scope:
  - Model observe_only, approval_gated, and admin_auto authority modes.
  - Bind elevated runs to named securityProfileRef and actionPolicyRef values.
  - Implement a remediation action registry with canonical v1 action kinds, risk tiers, preconditions, input validation, idempotency metadata, verification requirements, and audit payload shapes.
  - Reject explicitly unsupported raw admin operations.
- Out of scope:
  - Lock ledger implementation beyond declaring required idempotency metadata.
  - Mission Control approval UI.
  - Automatic self-healing creation.
- Acceptance criteria:
  - observe_only tasks can read evidence and suggest actions but cannot execute side-effecting actions.
  - approval_gated tasks can propose actions and dry runs, but side effects require the configured approval decision.
  - admin_auto tasks can execute only actions allowed by actionPolicyRef and still require approval for high-risk actions when policy says so.
  - Audit records include both the requesting user or workflow and the execution principal used for privileged actions.
  - Each registry action declares target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, and audit payload shape.
  - Raw host shell, arbitrary SQL, arbitrary Docker image pull/run, arbitrary volume mounts, arbitrary egress changes, decrypted secret reads, and redaction bypasses are rejected by default.
- Requirements:
  - Privileged remediation is controlled by named policy/profile binding, not implicit host or runtime access.
  - Canonical action families include execution lifecycle, managed-session, provider-profile/slot, and workload/container actions as typed operations.
  - High-risk actions are risk-tiered and can require approval or be disabled.
  - Secrets and redaction rules apply equally to elevated remediation.
- Source design coverage:
  - `DESIGN-REQ-010`: This story owns authority modes and named security profiles through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-011`: This story owns secrets and redaction remain enforced through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-012`: This story owns typed remediation action registry through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-013`: This story owns no raw admin console actions through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-023`: This story owns practical v1 scope and future extension boundary through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-005: Guard remediation mutations with locks, idempotency, and loop limits

- Short name: `remediation-mutation-guards`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 12. Locking, idempotency, and loop prevention, 16. Failure modes and edge cases
- Dependencies: STORY-004
- Why: Administrative remediation can harm shared targets if concurrent healers or stale retries repeat destructive actions.
- Independent test: Run concurrent remediators against one target and duplicate action requests with the same idempotency key, then assert only one mutator proceeds, duplicate side effects collapse to ledger results, stale locks recover, cooldowns enforce, and target run changes cause no-op, re-diagnose, or approval escalation.
- Scope:
  - Implement lock scopes and default exclusive target_execution lock behavior for mutations.
  - Persist lock identity, holder, expiration, and lock-loss state.
  - Create a remediation action ledger keyed by stable idempotency keys.
  - Enforce retry budgets, cooldowns, max actions, force-termination limits, nested remediation defaults, and target-change guards.
- Out of scope:
  - Action handler business logic beyond ledger integration.
  - Detailed Mission Control lock UI.
- Acceptance criteria:
  - Side-effecting actions acquire an idempotent mutation lock before acting, defaulting to exclusive target_execution scope in v1.
  - Lock records capture scope, target workflow/run, holder workflow/run, timestamps, expiration, and mode.
  - Stale locks expire or are recoverable, and lock loss prevents further silent mutation by the remediation task.
  - The remediation action ledger, not generic execution update caching alone, is the canonical idempotency surface for remediation actions.
  - Retry budgets enforce max total actions, max attempts per action kind, cooldowns, and terminal escalation conditions.
  - Self-targeting and automatic nested remediation are rejected by default, and automatic self-healing depth defaults to one.
  - Before acting, the system compares pinned runId, current runId, target state, summary, and session identity and then no-ops, re-diagnoses, or escalates when the target materially changed.
- Requirements:
  - Mutations require exclusive locking by default.
  - Every action request has a stable idempotency key for the intended side effect.
  - Loop prevention is enforced for nested and automatic remediation.
  - Lock conflict, stale precondition, already-released lease, and missing container cases produce explicit outcomes.
- Source design coverage:
  - `DESIGN-REQ-009`: This story owns freshness check before mutation through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-014`: This story owns exclusive mutation locks through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-015`: This story owns action idempotency ledger and retry budgets through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-016`: This story owns nested remediation and loop prevention through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-022`: This story owns graceful degradation and edge cases through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-006: Publish remediation lifecycle, audit artifacts, and summaries

- Short name: `remediation-audit-lifecycle`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 13. Runtime lifecycle, 14. Artifacts, summaries, and audit, 16.11 Remediation task itself fails
- Dependencies: STORY-005
- Why: Remediation must be observable and reviewable after the fact, especially when privileged actions were considered or applied.
- Independent test: Execute remediation scenarios for diagnosis-only, applied action, approval escalation, cancellation, continue-as-new, and remediation failure, then assert phase transitions, artifacts, run_summary remediation block, audit events, lock release attempts, and preserved cursor/ledger/approval state.
- Scope:
  - Expose remediationPhase values in summaries and read models while reusing top-level MoonMind.Run state.
  - Publish required remediation context, plan, decision log, action request/result, verification, and summary artifacts.
  - Write target-side continuity/control artifacts when owned subsystems are mutated.
  - Persist compact control-plane audit events.
  - Handle cancellation, rerun, continue-as-new, and remediator failure semantics.
- Out of scope:
  - UI rendering of artifacts.
  - Creating action handlers not needed to emit request/result artifacts.
- Acceptance criteria:
  - Read models expose remediationPhase values such as collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, and failed without replacing top-level mm_state.
  - Remediation runs publish reports/remediation_plan.json, logs/remediation_decision_log.ndjson, action request/result artifacts, verification artifacts, and reports/remediation_summary.json in addition to the context artifact.
  - reports/run_summary.json includes a stable remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lock conflicts, approval count, evidenceDegraded, and escalated fields.
  - Control-plane audit events record actor user, execution principal, remediator workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
  - Canceling remediation does not mutate the target unless a separate action already requested that mutation, and remediation attempts best-effort lock release and final audit publication.
  - Continue-as-new preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.
- Requirements:
  - Every diagnosis, plan, action request, action result, and verification result leaves durable evidence.
  - Target-side subsystem artifacts remain authoritative for subsystem control boundaries; remediation adds a parallel audit trail.
  - Final resolution values are stable and operator-visible.
  - Cancellation, rerun, and continue-as-new do not lose remediation state.
- Source design coverage:
  - `DESIGN-REQ-017`: This story owns remediation lifecycle phases through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-018`: This story owns cancellation, rerun, and continue-as-new safety through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-019`: This story owns durable remediation artifacts and summaries through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-020`: This story owns structured control-plane audit events through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-022`: This story owns graceful degradation and edge cases through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-007: Show remediation creation, evidence, links, and approvals in Mission Control

- Short name: `remediation-mission-control`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 15. Mission Control UX, 14.4 Target-side linkage summary
- Dependencies: STORY-006
- Why: Task remediation is operator-facing; users need a clear control surface for target selection, evidence preview, links, lock state, live observation, and high-risk handoff.
- Independent test: Use mocked remediation APIs in frontend tests and route tests to verify creation payloads, pinned target selection, inbound/outbound panels, evidence links, live follow labels/reconnect state, approval decisions, and redaction-safe artifact presentation.
- Scope:
  - Add create remediation entrypoints from task detail, failed banners, attention-required surfaces, stuck surfaces, and relevant provider-slot/session problem surfaces.
  - Let users choose pinned target run, step scope, authority mode, live-follow mode, action policy, and evidence preview.
  - Render target-side Remediation Tasks panel and remediation-side Remediation Target panel.
  - Expose remediation artifacts, referenced target logs/diagnostics, decision logs, action request/result artifacts, verification artifacts, live follow state, and approval handoff.
- Out of scope:
  - Backend action implementation beyond consuming existing APIs.
  - Automatic remediation policy creation.
- Acceptance criteria:
  - Mission Control exposes Create remediation task from task detail and relevant failure, attention, stuck, provider-slot, and session surfaces.
  - The create UI lets operators choose target run, selected steps, troubleshooting-only versus admin remediation, live-follow mode, action policy, and previewed evidence classes.
  - Target task detail shows remediation task links, status, authority mode, last action, resolution, and active lock badge.
  - Remediation task detail shows target execution link, pinned target runId, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
  - Evidence presentation links to remediation context, target logs and diagnostics, decision log, action request/result artifacts, and verification artifacts through server-mediated artifact views.
  - Live follow is clearly labeled as observation, shows reconnect state and sequence position, makes managed-session epoch boundaries explicit, and falls back to durable artifacts when streaming is unavailable.
  - Approval-gated and high-risk actions show proposed action, preconditions, expected blast radius, approve/reject controls, and durable audit linkage.
- Requirements:
  - Forward and reverse remediation links are visible to operators.
  - Mission Control does not imply live logs are the source of truth.
  - Operator handoff for approval is auditable.
  - Redaction-safe artifact presentation is preserved.
- Source design coverage:
  - `DESIGN-REQ-005`: This story owns forward and reverse remediation linkage through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-008`: This story owns optional live follow semantics through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-019`: This story owns durable remediation artifacts and summaries through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-021`: This story owns mission control create and detail ux through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

### STORY-008: Bound automatic self-healing policy and v1 rollout scope

- Short name: `remediation-self-healing-policy`
- Source reference path: `docs/Tasks/TaskRemediation.md`
- Source sections: 7.6 Future automatic self-healing policy, 17. Recommended v1, 18. Future extensions, Appendix C. Design rule summary
- Dependencies: STORY-001
- Why: The design explicitly separates the powerful remediation foundation from later automation; this story keeps rollout safe and traceable.
- Independent test: Verify that v1 defaults require explicit manual creation and that any remediationPolicy seed or API input is validated as disabled or proposal-only unless a bounded policy explicitly permits it; assert no automatic nested healer is created by ordinary failure paths.
- Scope:
  - Codify v1 as manual creation first with pinned targets, artifact-first context, owned evidence tools, observe_only/admin_auto, a small typed action registry, exclusive target locks, and full audit artifacts.
  - Define but do not enable automatic remediation policies with triggers, createMode, templateRef, authorityMode, and maxActiveRemediations.
  - Enforce that future automatic self-healing is policy-driven, bounded, and preserves artifact-first evidence, typed actions, explicit locks, audit, and no raw root shell rules.
- Out of scope:
  - Shipping automatic task creation from stuck detection or finish summaries in v1.
  - Historical analytics, templates, and richer registry coverage beyond documented extension points.
- Acceptance criteria:
  - V1 documentation, configuration, and behavior keep remediation creation manual unless an explicit bounded policy path is implemented.
  - The default v1 authority choices are observe_only and admin_auto, with approval_gated treated as supported only where approval surfaces are implemented.
  - The initial registry is limited to documented safe actions and excludes raw Docker or host shell access.
  - Automatic remediation policy fields are validated as bounded configuration rather than implicit failure side effects.
  - Ordinary failed tasks do not automatically spawn admin healers by default.
  - Future extensions are documented as preserving artifact-first evidence, typed actions, explicit locks, strict audit, and no raw root shell.
- Requirements:
  - Manual creation is the v1 default.
  - Automatic self-healing is future, policy-driven, bounded, and depth-limited.
  - Recommended v1 scope is explicit and testable.
  - Future work does not weaken core design rules.
- Source design coverage:
  - `DESIGN-REQ-016`: This story owns nested remediation and loop prevention through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-023`: This story owns practical v1 scope and future extension boundary through its scope, acceptance criteria, and validation surface.
  - `DESIGN-REQ-024`: This story owns acceptance criteria for implementation readiness through its scope, acceptance criteria, and validation surface.
- Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-001, STORY-007
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-003, STORY-007
- `DESIGN-REQ-009` -> STORY-003, STORY-005
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-002, STORY-003, STORY-004
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-004
- `DESIGN-REQ-014` -> STORY-005
- `DESIGN-REQ-015` -> STORY-005
- `DESIGN-REQ-016` -> STORY-005, STORY-008
- `DESIGN-REQ-017` -> STORY-006
- `DESIGN-REQ-018` -> STORY-006
- `DESIGN-REQ-019` -> STORY-002, STORY-006, STORY-007
- `DESIGN-REQ-020` -> STORY-006
- `DESIGN-REQ-021` -> STORY-007
- `DESIGN-REQ-022` -> STORY-002, STORY-003, STORY-005, STORY-006
- `DESIGN-REQ-023` -> STORY-002, STORY-004, STORY-008
- `DESIGN-REQ-024` -> STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-004
- `STORY-006` depends on: STORY-005
- `STORY-007` depends on: STORY-006
- `STORY-008` depends on: STORY-001

## Out Of Scope Items And Rationale

- Raw host shell, unrestricted Docker, arbitrary SQL, secret reads, and artifact redaction bypasses: Owned as guardrails by STORY-004 because the design explicitly rejects raw admin console behavior.
- Automatic admin healer creation for every failed task: Deferred to STORY-008 policy boundaries; v1 is manual creation first.
- Cross-task managed-session reuse and unbounded workflow-history imports: Excluded by STORY-002 and STORY-003 bounded evidence and server-mediated access requirements.
- Treating Live Logs as source of truth: Rejected by STORY-003 and STORY-007; live follow is observation only and falls back to durable evidence.
- New specs or specs/ directories during breakdown: Not created; this output is limited to docs/tmp/story-breakdowns for later specify.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Recommended First Story

`STORY-001` should run first through `/speckit.specify` because every other story depends on a durable remediation relationship, pinned target identity, and read model link contract.

## Verification Notes

- No `spec.md` files are created by this breakdown.
- No directories under `specs/` are created by this breakdown.
- TDD remains the default downstream strategy for `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- `/speckit.verify` should compare final behavior against the original design preserved from `docs/Tasks/TaskRemediation.md`.

# Story Breakdown: Task Remediation

- Source design: `Task Remediation`
- Source document reference path: `docs/Tasks/TaskRemediation.md`
- Requested path note: `docs/Tasks/TaskRemediationSystem.md` was not present in this checkout; breakdown used the readable current document `docs/Tasks/TaskRemediation.md`.
- Story extraction date: `2026-05-08T05:30:04Z`
- Output mode: `jira`

## Design Summary

Task Remediation defines a first-class MoonMind relationship where one task can troubleshoot, observe, and optionally intervene on another task or run. The design keeps remediation as a normal MoonMind.Run with nested task.remediation semantics, durable forward and reverse links, pinned target run identity, artifact-first evidence, optional live follow, typed policy-bound actions, strict authority separation, locks, idempotency, audit artifacts, and Mission Control visibility. It explicitly excludes raw host, Docker, SQL, secret, and unbounded-history access; v1 is manual, bounded, artifact-first, and focused on safe immediate repair plus reviewable long-term prevention.

## Coverage Points

- **DESIGN-REQ-001 - First-class remediation purpose and relationship** (requirement, 1. Purpose; 2. Why a separate system is required; 5.2 Remediation is a relationship, not a dependency): MoonMind must model remediation as a dedicated relationship where one task investigates or repairs another, rather than overloading dependencies.
- **DESIGN-REQ-002 - Remediation remains a normal MoonMind.Run** (constraint, 5.1 Remediation tasks remain MoonMind.Run; 5.4 Source of truth remains unchanged): Remediation must reuse the normal task execution model and layer on existing Temporal, plan, artifact, workflow-state, and projection sources of truth.
- **DESIGN-REQ-003 - Canonical task.remediation submission contract** (integration, 7. Submission contract): Create flows must normalize remediation intent into initialParameters.task.remediation with target, mode, authority, evidence, action, approval, lock, and trigger fields.
- **DESIGN-REQ-004 - Create-time validation and bounded automatic policy** (requirement, 7.4 Create-time validation; 7.6 Future automatic self-healing policy): The platform must validate target identity, visibility, run pinning, supported types, permissions, action policy, nested remediation, and bounded future automatic policy before execution.
- **DESIGN-REQ-005 - Pinned workflow and run identity** (state-model, 3. Design goals; 6. Core invariants; 8.1 Why both workflowId and runId are required): Remediation must target a logical workflowId and persist a concrete runId snapshot so evidence and action decisions do not silently drift.
- **DESIGN-REQ-006 - Durable bidirectional linkage and read model** (state-model, 8. Identity, linkage, and read models): The system must persist forward and reverse remediation links plus current status, lock, action, outcome, and UI/API read fields.
- **DESIGN-REQ-007 - Reverse lookup API** (integration, 8.5 Reverse lookup API): Execution APIs should expose inbound and outbound remediation relationships for target and remediation task views.
- **DESIGN-REQ-008 - Artifact-first remediation context builder** (artifact, 9.1 Evidence sources; 9.2 Remediation Context Builder; 9.3 Context artifact shape): A MoonMind-owned context builder must resolve target evidence and publish reports/remediation_context.json as the stable remediation entrypoint.
- **DESIGN-REQ-009 - Bounded server-mediated evidence and logs** (security, 6. Core invariants; 9.4 Boundedness rule; 9.5 Evidence access surface; 10.5 Artifact and log access mediation): Evidence must stay bounded, ref-based, policy-mediated, and accessible through typed MoonMind surfaces rather than raw URLs, paths, or UI scraping.
- **DESIGN-REQ-010 - Optional live follow with durable cursor fallback** (observability, 9.6 Live follow semantics; 15.5 Live follow behavior; 16.5 Live follow unavailable): Live follow is additive, policy-gated, cursor-resumable, clearly labeled in UI, and must fall back to durable artifacts when unavailable.
- **DESIGN-REQ-011 - Fresh target health before side effects** (constraint, 9.7 Evidence freshness before action; 12.7 Target-change guard): Before acting, remediation must reread current bounded target health and compare pinned and current target state to avoid stale assumptions.
- **DESIGN-REQ-012 - Immediate repair plus long-term prevention workflow** (requirement, 9.8 Immediate repair and prevention workflow; Appendix C): Remediation must consider the smallest safe immediate repair, verify the result, and separately analyze/prep reviewable recurrence-prevention changes when warranted.
- **DESIGN-REQ-013 - Authority modes, principals, and permissions** (security, 10. Security and authority model): Observe-only, approval-gated, and admin-auto behavior must be governed by named principals/profiles and separate permissions for viewing, creating, approving, and auditing.
- **DESIGN-REQ-014 - Secret and redaction posture** (security, 10.4 Secret handling; 10.7 Visibility and redaction posture): Privileged remediation must not expose raw secrets or bypass normal visibility, redaction, artifact, log, workflow-payload, or summary hygiene.
- **DESIGN-REQ-015 - Typed action registry and canonical action families** (integration, 11. Remediation action registry): Admin interventions must be typed allowlisted action kinds across execution lifecycle, managed-session, provider-profile, and workload control planes.
- **DESIGN-REQ-016 - Action metadata, request/result contracts, and risk verification** (artifact, 11.2 through 11.6): Every action kind must declare target type, inputs, risk, preconditions, idempotency, verification, audit shape, and durable v1 request/result evidence.
- **DESIGN-REQ-017 - Unsupported raw administrative actions** (non-goal, 4. Non-goals; 11.7 Explicitly unsupported actions): The system must reject raw host shell, SQL, unrestricted Docker, volume, network, secret, and redaction-bypass operations.
- **DESIGN-REQ-018 - Exclusive mutation locking** (state-model, 12.1 through 12.3): Mutation requires explicit lock scopes with idempotent acquisition, expiration/recovery, lock-loss surfacing, and no silent mutation after lock loss.
- **DESIGN-REQ-019 - Action idempotency ledger and loop prevention** (state-model, 12.4 through 12.6): Side-effecting actions need stable idempotency keys, a canonical remediation action ledger, retry budgets, cooldowns, escalation, and nested remediation limits.
- **DESIGN-REQ-020 - Remediation lifecycle phases and step structure** (state-model, 13. Runtime lifecycle): Remediation uses normal mm_state plus bounded remediationPhase values and observable phases such as lock, evidence, diagnosis, approval, action, verification, summary, and release.
- **DESIGN-REQ-021 - Cancellation, rerun, and Continue-As-New safety** (constraint, 13.4 through 13.6): Cancellation affects only remediation unless an explicit action has occurred; reruns retain pinned/current identity semantics; Continue-As-New preserves refs, lock, ledger, approval, budgets, and cursor.
- **DESIGN-REQ-022 - Required remediation artifacts and summaries** (artifact, 14.1 through 14.4; Appendix B): Remediation must emit context, plan, decision log, action request/result, verification, summary, target-side annotations, and stable summary/linkage blocks.
- **DESIGN-REQ-023 - Queryable control-plane audit events** (observability, 14.5 Control-plane audit events): MoonMind must persist compact structured audit events for actor, principal, remediation/target identity, action, risk, approval, timestamps, and bounded metadata.
- **DESIGN-REQ-024 - Mission Control remediation UX** (integration, 15. Mission Control UX): Mission Control must expose remediation creation, target-side and remediation-side panels, evidence access, live follow state, and operator approval handoff.
- **DESIGN-REQ-025 - Failure modes degrade safely** (requirement, 16. Failure modes and edge cases): Missing targets, reruns, partial artifacts, live follow loss, lock conflicts, stale preconditions, already-fixed resources, risky termination, and remediator failure must produce bounded outcomes.
- **DESIGN-REQ-026 - Recommended v1 scope** (migration, 17. Recommended v1): v1 should be manual, pinned, artifact-first, tool-mediated, limited to observe_only/admin_auto, a small action registry, exclusive locks, and full audit artifacts.
- **DESIGN-REQ-027 - Future extensions remain bounded** (non-goal, 18. Future extensions): Automatic policies, proposal review, richer locks, analytics, templates, and suggestions are later work that must preserve artifact-first evidence, typed actions, locks, audit, and no raw root shell.
- **DESIGN-REQ-028 - Document-level acceptance criteria** (requirement, 19. Acceptance criteria): The design is implementation-ready only when canonical contract, dependency distinction, pinned runs, context/evidence tools, registry, profiles, locks, artifacts, UI links, degradation, bounded automation, immediate repair, and prevention are covered.

## Ordered Story Candidates

### STORY-001: Create canonical remediation submissions with durable target links

- Short name: `canonical-remediation-links`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 1. Purpose, 2. Why a separate system is required, 5. Architectural stance, 6. Core invariants, 7. Submission contract, 8. Identity, linkage, and read models
- Dependencies: None
- Independent test: Submit remediation create requests for valid, missing-target, invisible-target, self-reference, unsupported authority, omitted-runId, and selected-taskRunId cases, then assert canonical task.remediation normalization, pinned run persistence, link rows, reverse lookup API output, and dependency-free execution start behavior.
- Needs clarification: None
- Scope:
  - Normalize all create surfaces into initialParameters.task.remediation.
  - Validate target workflow, visibility, unsupported target types, self/nested remediation limits, authority mode, action policy compatibility, selected taskRunIds, and resolved runId.
  - Persist durable forward and reverse remediation link data with status, lock, last action, outcome, and read model fields.
  - Expose inbound and outbound remediation lookup surfaces for execution detail views.
- Out of scope:
  - Building evidence bundles or action execution.
  - Automatic remediation policy execution beyond validating bounded policy shape.
- Acceptance criteria:
  - Given a valid remediation create request, the backend persists task.remediation with workflowId and resolved runId before MoonMind.Run starts.
  - Given invalid target, visibility, self-reference, nested, authority, action policy, or taskRunIds input, create fails early with a structured remediation error and no null-target task is created.
  - Given an execution with inbound or outbound remediation links, the API returns direction-specific link records with status, authority mode, lock, last action, resolution, and timestamps.
  - Remediation links are modeled separately from task dependencies and do not wait for target success.
- Owned design coverage:
  - `DESIGN-REQ-001`: Owns the dedicated remediation relationship and dependency distinction.
  - `DESIGN-REQ-002`: Owns reuse of MoonMind.Run and existing sources of truth.
  - `DESIGN-REQ-003`: Owns canonical task.remediation contract normalization.
  - `DESIGN-REQ-004`: Owns create-time validation and bounded future automatic policy validation.
  - `DESIGN-REQ-005`: Owns workflowId/runId target pinning.
  - `DESIGN-REQ-006`: Owns durable bidirectional linkage/read model.
  - `DESIGN-REQ-007`: Owns reverse lookup API.

### STORY-002: Build bounded remediation evidence context and live follow tools

- Short name: `remediation-evidence-context`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 9. Evidence and context model, 10.5 Artifact and log access mediation, 15.4 Evidence presentation, 15.5 Live follow behavior, 16.3 Historical target has only merged logs, 16.4 Missing or partial artifact refs, 16.5 Live follow unavailable
- Dependencies: STORY-001
- Independent test: Create a remediation context for targets with full evidence, partial artifacts, merged-log-only history, inactive live follow, and active live follow, then assert bounded JSON shape, artifact refs instead of raw URLs/paths, durable cursor behavior, and degraded-evidence flags.
- Needs clarification: None
- Scope:
  - Introduce a Remediation Context Builder service or activity that writes reports/remediation_context.json.
  - Resolve execution detail, step ledger, task-run observability, artifact refs, diagnostics, provider snapshots, continuity refs, policies, and live-follow cursor state.
  - Expose typed remediation.get_context, read_target_artifact, read_target_logs, follow_target_logs, and verify_target surfaces.
  - Bound embedded evidence and degrade with explicit unavailable-evidence metadata.
- Out of scope:
  - Side-effecting remediation actions.
  - Mission Control rendering beyond providing artifact/tool data needed by UI.
- Acceptance criteria:
  - reports/remediation_context.json is generated up front and linked to the remediation task.
  - The context artifact contains refs and compact summaries, not unbounded logs, presigned URLs, raw storage keys, local paths, or secrets.
  - Live follow starts only when target activity, taskRun support, and policy permit it; otherwise logs and artifacts remain available.
  - Evidence access is through typed MoonMind-owned surfaces, and every missing evidence class is recorded without deadlock.
- Owned design coverage:
  - `DESIGN-REQ-008`: Owns context builder and canonical context artifact.
  - `DESIGN-REQ-009`: Owns bounded server-mediated evidence access.
  - `DESIGN-REQ-010`: Owns live follow semantics and fallback.
  - `DESIGN-REQ-025`: Owns partial evidence and live-follow unavailable degradation for evidence flows.

### STORY-003: Enforce remediation authority, policy profiles, and secret-safe access

- Short name: `remediation-authority-policy`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 10. Security and authority model, 11.7 Explicitly unsupported actions, Appendix C. Design rule summary
- Dependencies: STORY-001
- Independent test: Exercise remediation evidence/action requests under observe_only, approval_gated, admin_auto, missing permission, admin profile mismatch, non-admin visibility, and secret-bearing artifact cases, then assert deny/approval/executable decisions and redacted outputs.
- Needs clarification: None
- Scope:
  - Model observe_only, approval_gated, and admin_auto authority enforcement.
  - Bind elevated remediation to named securityProfileRef/actionPolicyRef and record requesting user plus execution principal.
  - Enforce distinct permissions for viewing targets, creating remediation, requesting admin profiles, approving high-risk actions, and inspecting audit history.
  - Reject raw secret, host, Docker, SQL, storage, network, and redaction-bypass access through remediation surfaces.
- Out of scope:
  - Implementing individual typed action execution semantics.
  - Mission Control approval UI; this story provides backend decisions/contracts that UI can consume.
- Acceptance criteria:
  - Observe-only remediators can read allowed evidence and suggest actions but cannot execute side effects.
  - Admin remediation uses a named principal/profile and records both requester and effective privileged principal.
  - Users cannot create admin remediation, approve high-risk actions, or inspect audit history without explicit permission.
  - No remediation context, payload, artifact preview, log, diagnostic, or summary exposes raw secrets or unauthorized target existence.
  - Unsupported raw operations fail closed through policy, not through hidden fallback execution.
- Owned design coverage:
  - `DESIGN-REQ-013`: Owns authority modes, principals, and permissions.
  - `DESIGN-REQ-014`: Owns secret handling and redaction posture.
  - `DESIGN-REQ-017`: Owns explicit raw-admin non-goals and denials.

### STORY-004: Provide typed remediation action registry and v1 action evidence contracts

- Short name: `remediation-action-registry`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 9.5 Evidence access surface for remediation tasks, 11. Remediation action registry, 17. Recommended v1, Appendix A. Example action policy
- Dependencies: STORY-003
- Independent test: List allowed actions for multiple policies and evaluate allowed, high-risk, dry-run, unsupported, missing-precondition, duplicate-key, raw-shell, raw-SQL, raw-Docker, and redaction-sensitive action requests, then verify request/result artifacts and decisions.
- Needs clarification: None
- Scope:
  - Expose remediation.list_allowed_actions and remediation.execute_action through a typed MoonMind-owned boundary.
  - Implement v1 registry metadata for execution lifecycle, managed-session, provider-profile, and workload action families in the recommended v1 subset.
  - Serialize action request/result artifacts with schemaVersion, identity, requester, target, risk, dryRun, idempotency, status, before/after refs, verification hints, and side effects.
  - Enforce risk tiers, preconditions, verification requirements, explicit unsupported-action rejection, and no raw Docker/host shell access.
- Out of scope:
  - Lock acquisition and action ledger persistence beyond consuming/declaring idempotency keys.
  - Full Mission Control approval handoff.
- Acceptance criteria:
  - Allowed action listing returns only policy-compatible typed actions with target type, inputs, risk, preconditions, idempotency, verification, and audit metadata.
  - Action request/result evidence uses durable bounded v1 contracts and contains no raw secrets or raw administrative handles.
  - High-risk actions are marked high and return approval_required, rejected, or executable according to policy.
  - Unsupported raw operations are rejected by kind before any side effect.
  - The initial registry matches the practical v1 subset unless an action is unavailable, in which case it is omitted or denied with bounded reason.
- Owned design coverage:
  - `DESIGN-REQ-015`: Owns typed registry and canonical action families.
  - `DESIGN-REQ-016`: Owns action metadata, request/result contracts, risk, and verification.
  - `DESIGN-REQ-017`: Owns raw-operation rejection at the registry boundary.
  - `DESIGN-REQ-026`: Owns v1 action-registry subset.

### STORY-005: Add remediation locks, action ledger, and loop prevention

- Short name: `remediation-lock-ledger`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 6. Core invariants, 9.7 Evidence freshness before action, 12. Locking, idempotency, and loop prevention, 16.6 Lock conflict, 16.7 Precondition no longer holds
- Dependencies: STORY-004
- Independent test: Run competing remediators and repeated action requests against the same target, stale locks, lost locks, target run changes, stale preconditions, nested remediation attempts, and cooldown exhaustion, then assert only allowed idempotent mutations occur and all conflicts are visible.
- Needs clarification: Confirm whether lock and action ledger storage should reuse execution_remediation_links/control events or require a dedicated table for query performance.
- Scope:
  - Implement lock scopes, default target_execution exclusive mutation lock, idempotent acquisition, expiration/recovery, and lock-loss surfacing.
  - Create a remediation action ledger keyed by logical intended side effect rather than relying solely on generic execution update idempotency.
  - Enforce retry budgets, per-action attempt limits, cooldowns, terminal escalation, nested remediation defaults, and target-change checks.
  - Return bounded no_op, precondition_failed, lock_conflict, approval/escalation, or failure outcomes for stale or conflicting actions.
- Out of scope:
  - Defining action kind metadata beyond consuming registry risk/precondition information.
  - Building the full remediation lifecycle UI.
- Acceptance criteria:
  - Only one remediator can hold the default mutation lock for a target execution at a time.
  - Lock acquisition and action execution are idempotent under retries and replays.
  - A remediation task stops mutating and records a bounded reason after lock loss, material target change, retry-budget exhaustion, or disallowed nested remediation.
  - Repeated identical action requests return ledger-backed decisions instead of duplicating side effects.
  - Fresh target health is checked immediately before side-effecting action evaluation.
- Owned design coverage:
  - `DESIGN-REQ-011`: Owns freshness and target-change guards before mutation.
  - `DESIGN-REQ-018`: Owns lock scopes and lock lifecycle.
  - `DESIGN-REQ-019`: Owns action ledger, budgets, cooldowns, and nested remediation prevention.
  - `DESIGN-REQ-025`: Owns lock conflict and stale-precondition edge handling.

### STORY-006: Run observable remediation lifecycle with repair and prevention outputs

- Short name: `remediation-runtime-lifecycle`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 9.8 Immediate repair and prevention workflow, 13. Runtime lifecycle, 16.11 Remediation task itself fails, Appendix C. Design rule summary
- Dependencies: STORY-002, STORY-005
- Independent test: Drive a remediation workflow through diagnosis-only, no-action-needed, approval-required, repaired-after-action, still-failed, unsafe, escalated, cancellation, target-rerun, and Continue-As-New scenarios, then assert phases, summaries, preserved refs, lock release, and prevention decision records.
- Needs clarification: None
- Scope:
  - Expose remediationPhase values within summary/read models without introducing a new top-level state machine.
  - Support recommended lifecycle nodes from lock acquisition through evidence, diagnosis, plan, approval, action, verification, summary, and release.
  - Record immediate repair candidates and outcomes separately from long-term prevention analysis and PR creation when policy/repo authority allow.
  - Preserve target identity, context refs, lock identity, action ledger, approval state, budgets, and live cursor across cancellation, rerun, and Continue-As-New boundaries.
- Out of scope:
  - Implementing the concrete action registry internals.
  - Mission Control panel rendering beyond lifecycle/read model fields.
- Acceptance criteria:
  - Remediation read models expose bounded remediationPhase values while top-level mm_state remains the normal execution state.
  - Immediate repair attempts are the smallest plausible action and are followed by verify_target with repaired/still_failed/not_attempted/unsafe/approval_required/escalated outcomes.
  - Recurrence-prevention analysis is recorded whether it creates a PR, reports findings, or determines no reviewable fix exists.
  - Cancellation of remediation does not mutate the target except for already-requested actions and still attempts lock release and final audit publication.
  - Continue-As-New preserves all remediation-critical refs and budgets.
- Owned design coverage:
  - `DESIGN-REQ-012`: Owns immediate repair plus prevention workflow.
  - `DESIGN-REQ-020`: Owns lifecycle phases and recommended step structure.
  - `DESIGN-REQ-021`: Owns cancellation, rerun, and Continue-As-New safety.
  - `DESIGN-REQ-025`: Owns remediator failure and final summary/lock-release edge handling.

### STORY-007: Publish remediation audit artifacts, summaries, and queryable events

- Short name: `remediation-audit-artifacts`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 14. Artifacts, summaries, and audit, Appendix B. Example remediation summary
- Dependencies: STORY-006
- Independent test: Run remediation outcomes covering diagnosis-only, applied action, rejected/approval-required action, degraded evidence, target-side session mutation, and failure, then assert artifact types, summary block values, target linkage summary, audit rows, redaction, and artifact presentation metadata.
- Needs clarification: None
- Scope:
  - Emit required remediation artifacts: context, plan, decision log, action request/result, verification, and summary.
  - Add stable remediation block to remediation run summaries and inbound linkage metadata for target execution detail.
  - Publish target-side continuity/control artifacts or annotations when the remediation action mutates managed sessions or workload containers.
  - Persist structured audit events for actor, principal, remediation/target identity, action, risk, approval, timestamps, and bounded metadata.
- Out of scope:
  - Mission Control page composition; this story supplies data/artifacts for UI.
  - Creating new analytics dashboards for historical remediation trends.
- Acceptance criteria:
  - Every remediation run publishes the minimum required artifact set that applies to its path and uses remediation.* artifact types.
  - Decision logs record attempted/skipped/denied/escalated repair candidates, action refs, verification refs, prevention refs or no-PR reasons.
  - Run summary includes stable remediation fields such as target ids, mode, authority, actions, immediateRepair, prevention, resolution, evidenceDegraded, and escalated.
  - Queryable audit events exist for side-effecting action decisions and contain bounded metadata only.
  - Artifact presentation obeys normal preview/redaction rules.
- Owned design coverage:
  - `DESIGN-REQ-022`: Owns required artifacts, summaries, and target-side linkage summaries.
  - `DESIGN-REQ-023`: Owns compact control-plane audit events.
  - `DESIGN-REQ-028`: Owns document acceptance criteria related to audit artifacts and summary blocks.

### STORY-008: Expose Mission Control remediation creation, review, and handoff panels

- Short name: `mission-control-remediation`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 15. Mission Control UX, 16. Failure modes and edge cases
- Dependencies: STORY-001, STORY-002, STORY-004, STORY-007
- Independent test: Use Mission Control tests to create remediation from failed and stuck task surfaces, inspect target/remediation panels for inbound/outbound links, preview evidence, follow live logs with fallback, and approve/reject a high-risk action while asserting audit-visible decisions.
- Needs clarification: None
- Scope:
  - Expose Create remediation task from task detail, failed banners, attention-required/stuck surfaces, and applicable provider-slot/session problem surfaces.
  - Let operators choose pinned run, selected steps, troubleshooting/admin mode, live-follow mode, action policy, and preview evidence attachment.
  - Show target-side Remediation Tasks and remediation-side Remediation Target panels with links, state, evidence, allowed actions, approvals, lock, action, and resolution data.
  - Provide evidence presentation, live follow state/reconnect/epoch labels, durable fallback, and approval/rejection handoff for high-risk or approval-gated actions.
- Out of scope:
  - Backend create, evidence, action, lock, lifecycle, or audit implementation except consuming their APIs/read models.
  - Automatic remediation policy authoring UI.
- Acceptance criteria:
  - Operators can start remediation from all specified task/problem surfaces and the submitted payload matches canonical task.remediation.
  - Target task detail shows remediation links, status, authority, last action, resolution, and active lock.
  - Remediation task detail shows target link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
  - Live follow UI is labeled as observation, preserves sequence position, shows reconnect/epoch state, and falls back to durable artifacts.
  - Approval handoff shows action, preconditions, expected blast radius, approve/reject controls, and persists the decision to the audit trail.
- Owned design coverage:
  - `DESIGN-REQ-010`: Owns live follow presentation expectations.
  - `DESIGN-REQ-024`: Owns Mission Control create/detail/evidence/handoff UX.
  - `DESIGN-REQ-025`: Owns UX handling for unavailable live follow, lock conflict, and other bounded failure states.
  - `DESIGN-REQ-028`: Owns acceptance criteria related to Mission Control links and degradation.

### STORY-009: Ship bounded manual remediation v1 and keep future automation policy-gated

- Short name: `manual-remediation-v1`
- Source reference: `docs/Tasks/TaskRemediation.md` sections 7.6 Future automatic self-healing policy, 17. Recommended v1, 18. Future extensions, 19. Acceptance criteria, Appendix A. Example action policy, Appendix C. Design rule summary
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008
- Independent test: Run an end-to-end manual v1 remediation scenario and a negative automatic-trigger scenario, then assert v1 defaults, no implicit self-healer creation, bounded unsupported future fields, and coverage of every document acceptance criterion through prior story evidence.
- Needs clarification: None
- Scope:
  - Gate v1 behavior to manual operator-created remediation tasks.
  - Document/configure v1 defaults for pinned run, artifact context, MoonMind evidence tools, observe_only/admin_auto, small action registry, exclusive target_execution lock, and full audit artifacts.
  - Ensure future automatic policy inputs are bounded, explicit, disabled until implemented, and do not silently spawn healers.
  - Verify the document-level acceptance criteria as an end-to-end v1 contract checklist.
- Out of scope:
  - Implementing automatic remediation triggers, proposal-based remediation review, analytics, reusable templates, or broad concurrent remediator policy.
  - Expanding action registry beyond the v1 subset unless needed by another accepted story.
- Acceptance criteria:
  - Manual creation is the only active v1 remediation creation path unless explicit policy support is later implemented.
  - v1 persists workflowId + runId, generates context up front, exposes evidence tools, supports observe_only/admin_auto, uses the v1 action subset, enforces exclusive locks, and publishes audit artifacts.
  - Automatic remediation policy fields, if accepted, are inert/bounded unless a supported policy executor exists.
  - Future extensions are documented as out of current scope and cannot bypass artifact-first evidence, typed actions, locks, audit, or no-raw-root rules.
  - A final v1 verification checklist maps each document acceptance criterion to implemented stories/tests.
- Owned design coverage:
  - `DESIGN-REQ-004`: Owns automatic policy boundedness as a v1/future boundary.
  - `DESIGN-REQ-026`: Owns recommended v1 packaging.
  - `DESIGN-REQ-027`: Owns future extension non-goals and guardrails.
  - `DESIGN-REQ-028`: Owns end-to-end acceptance checklist closure.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001, STORY-009
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-002
- `DESIGN-REQ-010` -> STORY-002, STORY-008
- `DESIGN-REQ-011` -> STORY-005
- `DESIGN-REQ-012` -> STORY-006
- `DESIGN-REQ-013` -> STORY-003
- `DESIGN-REQ-014` -> STORY-003
- `DESIGN-REQ-015` -> STORY-004
- `DESIGN-REQ-016` -> STORY-004
- `DESIGN-REQ-017` -> STORY-003, STORY-004
- `DESIGN-REQ-018` -> STORY-005
- `DESIGN-REQ-019` -> STORY-005
- `DESIGN-REQ-020` -> STORY-006
- `DESIGN-REQ-021` -> STORY-006
- `DESIGN-REQ-022` -> STORY-007
- `DESIGN-REQ-023` -> STORY-007
- `DESIGN-REQ-024` -> STORY-008
- `DESIGN-REQ-025` -> STORY-002, STORY-005, STORY-006, STORY-008
- `DESIGN-REQ-026` -> STORY-004, STORY-009
- `DESIGN-REQ-027` -> STORY-009
- `DESIGN-REQ-028` -> STORY-007, STORY-008, STORY-009

## Dependencies Between Stories

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-004
- `STORY-006` depends on: STORY-002, STORY-005
- `STORY-007` depends on: STORY-006
- `STORY-008` depends on: STORY-001, STORY-002, STORY-004, STORY-007
- `STORY-009` depends on: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008

## Out-of-Scope Items and Rationale

- Automatic remediation policies, proposal-based remediation review, richer action registry coverage, analytics, templates, and generated suggestions are future extensions owned by STORY-009 only as bounded guardrails, not as v1 implementation work.
- Raw host shell, arbitrary Docker, arbitrary SQL, direct secret reads, unbounded workflow-history import, cross-task managed-session reuse, automatic self-application of prevention changes, and Live Logs as source of truth are explicit non-goals covered by STORY-003 and STORY-004 guardrails.
- Spec creation, planning, task generation, implementation, verification, Jira issue creation, and PR work are outside this breakdown step.

## Coverage Gate

PASS - every major design point is owned by at least one story.

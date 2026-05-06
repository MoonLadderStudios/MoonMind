# Story Breakdown: Task Proposal System

- Source: `docs/Tasks/TaskProposalSystem.md`
- Original source document reference path: `docs/Tasks/TaskProposalSystem.md`
- Story extraction date: `2026-05-06T22:12:50Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The Task Proposal System defines a Temporal-native follow-up work pipeline where MoonMind generates reviewable proposals after a run, validates and stores canonical task snapshots, delivers human-review artifacts to GitHub Issues or Jira, and promotes work only after verified tracker approval. The design fixes submission payload invariants, proposal policy resolution, dedup-first delivery, external review commands and workflow states, stored payload security, skill and preset preservation, observability surfaces, worker-boundary placement, data model fields, idempotent recovery behavior, and explicit non-goals such as avoiding a dedicated MoonMind proposal queue page.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Summary): External-tracker-native proposal review - Proposals are follow-up work items surfaced through configured GitHub Issues or Jira issues rather than an internal proposal queue.
- `DESIGN-REQ-002` (state-model, 1. Summary, 2. Core Invariants): Stored task snapshot is executable source of truth - Promotion must execute MoonMind stored validated taskCreateRequest data or an artifact reference, not external issue text.
- `DESIGN-REQ-003` (integration, 1. Summary, 3. MoonMind.Run Lifecycle Integration): Temporal-native proposals stage - A dedicated proposals stage runs after execution and before finalization when both global and task-level gates enable it.
- `DESIGN-REQ-004` (requirement, 3.1 Submit-time contract, 3.1.1 Canonical submission-path normalization): Canonical nested submit-time proposal intent - All submission paths must persist task.proposeTasks, task.proposalPolicy, task.skills, and step.skills in initialParameters using the canonical nested task payload.
- `DESIGN-REQ-005` (constraint, 3.1.1 Canonical submission-path normalization): Compatibility reads are replay-only - Workflow code may read older/root proposal shapes only for replay and in-flight compatibility; new submissions must write nested task fields.
- `DESIGN-REQ-006` (state-model, 3.2 Run states): Proposal-capable run state vocabulary - The proposals state and related lifecycle vocabulary must be consistent across workflow, API, UI, summaries, issues, and docs.
- `DESIGN-REQ-007` (artifact, 3.4 Proposal generation): Generation activities are side-effect free - Generators analyze artifacts, agent results, diagnostics, skill metadata, and preset provenance while treating inputs as untrusted and avoiding commits, pushes, issues, or task creation.
- `DESIGN-REQ-008` (integration, 3.5 Proposal submission and delivery): Submission validates and delivers proposals - Submission validates candidates and skill selectors, resolves policy, normalizes origin, enforces routing, computes deduplication, persists records, and creates or updates tracker issues.
- `DESIGN-REQ-009` (observability, 3.6 Finish summary integration): Finish summaries report proposal outcomes - Run summaries and reports/run_summary.json must include requested/generated/submitted/delivered counts, delivery failures, validation errors, issue links, and dedup updates.
- `DESIGN-REQ-010` (requirement, 4. Proposal Policy): Global and per-task proposal policy resolution - Proposal policy merges global defaults with bounded task overrides for targets, limits, severity, runtime defaults, provider, GitHub, and Jira routing.
- `DESIGN-REQ-011` (state-model, 4.4 Project-targeted proposals, 4.5 MoonMind-targeted proposals): Project versus MoonMind targeting - Project proposals keep the triggering repository, while run-quality proposals route to the configured MoonMind repository with category, severity, and tag gates.
- `DESIGN-REQ-012` (state-model, 5.1 Delivery model, 13. Desired Data Model): Durable proposal delivery record - Each submitted proposal creates or updates a delivery/audit/idempotency record with provider, external identity, repository, dedup data, status, snapshot reference, origin, actors, and promotion linkage.
- `DESIGN-REQ-013` (requirement, 5.2 Dedup-first delivery): Dedup-first delivery - MoonMind derives dedup keys from repository and title, searches local/provider metadata, and updates matching open issues instead of creating duplicates.
- `DESIGN-REQ-014` (integration, 5.3 GitHub Issues delivery): GitHub Issues review contract - GitHub-delivered proposal issues require canonical title prefix, labels, hidden markers, source links, and comment commands for promotion, dismissal, deferral, and priority changes.
- `DESIGN-REQ-015` (integration, 5.4 Jira delivery): Jira issue review contract - Jira-delivered proposal issues require canonical summary prefix, ADF description, labels/custom fields, workflow states, links, and promotion/dismissal/deferral triggers.
- `DESIGN-REQ-016` (security, 5.5 External issue rendering, 11. Security and Integrity): Rendered issue text is review-only - External issue content must include review context and an explicit notice that edited text is not executable; large payloads are linked by reference.
- `DESIGN-REQ-017` (requirement, 6. Canonical Proposal Payload Contract): Canonical proposal payload contract - Stored proposals must use the canonical Temporal submit shape accepted by /api/executions, including task.runtime.mode, tool.type=skill for executable tools, and no agent_runtime tool type.
- `DESIGN-REQ-018` (constraint, 6.3 Payload rules, 8.5 Skill preservation and inheritance): Skill intent preservation - Proposal payloads preserve explicit material skill selectors without embedding skill bodies, mutable runtime materialization, or undocumented re-resolution side effects.
- `DESIGN-REQ-019` (constraint, 6.3 Payload rules, 8.6 Preset provenance preservation): Preset provenance preservation - Reliable authored preset and step source provenance is review metadata preserved in flat execution-ready payloads, never live preset dependencies or fabricated bindings.
- `DESIGN-REQ-020` (state-model, 7. Origin, Identity, and Naming): Canonical origin and identity metadata - Workflow-originated proposals use origin.source=workflow, origin.id=workflow_id, snake_case origin metadata, and workflow_id as durable source identity.
- `DESIGN-REQ-021` (security, 8. Review, Promotion, and Execution): Verified human review actions - Promotion, dismissal, deferral, reprioritization, and revision requests require provider-native verified human actors and permission checks.
- `DESIGN-REQ-022` (integration, 8.3 Promotion flow): Promotion creates a new MoonMind.Run - Promotion loads the delivery record and stored snapshot, applies bounded controls, validates payloads, submits through TemporalExecutionService.create_execution(), stores promoted identifiers, and updates the tracker.
- `DESIGN-REQ-023` (state-model, 8.4 Dismissal and deferral): Dismissal and deferral are non-executing decisions - Dismissal and deferral update delivery decision state, actor, provider event identity, note, timestamp, and external issue state without starting work.
- `DESIGN-REQ-024` (requirement, 8.7 Runtime selection): Runtime override validation - Operators can override runtime using backend-supported task-facing values, and disabled/incompatible runtimes must fail validation before workflow creation.
- `DESIGN-REQ-025` (integration, 9. API and Integration Contract): Internal proposal and recovery APIs - POST /api/proposals remains the internal submission API, with optional admin/recovery endpoints for inspection, redelivery, sync, and promotion.
- `DESIGN-REQ-026` (integration, 9.2 Webhook endpoints): Provider webhook contract - GitHub and Jira webhooks verify signatures or secrets, normalize events, verify actor permissions, enforce idempotency, avoid secret logging, and update delivery state consistently.
- `DESIGN-REQ-027` (integration, 9.4 Provider adapters, 11. Security and Integrity): Provider adapter boundary - Provider adapters encapsulate issue lookup/create/update/comment/link/read/status mapping, credentials, retries, redaction, and allowlist enforcement.
- `DESIGN-REQ-028` (observability, 10. Observability and UI Contract): Mission Control delivery visibility - Mission Control and execution details expose proposals state, counts, errors, external links, provider keys, dedup status, task previews, and promotion links.
- `DESIGN-REQ-029` (non-goal, 10.3 No dedicated proposal page): No dedicated proposal review page - A standalone MoonMind proposal queue page with primary Promote/Dismiss controls is explicitly excluded from the desired review workflow.
- `DESIGN-REQ-030` (observability, 10.4 Failure handling): Best-effort failure handling - Proposal errors do not invalidate successful parent runs; malformed candidates are skipped, retries are bounded/idempotent, partial success is visible, and provider errors are redacted.
- `DESIGN-REQ-031` (security, 11. Security and Integrity): Tracker content is untrusted - MoonMind never executes edited issue Markdown or Jira ADF, accepts only bounded controls, requires new validated revisions for larger changes, resolves credentials inside trusted code, and redacts errors/logs.
- `DESIGN-REQ-032` (integration, 12. Worker-Boundary Architecture): Worker boundary separation - Workflows orchestrate while activities perform side effects; generation, submission/delivery, and webhook/promotion run on appropriate separated fleets.

## Ordered Story Candidates

### STORY-001: Normalize proposal intent in Temporal submissions

- Short name: `proposal-intent-normalization`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 3.1 Submit-time contract, 3.1.1 Canonical submission-path normalization, 3.2 Run states
- Why: This story establishes the durable run contract that all later proposal generation and promotion behavior depends on.
- Independent test: Submit representative task payloads through API, schedule/promotion-style service calls, and Codex managed-session internal task creation, then assert persisted initialParameters.task.* fields and proposals-state gating without invoking delivery adapters.
- Dependencies: None
- Unresolved clarification markers: None
- Scope:
  - Normalize task.proposeTasks, task.proposalPolicy, task.skills, and step.skills into initialParameters.task.* for all new task submission paths.
  - Gate the proposals stage on global enablement plus initialParameters.task.proposeTasks.
  - Expose the canonical proposals lifecycle state consistently through workflow/API/UI status mapping.
  - Retain replay-only compatibility reads for older proposal shapes without using them for new submissions.
- Out of scope:
  - Generating proposals, delivering issues, or promoting proposals.
  - Creating new spec directories or Jira issues as part of breakdown.
- Acceptance criteria:
  - New submission paths write proposal opt-in and policy only to the canonical nested task payload.
  - Codex managed-session originated task creation does not rely on root-level flags, turn metadata, container environment, or adapter-local state for durable proposal intent.
  - The workflow enters proposals only when global settings and initialParameters.task.proposeTasks are both enabled.
  - Replay/in-flight compatibility reads are isolated and covered by a boundary test.
  - The proposals state vocabulary is reflected consistently in workflow payloads, API responses, UI mapping, finish summaries, and documentation references touched by the change.
- Owned coverage:
  - `DESIGN-REQ-003` - Temporal-native proposals stage
  - `DESIGN-REQ-004` - Canonical nested submit-time proposal intent
  - `DESIGN-REQ-005` - Compatibility reads are replay-only
  - `DESIGN-REQ-006` - Proposal-capable run state vocabulary
- Risks or open questions:
  - Submission surfaces may already have divergent normalization paths that require a full-codebase migration in one change.

### STORY-002: Generate and validate proposal candidates from run evidence

- Short name: `proposal-candidate-generation`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 3.4 Proposal generation, 3.5 Proposal submission and delivery, 6. Canonical Proposal Payload Contract, 12. Worker-Boundary Architecture
- Why: Proposal quality and safety depend on separating LLM-facing generation from trusted submission and enforcing canonical payload validation before side effects.
- Independent test: Run proposal generation against synthetic run artifacts and agent results, then assert generated candidates are side-effect free, canonical, redacted, and rejected when malformed skill selectors or non-flat preset payloads are present.
- Dependencies: STORY-001
- Unresolved clarification markers: None
- Scope:
  - Run proposal generation in activities that inspect run artifacts, normalized agent results, finish-summary signals, diagnostics, skill metadata references, and reliable preset provenance.
  - Treat all inputs as untrusted and keep large context artifact-backed by reference.
  - Validate taskCreateRequest shape, runtime/tool fields, skill selectors, preset provenance, secret redaction, and execution-ready flat payload rules before submission.
  - Separate generation activity placement from control-plane submission/delivery activity placement.
- Out of scope:
  - Creating GitHub or Jira issues.
  - Handling webhook approval events or promotion.
- Acceptance criteria:
  - Generation activities do not commit, push, create issues, create tasks, or mutate proposal delivery records.
  - Generated taskCreateRequest payloads validate against the canonical /api/executions task contract.
  - tool.type=skill is accepted for executable tools and tool.type=agent_runtime is rejected.
  - Explicit material skill selectors are preserved by reference or selector, not embedded as skill bodies or runtime materialization state.
  - Reliable authoredPresets and steps[].source provenance are preserved when present; absent provenance is not fabricated.
  - Activity/task-queue boundaries keep LLM-capable generation separate from trusted submission and delivery side effects.
- Owned coverage:
  - `DESIGN-REQ-007` - Generation activities are side-effect free
  - `DESIGN-REQ-008` - Submission validates and delivers proposals
  - `DESIGN-REQ-017` - Canonical proposal payload contract
  - `DESIGN-REQ-018` - Skill intent preservation
  - `DESIGN-REQ-019` - Preset provenance preservation
  - `DESIGN-REQ-032` - Worker boundary separation
- Risks or open questions:
  - Generator output may be hard to make deterministic enough for stable unit tests unless fixtures normalize timestamps and external links.

### STORY-003: Resolve proposal policy and delivery records deterministically

- Short name: `proposal-policy-delivery-records`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 4. Proposal Policy, 5.1 Delivery model, 5.2 Dedup-first delivery, 7. Origin, Identity, and Naming, 13. Desired Data Model
- Why: This story defines the audit and idempotency core of proposals independently from any specific external tracker.
- Independent test: Call proposal submission with project-targeted, MoonMind-targeted, duplicate, and policy-overridden candidates against a fake provider boundary; assert resolved policy decisions, delivery records, dedup behavior, and origin metadata without external network calls.
- Dependencies: STORY-002
- Unresolved clarification markers: None
- Scope:
  - Merge global defaults with task.proposalPolicy using documented precedence.
  - Enforce project-target and MoonMind-target routing, severity, tag, destination, capacity, and defaultRuntime rules.
  - Compute repository-aware dedup keys and hashes from canonical repository target and normalized title.
  - Create or update delivery records with provider, external identity placeholders, status, snapshot reference, origin metadata, actors, timestamps, and promotion linkage fields.
  - Use canonical origin.source, origin.id, snake_case metadata, and workflow_id identity rules.
- Out of scope:
  - Provider-specific issue creation, labels, comments, custom fields, or webhooks.
  - Promotion of approved issues.
- Acceptance criteria:
  - Policy resolution preserves explicit candidate values over defaults while enforcing allowlists, capacity limits, severity gates, and tag gates.
  - Project proposals keep the triggering repository and MoonMind run-quality proposals rewrite to the configured MoonMind repository only when category/severity/tag gates pass.
  - Dedup searches local open delivery records and provider metadata before creating a new delivery target.
  - Existing open duplicates update or link to the existing issue path instead of creating duplicate reviewer-facing records.
  - Delivery records include the canonical field set or an explicitly documented subset with provider-specific metadata separated from canonical fields.
  - Origin metadata uses origin.source=workflow, origin.id=workflow_id, and snake_case keys.
- Owned coverage:
  - `DESIGN-REQ-010` - Global and per-task proposal policy resolution
  - `DESIGN-REQ-011` - Project versus MoonMind targeting
  - `DESIGN-REQ-012` - Durable proposal delivery record
  - `DESIGN-REQ-013` - Dedup-first delivery
  - `DESIGN-REQ-020` - Canonical origin and identity metadata
- Risks or open questions:
  - Existing task_proposals storage may need careful migration to serve as a delivery/audit record without introducing compatibility aliases.

### STORY-004: Deliver proposals to GitHub and Jira review surfaces

- Short name: `external-tracker-delivery`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 5. External Proposal Delivery, 9.4 Provider adapters, 11. Security and Integrity
- Why: External tracker delivery is the primary human review experience and must be safe, idempotent, and provider-bounded.
- Independent test: Use fake GitHub and Jira adapters to submit new and duplicate proposals, then assert rendered issue fields, labels/custom fields, hidden markers, commands/workflow state metadata, issue update behavior, redaction, and destination allowlist enforcement.
- Dependencies: STORY-003
- Unresolved clarification markers: Exact default Jira issue type and custom field IDs may be deployment-specific and should be supplied by operator configuration.
- Scope:
  - Render GitHub proposal issues with canonical prefix, labels, hidden MoonMind markers, source links, dedup metadata, and reviewer commands.
  - Render Jira proposal issues with canonical prefix, ADF description, labels/custom fields, workflow states, source links, and accepted promotion/dismissal/deferral triggers.
  - Include explicit notice that edited issue text is not executable.
  - Link large payloads, logs, artifacts, and diagnostics by reference instead of embedding them.
  - Keep credentials, retries, redaction, provider issue operations, and destination allowlists inside provider adapters.
- Out of scope:
  - A MoonMind-hosted proposal review queue page.
  - Executing approved proposals or processing webhook decisions.
- Acceptance criteria:
  - GitHub issues are created or updated with [MoonMind proposal] titles, canonical labels, hidden markers, source links, dedup metadata, and reviewer action instructions.
  - Jira issues are created or updated with [MoonMind proposal] summaries, ADF descriptions, labels/custom fields, canonical workflow states, source links, and configured action triggers.
  - Issue bodies/descriptions include a clear stored-snapshot notice and never embed raw executable payload replacement instructions.
  - Provider adapters enforce repository, organization, Jira site, Jira project, and action allowlists.
  - Provider credentials and raw provider errors are never exposed to managed agent environments, logs, comments, or API responses.
- Owned coverage:
  - `DESIGN-REQ-001` - External-tracker-native proposal review
  - `DESIGN-REQ-014` - GitHub Issues review contract
  - `DESIGN-REQ-015` - Jira issue review contract
  - `DESIGN-REQ-016` - Rendered issue text is review-only
  - `DESIGN-REQ-027` - Provider adapter boundary
  - `DESIGN-REQ-031` - Tracker content is untrusted
- Risks or open questions:
  - Jira custom field and workflow configurations vary by site and may require configuration validation fixtures.

### STORY-005: Process verified tracker decisions and promote approved proposals

- Short name: `proposal-review-promotion`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 8. Review, Promotion, and Execution, 9.2 Webhook endpoints, 9.3 Admin and recovery APIs
- Why: Promotion is the critical control-plane bridge from review to execution and must be permission-checked, idempotent, bounded, and snapshot-based.
- Independent test: Replay signed fake GitHub and Jira decision events, duplicate event IDs, unauthorized actors, edited issue bodies, runtime overrides, and dismissal/deferral actions; assert only authorized promotion creates a Temporal execution from the stored snapshot.
- Dependencies: STORY-004
- Unresolved clarification markers: None
- Scope:
  - Handle GitHub and Jira webhook events with signature/shared-secret verification, provider event idempotency, event normalization, actor permission checks, and redacted diagnostics.
  - Promote only by loading the delivery record and stored task snapshot, applying bounded controls, validating payloads, preserving skill and preset intent, and calling TemporalExecutionService.create_execution().
  - Support dismissal, deferral, reprioritization, and revision-request state changes without starting work.
  - Expose admin/recovery APIs for inspection, redelivery, sync, and promote where appropriate for operators and tests.
  - Validate runtime overrides against backend-supported task runtimes before workflow creation.
- Out of scope:
  - Generating candidate proposals.
  - Rendering provider issue content except follow-up comments/transitions from decisions.
- Acceptance criteria:
  - Webhook handlers verify provider signatures or shared secrets before processing decisions.
  - Provider event IDs make webhook decision handling idempotent.
  - Actor permission checks block unauthorized promotion, dismissal, deferral, reprioritization, and revision requests.
  - Promotion ignores edited issue body text and Jira ADF, uses only the stored snapshot plus bounded controls, and creates a new MoonMind.Run through the canonical Temporal-backed create path.
  - Promotion preserves explicit skill selectors, authoredPresets, and steps[].source provenance unless a validated proposal revision changed them.
  - Dismissal and deferral record actor, provider event identity, note/reason, timestamp, and external issue state without starting execution.
  - Disabled or unsupported runtime overrides fail validation before a workflow is created.
- Owned coverage:
  - `DESIGN-REQ-002` - Stored task snapshot is executable source of truth
  - `DESIGN-REQ-021` - Verified human review actions
  - `DESIGN-REQ-022` - Promotion creates a new MoonMind.Run
  - `DESIGN-REQ-023` - Dismissal and deferral are non-executing decisions
  - `DESIGN-REQ-024` - Runtime override validation
  - `DESIGN-REQ-025` - Internal proposal and recovery APIs
  - `DESIGN-REQ-026` - Provider webhook contract
  - `DESIGN-REQ-031` - Tracker content is untrusted
- Risks or open questions:
  - Promotion touches security-sensitive webhook, auth, and Temporal boundaries and needs integration tests with realistic adapter payloads.

### STORY-006: Surface proposal outcomes in summaries and Mission Control

- Short name: `proposal-observability`
- Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 3.6 Finish summary integration, 10. Observability and UI Contract, 14. Acceptance Criteria
- Why: Operators need enough visibility to audit best-effort proposal behavior and recover failures while external trackers remain the review surface.
- Independent test: Run proposal-stage success, partial failure, duplicate update, delivery failure, and promoted-link fixtures through finish summary/API/UI projection helpers and Mission Control tests; assert visible counts, links, redacted errors, state mapping, and absence of primary queue behavior.
- Dependencies: STORY-003
- Unresolved clarification markers: None
- Scope:
  - Record proposal requested/generated/submitted/delivered/updated/failed counts and redacted validation/provider errors in typed finish results and reports/run_summary.json.
  - Expose external issue links, provider, external key, delivery status, last sync, dedup behavior, compact task summary, and promotion result links in API and Mission Control execution detail.
  - Map mm_state=proposals and related states for dashboard compatibility.
  - Ensure partial proposal success does not change a successful parent run into a failed run.
  - Exclude a standalone proposal queue page as the primary review workflow.
- Out of scope:
  - Implementing provider adapters, webhooks, or promotion.
  - Building a dedicated MoonMind proposal review page.
- Acceptance criteria:
  - Finish summaries and reports/run_summary.json include requested/generated/submitted/delivered counts, provider failures, redacted validation errors, issue links, and dedup updates.
  - Execution detail and Mission Control show provider, external key, delivery status, last sync timestamp, dedup new-or-updated status, compact task summary, and promotion result links.
  - mm_state=proposals is visible and mapped to running for dashboard compatibility.
  - Malformed candidates are skipped with visible redacted errors and do not promote or silently drop semantically important fields.
  - External delivery failures are retried idempotently and visible in summaries, delivery records, and operator diagnostics.
  - No standalone proposal queue page becomes the normal review path.
- Owned coverage:
  - `DESIGN-REQ-009` - Finish summaries report proposal outcomes
  - `DESIGN-REQ-028` - Mission Control delivery visibility
  - `DESIGN-REQ-029` - No dedicated proposal review page
  - `DESIGN-REQ-030` - Best-effort failure handling
- Risks or open questions:
  - UI tests may need stable fixtures for generated links and timestamps.

## Coverage Matrix

- `DESIGN-REQ-001` External-tracker-native proposal review: STORY-004
- `DESIGN-REQ-002` Stored task snapshot is executable source of truth: STORY-005
- `DESIGN-REQ-003` Temporal-native proposals stage: STORY-001
- `DESIGN-REQ-004` Canonical nested submit-time proposal intent: STORY-001
- `DESIGN-REQ-005` Compatibility reads are replay-only: STORY-001
- `DESIGN-REQ-006` Proposal-capable run state vocabulary: STORY-001
- `DESIGN-REQ-007` Generation activities are side-effect free: STORY-002
- `DESIGN-REQ-008` Submission validates and delivers proposals: STORY-002
- `DESIGN-REQ-009` Finish summaries report proposal outcomes: STORY-006
- `DESIGN-REQ-010` Global and per-task proposal policy resolution: STORY-003
- `DESIGN-REQ-011` Project versus MoonMind targeting: STORY-003
- `DESIGN-REQ-012` Durable proposal delivery record: STORY-003
- `DESIGN-REQ-013` Dedup-first delivery: STORY-003
- `DESIGN-REQ-014` GitHub Issues review contract: STORY-004
- `DESIGN-REQ-015` Jira issue review contract: STORY-004
- `DESIGN-REQ-016` Rendered issue text is review-only: STORY-004
- `DESIGN-REQ-017` Canonical proposal payload contract: STORY-002
- `DESIGN-REQ-018` Skill intent preservation: STORY-002
- `DESIGN-REQ-019` Preset provenance preservation: STORY-002
- `DESIGN-REQ-020` Canonical origin and identity metadata: STORY-003
- `DESIGN-REQ-021` Verified human review actions: STORY-005
- `DESIGN-REQ-022` Promotion creates a new MoonMind.Run: STORY-005
- `DESIGN-REQ-023` Dismissal and deferral are non-executing decisions: STORY-005
- `DESIGN-REQ-024` Runtime override validation: STORY-005
- `DESIGN-REQ-025` Internal proposal and recovery APIs: STORY-005
- `DESIGN-REQ-026` Provider webhook contract: STORY-005
- `DESIGN-REQ-027` Provider adapter boundary: STORY-004
- `DESIGN-REQ-028` Mission Control delivery visibility: STORY-006
- `DESIGN-REQ-029` No dedicated proposal review page: STORY-006
- `DESIGN-REQ-030` Best-effort failure handling: STORY-006
- `DESIGN-REQ-031` Tracker content is untrusted: STORY-004, STORY-005
- `DESIGN-REQ-032` Worker boundary separation: STORY-002

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-004
- `STORY-006` depends on: STORY-003

## Out-of-Scope Items and Rationale

- Creating or modifying `spec.md` files is out of scope for breakdown; each story candidate can become one future specify input.
- Creating directories under `specs/` is out of scope until `/speckit.specify`.
- Creating Jira issues is out of scope for this breakdown; output mode `jira` means the story summaries and fields are shaped for downstream Jira creation.
- Implementing the proposal system is out of scope for breakdown; downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement` should remain TDD-first.
- A dedicated MoonMind proposal review page is excluded by the source design because GitHub Issues and Jira are the primary review surfaces.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.

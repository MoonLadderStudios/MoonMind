# Task Proposal System Story Breakdown

Source design: `docs/Tasks/TaskProposalSystem.md`
Story extraction date: 2026-05-16T09:00:28+00:00
Requested output mode: jira

## Design Summary

The Task Proposal System defines a Temporal-native, external-tracker-native architecture for reviewable follow-up work discovered during MoonMind runs. Candidate proposals are generated safely from run evidence, validated and normalized into canonical taskCreateRequest snapshots, deduplicated through durable delivery records, and delivered to GitHub Issues or Jira for human review. Promotion is intentionally separated from creation: verified tracker decisions load MoonMind's stored snapshot plus bounded reviewer controls, preserve skill and preset intent, and create a new MoonMind.Run only after validation. Mission Control exposes delivery status, links, summaries, failures, and promotion results without becoming the primary proposal queue.

## Coverage Points

- `DESIGN-REQ-001` **External-tracker-native proposal review** (requirement, 1. Summary): Proposals are reviewed in GitHub Issues or Jira, not in a dedicated MoonMind task proposal page.
- `DESIGN-REQ-002` **Stored taskCreateRequest is executable source of truth** (state-model, 1. Summary; 2. Core Invariants; 6. Canonical Proposal Payload Contract): Promotion executes MoonMind's stored validated task snapshot or artifact reference, not edited tracker content.
- `DESIGN-REQ-003` **Temporal-native proposal lifecycle** (integration, 1. Summary; 3. MoonMind.Run Lifecycle Integration): MoonMind.Run has a proposals stage after execution and before finalization, with generation and submission in Temporal activities.
- `DESIGN-REQ-004` **Core proposal safety invariants** (constraint, 2. Core Invariants): Creation is not execution, promotion starts a new MoonMind.Run, proposal payloads conform to the Temporal submit contract, and credentials stay in trusted boundaries.
- `DESIGN-REQ-005` **Canonical nested submit-time proposal fields** (requirement, 3.1 Submit-time contract; 3.1.1 Canonical submission-path normalization): New task creation paths persist proposal intent in initialParameters.task.proposeTasks and task.proposalPolicy, not adapter-local or root-only fields.
- `DESIGN-REQ-006` **Proposal state vocabulary** (state-model, 3.2 Run states; 10.1 Status mapping): The proposals state is first-class across workflow state, API responses, Mission Control, finish summaries, and external updates.
- `DESIGN-REQ-007` **Proposal stage gating** (requirement, 3.3 When the proposals stage runs): The proposals stage runs only when global proposal generation is enabled and task.proposeTasks opts in.
- `DESIGN-REQ-008` **Side-effect-free proposal generation** (security, 3.4 Proposal generation): Generator activities inspect run evidence, treat inputs as untrusted, use artifact refs for large context, redact secrets, and avoid side effects.
- `DESIGN-REQ-009` **Validated proposal submission and delivery** (integration, 3.5 Proposal submission and delivery): Submission validates candidates, resolves routing, normalizes origin, computes dedup fields, stores delivery records, and creates or updates tracker issues without promotion.
- `DESIGN-REQ-010` **Finish summary proposal outcomes** (observability, 3.6 Finish summary integration): Finish summaries and reports/run_summary.json expose requested, generated, submitted, delivered, failure, error, issue-link, and dedup-update outcomes.
- `DESIGN-REQ-011` **Proposal policy resolution** (requirement, 4. Proposal Policy): Global defaults and task overrides determine targets, caps, severity floors, runtime defaults, tracker provider, and destination allowlist decisions.
- `DESIGN-REQ-012` **Project and MoonMind target routing** (requirement, 4.4 Project-targeted proposals; 4.5 MoonMind-targeted proposals): Project proposals keep the triggering repository; MoonMind run-quality proposals rewrite to the configured MoonMind repository only after category, severity, and tag gates.
- `DESIGN-REQ-013` **Delivery record and dedup model** (state-model, 5.1 Delivery model; 5.2 Dedup-first delivery; 13. Desired Data Model): Each proposal has a durable delivery, audit, idempotency, dedup, status, origin, decision, and promotion record.
- `DESIGN-REQ-014` **GitHub Issues delivery contract** (integration, 5.3 GitHub Issues delivery): GitHub delivery uses canonical title prefixes, labels, hidden markers, source links, reviewer instructions, and commands.
- `DESIGN-REQ-015` **Jira delivery contract** (integration, 5.4 Jira delivery): Jira delivery uses prefixed summaries, ADF descriptions, labels/custom fields, workflow states, transitions, comments, and issue links.
- `DESIGN-REQ-016` **External issue rendering limits** (artifact, 5.5 External issue rendering): Issue text renders review metadata and links large payloads by reference, with notice that stored snapshots are executed instead of edited issue text.
- `DESIGN-REQ-017` **Canonical payload validation rules** (requirement, 6. Canonical Proposal Payload Contract): Stored proposals validate as normal task payloads, use supported runtimes and skill selectors correctly, avoid secrets and inline skill bodies, and are execution-ready flat payloads.
- `DESIGN-REQ-018` **Origin identity and metadata naming** (state-model, 7. Origin, Identity, and Naming): Workflow-originated proposals use origin.source workflow, workflow_id as durable identity, temporal_run_id as diagnostics, and snake_case metadata keys.
- `DESIGN-REQ-019` **Verified reviewer decision handling** (security, 8. Review, Promotion, and Execution; 9.2 Webhook endpoints): Promotion, dismissal, deferral, reprioritization, and revision requests require verified provider events, actor permission checks, idempotency, and redaction.
- `DESIGN-REQ-020` **Promotion creates a new Temporal execution** (integration, 8.3 Promotion flow): Promotion loads the delivery record and snapshot, applies bounded controls, validates, preserves intent, creates a MoonMind.Run, records the execution ID, and updates the tracker issue.
- `DESIGN-REQ-021` **Dismissal and deferral audit** (state-model, 8.4 Dismissal and deferral): Non-promoting decisions record actor, provider event, reason, timestamp, and external issue state.
- `DESIGN-REQ-022` **Skill and preset preservation** (constraint, 8.5 Skill preservation and inheritance; 8.6 Preset provenance preservation): Promotion preserves explicit skill intent and reliable preset provenance without silently re-resolving skill precedence or re-expanding live presets.
- `DESIGN-REQ-023` **Runtime override validation** (requirement, 8.7 Runtime selection; 8.8 Promotion result): Promotion can apply bounded runtime overrides using backend-supported runtime values, and disabled runtimes fail before workflow creation.
- `DESIGN-REQ-024` **Internal APIs and provider adapters** (integration, 9. API and Integration Contract): Internal proposal submission, webhook, admin/recovery APIs, and common provider adapters form the integration surface.
- `DESIGN-REQ-025` **Mission Control proposal visibility** (observability, 10. Observability and UI Contract): Execution details show proposal delivery status, links, counts, compact task summaries, and promotion links while avoiding a primary proposal queue page.
- `DESIGN-REQ-026` **Best-effort failure behavior** (observability, 10.4 Failure handling): Proposal failures do not corrupt parent run results; malformed candidates are skipped, retries are bounded/idempotent, and redacted errors remain visible.
- `DESIGN-REQ-027` **External tracker security and integrity** (security, 11. Security and Integrity): Tracker content is untrusted; MoonMind executes stored snapshots plus bounded controls and enforces signatures, allowlists, permissions, just-in-time credentials, and redaction.
- `DESIGN-REQ-028` **Worker-boundary architecture** (constraint, 12. Worker-Boundary Architecture): Workflows orchestrate, activities perform side effects, and generation, submission, delivery, webhook, and promotion run on appropriate trusted fleets.
- `DESIGN-REQ-029` **End-to-end acceptance criteria** (requirement, 14. Acceptance Criteria): The desired system satisfies one issue per dedup target, external review actions, stored-snapshot promotion, visible links and failures, dedup updates, provider allowlists, and Mission Control status display.

## Ordered Story Candidates

### STORY-001: Normalize proposal-capable submissions into the canonical Temporal run contract

Short name: `canonical-proposal-submit`
Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 3.1 Submit-time contract, 3.1.1 Canonical submission-path normalization, 3.2 Run states, 3.3 When the proposals stage runs, 2. Core Invariants

As a MoonMind operator, I want every proposal-capable task submission path to preserve proposal intent in the canonical nested task payload so the proposals stage behaves consistently across API, schedules, promotions, and managed Codex-originated submissions.

Independent test: Submit representative tasks through ordinary API, scheduled/promotion-like construction, and Codex managed-session internal creation paths; assert nested task.proposeTasks/task.proposalPolicy durability and proposal-stage gating behavior.

Acceptance criteria:
- All new task creation surfaces normalize proposal opt-in to initialParameters.task.proposeTasks before MoonMind.Run starts.
- All new task creation surfaces normalize proposal routing overrides to initialParameters.task.proposalPolicy without relying on adapter-local or root-only fields for new work.
- The workflow recognizes proposals as a first-class state and maps it consistently for API and Mission Control compatibility.
- The proposals stage is skipped when either the global proposal switch is disabled or task.proposeTasks is false or missing.
- Stored proposal candidate taskCreateRequest payloads validate against the same canonical Temporal submit contract used by /api/executions.

Requirements:
- Preserve task.proposeTasks, task.proposalPolicy, task.skills, and step.skills in durable run initialParameters.
- Use nested task.* proposal fields as the durable write contract for new work.
- Retain replay/in-flight compatibility reads only where workflow history requires them, without making compatibility shapes the new write path.
- Represent promotion as creation of a new MoonMind.Run rather than legacy queue work.

Owned design coverage:
- `DESIGN-REQ-003`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-004`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-005`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-006`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-007`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-017`: Owned by STORY-001 through its scope, validation checks, and acceptance criteria.

Dependencies: None
Needs clarification: None

### STORY-002: Generate and submit validated proposal delivery records without side effects during generation

Short name: `proposal-generation-delivery`
Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 3.4 Proposal generation, 3.5 Proposal submission and delivery, 3.6 Finish summary integration, 4. Proposal Policy, 4.4 Project-targeted proposals, 4.5 MoonMind-targeted proposals, 12. Worker-Boundary Architecture

As a MoonMind operator, I want proposal generation and submission split across the correct Temporal activity boundaries so candidates are derived safely from run evidence, validated, routed, deduplicated, and persisted before external delivery.

Independent test: Run proposal generation against synthetic run artifacts and agent results, then submit valid, malformed, project-targeted, and MoonMind-targeted candidates; assert no generation side effects, redacted skipped-candidate errors, persisted routing decisions, and finish-summary counts.

Acceptance criteria:
- Generation occurs in LLM-capable activities and performs no commits, pushes, issue creation, task creation, or delivery-record writes.
- Submission validates task payloads, skill selectors, origin metadata, routing policy, target gates, and delivery destinations before persistence or external delivery.
- Policy resolution merges global defaults and task overrides, honors explicit candidate values, applies capacity/severity/tag gates, and stores the resolved delivery decision.
- Project-targeted proposals keep the triggering repository while MoonMind-targeted run-quality proposals are rewritten only when severity and approved tags qualify.
- Finish summary and reports/run_summary.json expose requested, generated, submitted, delivered, failed, validation-error, issue-link, and dedup-update information.

Requirements:
- Treat run inputs, logs, and agent results as untrusted during generation.
- Use artifact-backed references for large context and skill metadata instead of embedding large bodies in workflow history.
- Normalize origin.source to workflow and origin.id to workflow_id for workflow-originated proposals.
- Use snake_case origin metadata keys across payloads, delivery records, APIs, rendering, and docs.
- Bound retries and expose partial success without compromising the parent run result.

Owned design coverage:
- `DESIGN-REQ-008`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-009`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-010`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-011`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-012`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-018`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-026`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-028`: Owned by STORY-002 through its scope, validation checks, and acceptance criteria.

Dependencies: STORY-001
Needs clarification: None

### STORY-003: Deliver deduplicated proposal review issues to GitHub or Jira

Short name: `external-proposal-delivery`
Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 1. Summary, 5. External Proposal Delivery, 9.1 Proposal submission API, 9.4 Provider adapters, 13. Desired Data Model, 14. Acceptance Criteria

As a reviewer, I want each proposal delivered as a deduplicated GitHub Issue or Jira issue with clear review context and commands so I can triage follow-up work in the tracker my team already uses.

Independent test: Submit repeated candidates for the same repository/title against fake GitHub and Jira adapters; assert exactly one external issue per provider destination and dedup hash, updates/comments for repeats, stored-snapshot notices, and complete delivery records.

Acceptance criteria:
- POST /api/proposals returns delivery information including id, status, provider, external key, external URL, dedup hash, and task preview.
- Dedup keys are derived from canonical repository target and normalized proposal title, then scoped by provider and destination.
- GitHub delivery creates or updates issues with canonical title prefix, labels, hidden marker, source links, reviewer instructions, and comment commands.
- Jira delivery creates or updates issues with canonical summary prefix, ADF description, labels/custom fields, workflow state mapping, source links, and issue links.
- External issue rendering links large payloads, logs, diagnostics, and artifacts by reference and explicitly states that MoonMind executes the stored proposal snapshot, not edited issue text.

Requirements:
- Persist a delivery/audit/idempotency record for every submitted proposal.
- Search local delivery records and provider metadata before creating new external issues.
- Use provider adapters behind a common service boundary for find, create, update, comment, link, read, event mapping, and status mapping.
- Keep provider credentials, retries, redaction, and policy enforcement inside trusted integration packages.
- Do not introduce a dedicated MoonMind task proposal review page as the primary triage surface.

Owned design coverage:
- `DESIGN-REQ-001`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-013`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-014`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-015`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-016`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-024`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-029`: Owned by STORY-003 through its scope, validation checks, and acceptance criteria.

Dependencies: STORY-002
Needs clarification: None

### STORY-004: Promote, dismiss, defer, and reprioritize proposals from verified tracker actions

Short name: `verified-proposal-decisions`
Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 8. Review, Promotion, and Execution, 9.2 Webhook endpoints, 9.3 Admin and recovery APIs, 11. Security and Integrity, 14. Acceptance Criteria

As a MoonMind operator, I want tracker actions to become auditable proposal decisions only after signature verification, actor permission checks, idempotency checks, and stored-snapshot validation so approved proposals start safe new MoonMind.Run executions.

Independent test: Feed signed and unsigned GitHub/Jira webhook events plus admin recovery calls through fake providers; assert duplicate events are ignored, unauthorized actors cannot decide proposals, promotion uses only the stored snapshot plus bounded controls, invalid runtime or skill selections fail before workflow creation, and dismissal/deferral audit fields are recorded.

Acceptance criteria:
- Webhook endpoints verify provider signatures or secrets, normalize decision events, check actor permissions, enforce provider-event idempotency, and redact errors/logs.
- Promotion never accepts a full task payload replacement from edited issue Markdown, Jira ADF, labels, or comments.
- Promotion loads the delivery record and stored proposal snapshot, verifies proposal state, applies bounded controls, validates the canonical task payload, and creates a new MoonMind.Run through TemporalExecutionService.create_execution().
- Promotion preserves explicit skill selectors, default inheritance semantics, authored preset metadata, and per-step source provenance from the stored proposal unless a validated proposal revision changed them.
- Dismissal, deferral, reprioritization, and revision-request actions record actor, provider event identity, note or reason, timestamp, and external issue state.

Requirements:
- Support GitHub comment commands and configured labels where policy allows.
- Support Jira transitions, configured field updates, and comment commands where policy allows.
- Apply runtime, priority, maxAttempts, note, and adapter-defined approved fields as bounded controls only.
- Use backend-served supported runtime values and fail disabled runtimes before workflow creation.
- Expose admin/recovery APIs for inspection, redelivery, sync, and controlled promotion without making them the normal review workflow.

Owned design coverage:
- `DESIGN-REQ-002`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-019`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-020`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-021`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-022`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-023`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-024`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-027`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-029`: Owned by STORY-004 through its scope, validation checks, and acceptance criteria.

Dependencies: STORY-003
Needs clarification: None

### STORY-005: Expose proposal delivery status and diagnostics without creating a proposal queue page

Short name: `proposal-observability-ui`
Source reference: `docs/Tasks/TaskProposalSystem.md`; sections: 10. Observability and UI Contract, 3.6 Finish summary integration, 5.1 Delivery model, 8.8 Promotion result, 13. Desired Data Model, 14. Acceptance Criteria

As a Mission Control user, I want proposal delivery progress, tracker links, dedup updates, failures, and promotion results visible from run details and summaries so I can understand proposal outcomes without using MoonMind as the primary review queue.

Independent test: Render execution detail and finish-summary fixtures for new, updated, failed, deferred, dismissed, and promoted deliveries; assert proposals maps to running where required, counts and redacted errors are visible, external links and compact task summaries render, promotion links appear after approval, and no primary proposal queue route is required.

Acceptance criteria:
- Mission Control shows mm_state = proposals while proposal generation or delivery is in progress and applies the documented dashboard compatibility mapping.
- Execution detail surfaces proposal counts, provider, external key, delivery status, last sync timestamp, external issue links, dedup-new-versus-updated status, compact task summary, and promotion result links.
- Finish summaries and run_summary artifacts expose proposal outcomes, including redacted validation and provider delivery errors.
- Valid MoonMind UI surfaces are source run finish summary, execution detail links, compact delivery status cards, admin/recovery views, GitHub Issues, and Jira issues.
- A standalone MoonMind proposal queue page with Promote and Dismiss buttons is not introduced as the primary reviewer experience.

Requirements:
- Keep proposal delivery status inspectable from existing execution-focused surfaces.
- Show skill context and preset provenance compactly when present in the stored proposal snapshot.
- Represent proposal generation and delivery as best-effort: parent runs can succeed while proposal-stage errors remain visible.
- Redact external provider errors before storage, logs, issue comments, and API responses.

Owned design coverage:
- `DESIGN-REQ-010`: Owned by STORY-005 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-013`: Owned by STORY-005 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-025`: Owned by STORY-005 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-026`: Owned by STORY-005 through its scope, validation checks, and acceptance criteria.
- `DESIGN-REQ-029`: Owned by STORY-005 through its scope, validation checks, and acceptance criteria.

Dependencies: STORY-002, STORY-003
Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-003
- `DESIGN-REQ-002` -> STORY-004
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-002
- `DESIGN-REQ-010` -> STORY-002, STORY-005
- `DESIGN-REQ-011` -> STORY-002
- `DESIGN-REQ-012` -> STORY-002
- `DESIGN-REQ-013` -> STORY-003, STORY-005
- `DESIGN-REQ-014` -> STORY-003
- `DESIGN-REQ-015` -> STORY-003
- `DESIGN-REQ-016` -> STORY-003
- `DESIGN-REQ-017` -> STORY-001
- `DESIGN-REQ-018` -> STORY-002
- `DESIGN-REQ-019` -> STORY-004
- `DESIGN-REQ-020` -> STORY-004
- `DESIGN-REQ-021` -> STORY-004
- `DESIGN-REQ-022` -> STORY-004
- `DESIGN-REQ-023` -> STORY-004
- `DESIGN-REQ-024` -> STORY-003, STORY-004
- `DESIGN-REQ-025` -> STORY-005
- `DESIGN-REQ-026` -> STORY-002, STORY-005
- `DESIGN-REQ-027` -> STORY-004
- `DESIGN-REQ-028` -> STORY-002
- `DESIGN-REQ-029` -> STORY-003, STORY-004, STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-002, STORY-003

## Out Of Scope

- Dedicated MoonMind proposal review queue page: The design explicitly makes GitHub Issues or Jira the primary human review surface.
- Executing tracker issue body edits as task payloads: The executable contract is MoonMind's stored validated snapshot plus bounded reviewer controls.
- Live preset re-expansion during default promotion: Promotion uses the reviewed flat task payload and preserves reliable provenance without silently changing execution semantics.
- Provider credential exposure to managed agents: Credentials remain inside trusted integration boundaries and are resolved just in time.

## Coverage Gate

PASS - every major design point is owned by at least one story.

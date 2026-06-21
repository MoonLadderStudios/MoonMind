# Story Breakdown: Workflow Proposal System

- Source: `docs/Workflows/WorkflowProposalSystem.md`
- Source document class: `canonical-declarative`
- Extracted: `2026-06-21T07:32:52Z`
- Requested output mode: `jira`

## Design Summary

The workflow proposal system defines a GitHub-Issues-native review loop for follow-up work discovered during MoonMind.UserWorkflow runs. Generated proposals are validated, routed, deduplicated, delivered to the correct GitHub repository, and promoted only after verified reviewer action, while the stored workflowCreateRequest snapshot remains the executable contract. The architecture keeps proposal generation and delivery in Temporal activities, records idempotent delivery/audit state, exposes Mission Control only as a link/status surface, and enforces fail-closed routing, security, observability, recovery, and testing requirements.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **GitHub-native proposal review loop** - Follow-up work discovered during MoonMind.UserWorkflow is preserved for GitHub Issue review and never starts automatically. Source: 1. Summary.
- `DESIGN-REQ-002` (state-model): **Stored workflow snapshot is executable source of truth** - Promotion executes MoonMind stored workflowCreateRequest data or artifact refs, not edited GitHub issue content. Source: 1. Summary; 2. Core Invariants.
- `DESIGN-REQ-003` (integration): **Temporal-native proposal lifecycle** - A proposals stage runs after execution and before finalization, with side effects isolated to activities. Source: 1. Summary; 3. UserWorkflow Lifecycle Integration.
- `DESIGN-REQ-004` (contract): **Submit-time proposal intent preservation** - proposeTasks, proposalPolicy, task skills, step skills, repository, and runtime metadata are normalized into initialParameters. Source: 3.1 Submit-time contract; 3.2 Canonical submission-path normalization.
- `DESIGN-REQ-005` (state-model): **Proposal-capable run state vocabulary** - The proposal lifecycle uses a consistent state vocabulary across workflow, API, Mission Control, summaries, labels, and docs. Source: 3.3 Run states.
- `DESIGN-REQ-006` (constraint): **Proposal stage enablement gates** - The proposals stage runs only when global proposal generation is enabled and task.proposeTasks opts in. Source: 3.4 When the proposals stage runs.
- `DESIGN-REQ-007` (security): **Safe proposal generation inputs and outputs** - Generators analyze artifacts, results, diagnostics, skills, and reliable preset provenance as untrusted input, using artifact refs and avoiding side effects. Source: 3.5 Proposal generation.
- `DESIGN-REQ-008` (requirement): **Submission and delivery record creation** - Submission validates candidates, routes them, preserves skill intent, computes dedup fields, stores delivery records, and delivers GitHub Issues. Source: 3.6 Proposal submission and delivery; 5. Proposal Delivery Records.
- `DESIGN-REQ-009` (artifact): **Finish summary proposal outcomes** - Typed results and reports/run_summary.json include requested/generated/submitted/delivered counts, links, dedup updates, and redacted errors. Source: 3.7 Finish summary integration.
- `DESIGN-REQ-010` (contract): **Global and per-workflow proposal policy** - Global controls and task.proposalPolicy merge at submission time to control targets, caps, severity, runtime defaults, labels, and template bounds. Source: 4. Proposal Policy and Routing.
- `DESIGN-REQ-011` (integration): **Deterministic repository routing** - Workflow-repo proposals route to the workflow repository; MoonMind run-quality proposals route to the configured MoonMind repository. Source: 4.4 Workflow-repo proposals; 4.5 MoonMind proposals.
- `DESIGN-REQ-012` (constraint): **Fail-closed routing behavior** - MoonMind does not guess destinations; unresolved, disallowed, unevidenced, or unauthorized delivery fails with sanitized recoverable errors. Source: 4.6 Fail-closed routing.
- `DESIGN-REQ-013` (requirement): **Destination-aware deduplication** - Dedup identity includes target class, destination repository, category, and normalized title, updating matching open issues instead of duplicating them. Source: 6. Deduplication.
- `DESIGN-REQ-014` (public-contract): **GitHub Issue review artifact contract** - Proposal Issues have canonical titles, labels, hidden markers, source links, reviewer instructions, and a stored snapshot notice. Source: 7. GitHub Issue Contract.
- `DESIGN-REQ-015` (security): **Verified reviewer decision handling** - Webhook or sync decisions verify authenticity, allowlist, marker ownership, actor authorization, idempotency, syntax, and bounded controls. Source: 8. GitHub Reviewer Decisions.
- `DESIGN-REQ-016` (requirement): **Promotion from stored snapshot only** - Promotion creates a new UserWorkflow from stored data and only permits bounded reviewer overrides for runtime, priority, and max attempts. Source: 8.1 Promotion.
- `DESIGN-REQ-017` (requirement): **Non-promotion decisions do not mutate executable snapshots** - Dismiss, defer, reprioritize, and revision decisions update audit/review state without changing stored executable payloads. Source: 8.2 Dismissal, deferral, reprioritization, and revision requests.
- `DESIGN-REQ-018` (non-goal): **Mission Control is not proposal review** - Mission Control shows proposal outcomes, links, and diagnostics but no queue, promote/dismiss buttons, editable payloads, or review workflow. Source: 9. Mission Control Surfaces.
- `DESIGN-REQ-019` (security): **Proposal trust-boundary security controls** - Generated text, GitHub issue text, and comments are untrusted; credentials stay in trusted services; outbound text is redacted/scanned; routing is allowlisted. Source: 10. Security Requirements.
- `DESIGN-REQ-020` (observability): **Proposal observability and recovery** - Telemetry tracks generation, delivery, decisions, and promotions; recovery supports replay, resync, idempotent reprocessing, unroutable reports, and orphan linking. Source: 11. Observability and Recovery.
- `DESIGN-REQ-021` (testing): **Required proposal-system test coverage** - Tests must cover routing, fail-closed behavior, dedup, stored-snapshot rendering, command parsing, auth, idempotency, promotion, rejection, summaries, and Mission Control exclusions. Source: 12. Testing Requirements.

## Story Candidates

### STORY-001: Normalize proposal intent into workflow submissions

- Short name: `proposal-intent-normalization`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 3.1 Submit-time contract, 3.2 Canonical submission-path normalization
- Why: As a workflow operator, I need every proposal-capable submission path to preserve proposal intent and related skill/repository metadata in the canonical Workflow payload so later proposal handling has a durable, validated contract.
- Independent test: Submit representative API and managed-session-originated Workflow payloads and assert initialParameters contain the canonical nested proposal fields and skill selectors.
- Dependencies: None
- Coverage: DESIGN-REQ-004
- Acceptance criteria:
  - session-level and adapter-level proposal opt-in map to initialParameters.task.proposeTasks
  - proposal routing overrides map to initialParameters.task.proposalPolicy without losing raw policy data
  - repository, runtime, task.skills, and step.skills survive submission normalization
  - malformed proposal intent fails validation before workflow start

### STORY-002: Run proposal generation as a gated Temporal stage

- Short name: `proposal-stage-generation`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 3.3 Run states, 3.4 When the proposals stage runs, 3.5 Proposal generation
- Why: As an operator, I need proposal generation to run only at the correct point in MoonMind.UserWorkflow and only when explicitly enabled, so best-effort follow-up discovery cannot compromise the parent run result.
- Independent test: Exercise UserWorkflow paths with global disabled, task disabled, and both enabled; assert state transitions, activity invocation, and non-blocking behavior on generation failure.
- Dependencies: STORY-001
- Coverage: DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007
- Acceptance criteria:
  - the proposals state appears consistently in workflow/API/status mapping when generation is enabled
  - generation is skipped when either global enablement or task.proposeTasks is false
  - workflow code performs no file, network, GitHub, or artifact side effects directly
  - generation failures are sanitized and do not change a successful parent execution into an incorrect result
  - large or sensitive generation inputs are passed by artifact reference or redacted metadata

### STORY-003: Validate and route proposal delivery records

- Short name: `proposal-routing-records`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 3.6 Proposal submission and delivery, 4. Proposal Policy and Routing, 5. Proposal Delivery Records
- Why: As an operator, I need proposal submission to validate candidates, resolve deterministic destinations, and store auditable delivery records before any GitHub Issue is created.
- Independent test: Submit candidate proposals for workflow-repo, MoonMind, missing repository, disallowed repository, missing evidence, and missing credentials cases; assert delivery records and fail-closed errors.
- Dependencies: STORY-001, STORY-002
- Coverage: DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012
- Acceptance criteria:
  - policy resolution merges global defaults with task.proposalPolicy and preserves explicit candidate values
  - workflow-repo proposals route to workflowCreateRequest.payload.repository
  - MoonMind proposals route to the configured MoonMind repository only when required run-quality evidence is present
  - unroutable or unauthorized proposals produce sanitized delivery failure records with recoverable next actions
  - delivery records contain provider, target class, repository, issue identifiers when available, dedup fields, stored snapshot ref, origin metadata, status, decisions, and sanitized errors

### STORY-004: Deduplicate and render GitHub proposal issues

- Short name: `github-proposal-delivery`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 6. Deduplication, 7. GitHub Issue Contract
- Why: As a reviewer, I need each proposal to appear as one clear GitHub Issue in the correct repository, with duplicate origins attached to the same issue and a visible warning that the issue is not the executable payload.
- Independent test: Deliver two equivalent candidates to the same destination and one to a different target/destination; assert update versus create behavior, labels, hidden marker, snapshot notice, and outbound redaction.
- Dependencies: STORY-003
- Coverage: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-019
- Acceptance criteria:
  - dedup identity includes target class, destination repository, normalized category, and normalized title
  - matching open local or GitHub marker records update the existing issue rather than creating duplicates
  - workflow_repo and moonmind proposals never merge unless all dedup identity fields match
  - created issues use [MoonMind proposal] titles, canonical state/target/category/priority/dedup labels, hidden markers, links, reviewer instructions, and the required stored snapshot notice
  - secret-like or unsafe outbound content is redacted or blocked before delivery

### STORY-005: Process verified GitHub reviewer decisions

- Short name: `reviewer-decision-processing`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 8. GitHub Reviewer Decisions, 8.2 Dismissal, deferral, reprioritization, and revision requests
- Why: As a reviewer, I need MoonMind to accept only verified, authorized, idempotent GitHub commands so proposal decisions are auditable and cannot be spoofed or replayed.
- Independent test: Replay valid and invalid webhook/sync events for every supported command and assert accepted decisions, rejected decisions, idempotency, labels, comments, and stored snapshot immutability.
- Dependencies: STORY-004
- Coverage: DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-019
- Acceptance criteria:
  - unsupported or malformed commands are ignored or rejected with sanitized audit reasons
  - duplicate GitHub events do not create duplicate comments, duplicate executions, or conflicting delivery states
  - authorized dismiss/defer/reprioritize/request-revision commands update delivery record decision state and GitHub labels/comments as appropriate
  - non-promotion decisions never alter the stored workflowCreateRequest snapshot
  - GitHub text and comments remain untrusted until parsed into bounded commands

### STORY-006: Promote approved proposals from stored snapshots

- Short name: `proposal-promotion`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 8.1 Promotion
- Why: As an authorized reviewer, I need an approved proposal to start a new MoonMind.UserWorkflow from MoonMind stored proposal data, with only bounded reviewer overrides allowed.
- Independent test: Promote a delivered proposal with and without bounded overrides, then assert the child execution payload comes from the stored snapshot, delivery record is linked, GitHub is updated, and replacement issue text is ignored.
- Dependencies: STORY-005
- Coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-016, DESIGN-REQ-019
- Acceptance criteria:
  - promotion uses stored workflowCreateRequest, origin metadata, bounded controls, and current execution contract validation
  - runtime mode, priority, and max attempts are the only accepted reviewer overrides
  - replacement instructions, steps, repositories, environment variables, credentials, or tool configuration in GitHub text are rejected or ignored
  - successful promotion creates exactly one new MoonMind.UserWorkflow and records its execution ID
  - GitHub state labels and comments link to the promoted execution while preserving the original issue as audit trail

### STORY-007: Publish proposal outcomes without Mission Control review controls

- Short name: `proposal-outcome-surfaces`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 3.7 Finish summary integration, 9. Mission Control Surfaces
- Why: As an operator, I need proposal outcomes, links, counts, and delivery failures to be visible in finish summaries, artifacts, and Mission Control without turning Mission Control into a proposal review UI.
- Independent test: Run a proposal-capable workflow with delivered, deduped, and failed proposal outcomes and assert finish summary, run_summary artifact, and Mission Control data omit review actions while showing links/status.
- Dependencies: STORY-003, STORY-004
- Coverage: DESIGN-REQ-009, DESIGN-REQ-018
- Acceptance criteria:
  - finish results record whether generation was requested plus generated, submitted, delivered, failed, and dedup-updated counts
  - reports/run_summary.json includes GitHub Issue links and redacted delivery failures
  - workflow detail pages expose proposal GitHub links and delivery status context
  - Mission Control exposes no proposal queue, promote/dismiss controls, editable proposal payloads, or review workflow detail pages

### STORY-008: Add proposal telemetry, recovery, and contract tests

- Short name: `proposal-telemetry-recovery`
- Source reference: `docs/Workflows/WorkflowProposalSystem.md`; sections: 10. Security Requirements, 11. Observability and Recovery, 12. Testing Requirements
- Why: As an operator, I need proposal delivery and promotion to be observable, recoverable, and covered by contract tests so failures can be repaired without bypassing stored-snapshot safety rules.
- Independent test: Run hermetic workflow/activity/service tests that cover every documented routing, security, idempotency, summary, Mission Control exclusion, and recovery case with sanitized telemetry assertions.
- Dependencies: STORY-003, STORY-004, STORY-005, STORY-006, STORY-007
- Coverage: DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-019
- Acceptance criteria:
  - telemetry events cover generation requested, candidates generated/rejected, proposals submitted, issues created/updated, delivery failures, decisions observed/accepted/rejected, promotions started/completed/failed
  - recovery tools cannot promote or redeliver in ways that bypass stored-snapshot rules
  - tests cover workflow-repo and MoonMind routing including Tactics examples, missing/disallowed repositories, dedup updates, stored-snapshot rendering, each reviewer command, webhook auth and actor authorization, replay idempotency, bounded promotion overrides, rejection of replacement payloads, finish summaries, and Mission Control review-control exclusions
  - logs and recovery output use sanitized failure reasons and stable non-sensitive identifiers

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-006
- `DESIGN-REQ-002` -> STORY-006
- `DESIGN-REQ-003` -> STORY-002
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-002
- `DESIGN-REQ-008` -> STORY-003
- `DESIGN-REQ-009` -> STORY-007
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-003
- `DESIGN-REQ-012` -> STORY-003
- `DESIGN-REQ-013` -> STORY-004
- `DESIGN-REQ-014` -> STORY-004
- `DESIGN-REQ-015` -> STORY-005
- `DESIGN-REQ-016` -> STORY-006
- `DESIGN-REQ-017` -> STORY-005
- `DESIGN-REQ-018` -> STORY-007
- `DESIGN-REQ-019` -> STORY-004, STORY-005, STORY-006, STORY-008
- `DESIGN-REQ-020` -> STORY-008
- `DESIGN-REQ-021` -> STORY-008

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-004
- `STORY-006` depends on: STORY-005
- `STORY-007` depends on: STORY-003, STORY-004
- `STORY-008` depends on: STORY-003, STORY-004, STORY-005, STORY-006, STORY-007

## Out Of Scope

- Creating or modifying any `spec.md` files; specification happens only in a downstream specify step.
- Creating directories under `specs/`; this breakdown is a temporary artifact under `artifacts/story-breakdowns/`.
- Creating Jira issues, GitHub issues, PRs, implementation plans, tasks, or code changes from this breakdown step.
- Treating this derived breakdown as authority over the canonical source document.

## Coverage Gate

PASS - every major design point is owned by at least one story.

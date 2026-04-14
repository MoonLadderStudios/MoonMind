# Breakdown: docs/Tasks/TaskProposalSystem.md

**Story extraction date**: 2026-04-14

## Design Summary

The Task Proposal System defines Temporal-native reviewable follow-up work for MoonMind runs. It requires durable canonical task payloads, explicit human promotion before execution, activity-bound generation/submission, policy-aware routing, skill-selector preservation, origin/linkage metadata, and consistent API/UI observability. Current open work centers on Codex managed-session normalization, end-to-end skill preservation, promotion linkage, origin naming, and proposal-stage UI alignment.

## Coverage Points

- **DESIGN-REQ-001** (requirement, 1. Summary): Reviewable follow-up work - Proposals are durable review objects for useful follow-up work and must not automatically start new executions.
- **DESIGN-REQ-002** (contract, 1. Summary / 2. Core Invariants): Canonical task payload - Every proposal stores a canonical taskCreateRequest compatible with Temporal-backed execution submission.
- **DESIGN-REQ-003** (constraint, 2. Core Invariants): Human-gated execution - Proposal creation is distinct from execution; only explicit human promotion creates running work.
- **DESIGN-REQ-004** (state-model, 2. Core Invariants): Repository-aware identity - taskCreateRequest.payload.repository is the canonical repository target for deduplication and future execution.
- **DESIGN-REQ-005** (contract, 3.1 Submit-time contract): Durable proposal intent at submit time - task.proposeTasks, task.proposalPolicy, task.skills, and step.skills are preserved in initialParameters.task.
- **DESIGN-REQ-006** (integration, 3.1.1 Canonical submission-path normalization): Canonical submission normalization - All task creation paths, including Codex managed sessions, normalize proposal intent into nested task.* fields instead of non-canonical locations.
- **DESIGN-REQ-007** (state-model, 3.2 Run states / 3.3 When the proposals stage runs): Proposal lifecycle state - The proposals state is first-class and entered only when global settings and task-level opt-in both allow generation.
- **DESIGN-REQ-008** (architecture, 3.4 Proposal generation): Activity-based generation - Generators run in activities, analyze run artifacts/results/skill metadata, treat inputs as untrusted, avoid side effects, and use artifact refs for large context.
- **DESIGN-REQ-009** (architecture, 3.5 Proposal submission): Activity-based submission - Submission validates candidates, resolves policy, normalizes origin, enforces routing, preserves skill intent, and stores proposals only.
- **DESIGN-REQ-010** (observability, 3.6 Finish summary integration): Finish summary outcomes - Finish summaries and reports/run_summary.json record requested, generated, submitted, and redacted error counts.
- **DESIGN-REQ-011** (requirement, 4. Proposal Policy): Proposal policy resolution - Global defaults and task proposalPolicy merge at submission, enforce target caps, severity gates, routing, and defaultRuntime only when needed.
- **DESIGN-REQ-012** (requirement, 4.4 Project-targeted proposals / 4.5 MoonMind-targeted proposals): Target routing - Project proposals keep the triggering repository while MoonMind run-quality proposals require repository rewrite, category normalization, severity, and tag gates.
- **DESIGN-REQ-013** (contract, 5. Canonical Proposal Payload Contract): Canonical payload validation - Stored candidates use supported runtime values, skill tool selectors, canonical task/step skills, and exclude secrets, raw logs, mutable skill directories, and ephemeral materialization state.
- **DESIGN-REQ-014** (state-model, 6. Origin, Identity, and Naming): Workflow origin metadata - Workflow-originated proposals use origin.source=workflow, origin.id=workflow_id, snake_case metadata, and workflow_id as durable identity.
- **DESIGN-REQ-015** (integration, 7.1 Promotion flow / 7.4 Response contract): Promotion bridge - Promotion loads an open proposal, merges overrides, validates the payload, creates a new MoonMind.Run through TemporalExecutionService, persists linkage, and returns proposal plus execution metadata.
- **DESIGN-REQ-016** (contract, 7.2 Skill preservation and inheritance / 7.3 Runtime selection): Skill and runtime preservation - Promotion preserves explicit agent skill intent, treats runtime override separately, fails incompatible or disabled runtime/skill selections, and uses the backend runtime list as source of truth.
- **DESIGN-REQ-017** (observability, 8. Observability and UI Contract): Review and status visibility - API and UI surfaces expose proposals state mappings, counts, errors, filtered proposal links, and compact skill/runtime/repository/publish context.
- **DESIGN-REQ-018** (resilience, 8.3 Failure handling): Best-effort failure handling - Generation/submission errors must not corrupt successful parent runs; malformed candidates are skipped, retries are bounded/idempotent, and partial success is visible.
- **DESIGN-REQ-019** (architecture, 9. Worker-Boundary Architecture): Worker-boundary placement - Generation belongs on the LLM-capable fleet and submission/storage on control-plane or integrations-capable activity boundaries.
- **DESIGN-REQ-020** (migration, 10. Current Implementation Snapshot): Remaining implementation gaps - The open work includes Codex session normalization verification, UI/state alignment, promotion linkage persistence, origin naming, and skill selector preservation end to end.

## Generated Specs

- **specs/172-canonical-proposal-intent/spec.md**: Canonical Proposal Intent Capture. Independent test: Submit proposal-capable work through ordinary API creation, proposal promotion, schedules, and Codex managed-session internal task creation, then inspect the created run's initial parameters and lifecycle gating without requiring actual proposal promotion.
- **specs/173-proposal-submission-policy/spec.md**: Safe Proposal Generation and Submission. Independent test: Run proposal generation and submission activities against representative run artifacts, agent results, policies, and malformed candidates, then verify stored proposals, skipped candidates, routing, and finish summary counts without invoking promotion.
- **specs/174-proposal-promotion-bridge/spec.md**: Proposal Promotion Bridge. Independent test: Create stored proposal records with and without explicit skill selectors, promote them with runtime and task overrides, then verify new execution metadata, proposal linkage, validation failures, and origin identity without requiring proposal generation.
- **specs/175-proposal-review-visibility/spec.md**: Proposal Review Visibility. Independent test: Drive execution and proposal records through API projection and Mission Control views, then verify status mappings, proposal links, count/error summaries, and context display without exercising generation internals.

## Coverage Matrix

- **DESIGN-REQ-001** -> 172-canonical-proposal-intent, 173-proposal-submission-policy
- **DESIGN-REQ-002** -> 172-canonical-proposal-intent, 173-proposal-submission-policy, 174-proposal-promotion-bridge
- **DESIGN-REQ-003** -> 173-proposal-submission-policy, 174-proposal-promotion-bridge
- **DESIGN-REQ-004** -> 172-canonical-proposal-intent, 174-proposal-promotion-bridge
- **DESIGN-REQ-005** -> 172-canonical-proposal-intent
- **DESIGN-REQ-006** -> 172-canonical-proposal-intent
- **DESIGN-REQ-007** -> 172-canonical-proposal-intent, 175-proposal-review-visibility
- **DESIGN-REQ-008** -> 173-proposal-submission-policy
- **DESIGN-REQ-009** -> 173-proposal-submission-policy
- **DESIGN-REQ-010** -> 173-proposal-submission-policy, 175-proposal-review-visibility
- **DESIGN-REQ-011** -> 173-proposal-submission-policy
- **DESIGN-REQ-012** -> 173-proposal-submission-policy
- **DESIGN-REQ-013** -> 173-proposal-submission-policy, 174-proposal-promotion-bridge
- **DESIGN-REQ-014** -> 174-proposal-promotion-bridge, 175-proposal-review-visibility
- **DESIGN-REQ-015** -> 174-proposal-promotion-bridge
- **DESIGN-REQ-016** -> 174-proposal-promotion-bridge
- **DESIGN-REQ-017** -> 175-proposal-review-visibility
- **DESIGN-REQ-018** -> 173-proposal-submission-policy, 175-proposal-review-visibility
- **DESIGN-REQ-019** -> 173-proposal-submission-policy
- **DESIGN-REQ-020** -> 172-canonical-proposal-intent, 173-proposal-submission-policy, 174-proposal-promotion-bridge, 175-proposal-review-visibility

## Dependencies Between Specs

- **172-canonical-proposal-intent** should be planned first because downstream proposal generation depends on durable submit-time intent.
- **173-proposal-submission-policy** depends on canonical proposal intent for proposal-stage gating and task payload inputs.
- **174-proposal-promotion-bridge** depends on stored proposal payloads from the submission story but can be validated with fixture records.
- **175-proposal-review-visibility** depends on state, summary, origin, and linkage fields produced by the prior stories but can be UI/API-tested with fixtures.

## Out Of Scope

- Implementing production code, plans, or task lists; this breakdown only creates one-story specs.
- Changing the canonical Task Proposal System design document.
- Reworking unrelated proposal features not described by the source design.

## Coverage Gate

PASS - every major design point is owned by at least one story.

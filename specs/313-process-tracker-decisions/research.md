# Research: Process Verified Tracker Decisions

## Traceability

This research artifact supports Jira issue `MM-599` and the single-story feature request "Process verified tracker decisions and promote approved proposals". Source design coverage is preserved for DESIGN-REQ-002, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026, and DESIGN-REQ-031.

## Setup Script

Decision: Proceed with manual planning artifacts in the active feature directory.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch name is `change-jira-issue-mm-599-to-status-in-pr-32547f89`, not `001-feature-name`; `.specify/feature.json` points at `specs/313-process-tracker-decisions`.
Rationale: The active spec is valid, and `plan.md` plus design artifacts can be created deterministically from the template and current repository state.
Alternatives considered: Renaming the managed branch was rejected because this step is limited to planning artifacts and should not mutate branch state.
Test implications: None beyond final artifact verification.

## Current Proposal Delivery Foundation

Decision: Treat existing proposal delivery records, renderers, bounded command parsing, and manual promotion as the foundation for MM-599.
Evidence: `moonmind/workflows/task_proposals/delivery.py` renders GitHub/Jira review issues with stored-snapshot notices, parses bounded `/moonmind promote|dismiss|defer|priority` commands, and validates provider delivery policy. `moonmind/workflows/task_proposals/service.py` records provider decisions and manually promotes proposals from stored snapshots. `api_service/api/routers/task_proposals.py` exposes manual promote/dismiss/priority operations.
Rationale: Existing code covers much of the safe review artifact and stored-snapshot boundary, so this story should complete the trusted decision-ingestion and execution bridge rather than replace the delivery foundation.
Alternatives considered: Rewriting proposal delivery from scratch was rejected because it would violate modularity and risk regressing already-tested delivery behavior.
Test implications: Existing tests should remain green; new tests should focus on missing provider-decision edges and integration boundaries.

## FR-001 / FR-006 / Provider Promotion Starts One Run

Decision: Add a provider decision path that, after authenticity and actor authorization, promotes through the same stored-snapshot run creation path used by manual promotion.
Evidence: Manual `promote_proposal()` returns a final request and `task_proposals.py` calls `TemporalExecutionService.create_execution()` with idempotency key `proposal-promote-{proposal_id}`. `record_provider_decision_event()` currently marks provider `promote` decisions as `ACCEPTED` but does not create a run.
Rationale: Source section 8.3 requires verified external approvals to create a new MoonMind.Run through the canonical create path.
Alternatives considered: Leaving provider approval as `ACCEPTED` for a later manual click was rejected because MM-599 requires promotion from approved external tracker actions.
Test implications: Unit tests for service orchestration and integration tests proving one run creation for provider approval.

## FR-002 / Canonical Decision Vocabulary

Decision: Extend provider decision normalization to include `request_revision` in addition to promote, dismiss, defer, and reprioritize.
Evidence: `_SUPPORTED_ACTIONS` currently includes `promote`, `dismiss`, `defer`, and `priority`; source section 8.2 names request revision as a canonical reviewer action.
Rationale: The spec requires all canonical non-executing decisions to update proposal state without arbitrary tracker content execution.
Alternatives considered: Treating request revision as dismiss was rejected because it has distinct review semantics and recovery expectations.
Test implications: Parser and service tests for request revision from command, Jira transition/field event, and policy rejection.

## FR-003 / Provider Authenticity

Decision: Add trusted provider-decision ingress that verifies GitHub signatures or Jira shared secrets before parsing decisions.
Evidence: No `/api/integrations/github/issues/webhook` or `/api/integrations/jira/webhook` routes were found for proposal decisions.
Rationale: The first acceptance criterion requires provider authenticity before any decision processing.
Alternatives considered: Relying on obscurity or provider issue identity matching was rejected because actor and payload verification are separate security requirements.
Test implications: Unit/API tests for valid signature/shared-secret, missing credential, invalid signature, and no side effects on failure.

## FR-004 / Actor Authorization

Decision: Add actor authorization checks before provider decisions mutate state or start runs.
Evidence: `record_provider_decision_event()` records `event.actor` as text and enforces optional `allowedActions`, but no actor permission check was found.
Rationale: External tracker actors must be verified against configured policy so comments or transitions from unauthorized users cannot promote work.
Alternatives considered: Trusting any signed provider event was rejected because provider authenticity only proves source, not reviewer permission.
Test implications: Unit/API tests for allowed actor, denied actor, unknown actor, and sanitized denial metadata.

## FR-005 / FR-017 / Sanitized Rejections And Redaction

Decision: Reuse `SecretRedactor` and safe metadata helpers, and extend them to provider webhook failures, rejected decisions, and external update failures.
Evidence: `TaskProposalService._scrub_json()`, `_scrub_text()`, and delivery `_safe_metadata()` already redact secret-like fields; provider rejection metadata is stored under `providerDecisions`.
Rationale: Provider credentials and webhook secrets must never appear in artifacts, logs, issue text, or API responses.
Alternatives considered: Returning raw provider validation errors was rejected by security guardrails and Constitution constraints.
Test implications: Unit tests for secret-like webhook payload fields, auth headers, provider error messages, and persisted provider decision rows.

## FR-007 / FR-012 / Non-Executing Decisions

Decision: Complete non-executing decision handling for dismiss, defer, reprioritize, and request revision with audit fields and no run creation.
Evidence: `record_provider_decision_event()` already updates status for dismiss, priority for priority, and note for defer; request revision and external issue state capture are missing.
Rationale: Reviewers need explicit non-executing outcomes that are auditable and recoverable.
Alternatives considered: Mapping all non-promote actions to dismissed was rejected because it loses reviewer intent.
Test implications: Unit tests for each decision and integration tests proving zero run creation.

## FR-008 / FR-009 / Snapshot-Only Promotion

Decision: Preserve the current stored snapshot promotion path and make provider promotion call it without using issue body or Jira ADF as executable payload.
Evidence: `promote_proposal()` loads `proposal.task_create_request`, validates `CanonicalTaskPayload`, applies bounded controls, and enforces flat preset-derived steps. Tests already prove unsafe provider body text is not persisted for a dismiss command.
Rationale: External issue text is an untrusted review artifact; only stored snapshot or validated revisions may execute.
Alternatives considered: Parsing edited issue body into taskCreateRequest was rejected as explicitly out of scope and unsafe.
Test implications: Provider-promotion boundary tests where edited issue text differs from stored snapshot.

## FR-010 / FR-011 / Skill And Preset Provenance

Decision: Treat preservation of skill selectors, `task.authoredPresets`, and `steps[].source` as required provider-promotion assertions.
Evidence: Existing preview serialization and manual promotion preserve stored payload fields, but no provider promotion test proves that path.
Rationale: Provider approvals must not re-resolve skills or flatten away provenance as an undocumented side effect.
Alternatives considered: Rebuilding task payload from rendered issue metadata was rejected because it would lose provenance and violate source design requirements.
Test implications: Unit tests with stored payload containing explicit skill selectors, authored presets, and step source metadata.

## FR-013 / Runtime Overrides

Decision: Validate provider-supplied runtime override controls through the same canonical task payload validation used by manual promotion.
Evidence: `TaskProposalPromoteRequest` and `promote_proposal()` validate runtime overrides for manual promotion; provider event/result does not currently carry runtime controls.
Rationale: Disabled or unsupported runtime values must fail before run creation regardless of decision source.
Alternatives considered: Ignoring provider runtime controls was rejected because bounded controls are explicitly part of promotion semantics.
Test implications: Unit tests for supported, blank, unknown, and incompatible runtime controls from provider events.

## FR-014 / Idempotency

Decision: Use provider event ID to deduplicate decision handling and a promotion idempotency key to prevent duplicate run creation.
Evidence: `record_provider_decision_event()` returns existing results for repeated provider event IDs; manual promotion uses `proposal-promote-{proposal_id}` as the run idempotency key.
Rationale: Webhooks can be retried, and duplicate approval must not create additional runs.
Alternatives considered: Timestamp-only deduplication was rejected because provider event IDs are stable and deterministic.
Test implications: Integration test replaying the same provider approval and expecting zero additional runs.

## FR-015 / FR-016 / Recovery And Inspection

Decision: Add recovery/inspection surfaces or explicitly extend existing proposal routes to expose promoted run IDs, decision audit, external issue state, redelivery, sync, and controlled promote actions.
Evidence: Existing `/api/proposals` routes expose proposal details and manual promote/dismiss/priority; no `proposal-deliveries` admin routes were found.
Rationale: Source section 9.3 requires operator recovery APIs for inspection and repair.
Alternatives considered: Relying only on database inspection was rejected because operators need supported recovery surfaces.
Test implications: API/integration tests for inspect, sync/redeliver, and controlled promote semantics.

## Unit Test Strategy

Decision: Use pytest unit tests for decision parser, provider authenticity helpers, actor policy, service state transitions, redaction, runtime override validation, and provenance preservation.
Evidence: Existing focused tests live in `tests/unit/workflows/task_proposals/test_delivery.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, and `tests/unit/workflows/temporal/test_proposal_activities.py`.
Rationale: Most behavior can be validated hermetically without live Jira or GitHub credentials.
Alternatives considered: Provider verification tests were rejected for required coverage because the story must remain CI-safe.
Test implications: Focused pytest first, then `./tools/test_unit.sh`.

## Integration / Boundary Test Strategy

Decision: Add boundary tests for provider decision ingestion through API/service into proposal state and run creation. Use `integration_ci` only if compose-backed DB/API coverage is necessary and remains hermetic.
Evidence: Existing `tests/integration/temporal/test_proposal_review_delivery.py` covers proposal submit-to-delivery and provider decision service behavior, but not authenticated webhook-to-run creation.
Rationale: This feature crosses provider boundary, proposal persistence, and Temporal execution service invocation, so shape drift must be caught beyond pure helper tests.
Alternatives considered: Unit-only verification was rejected because provider-approved promotion must prove the real invocation shape used by the route/service boundary.
Test implications: Run focused integration tests during implementation; run `./tools/test_integration.sh` if integration_ci tests are added.

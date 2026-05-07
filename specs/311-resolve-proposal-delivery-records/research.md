# Research: Resolve Proposal Policy and Delivery Records

## Setup Script

Decision: Proceed with manual planning artifacts in the active feature directory.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch name is `change-jira-issue-mm-597-to-status-in-pr-07dad35c`, not `001-feature-name`; `.specify/feature.json` points at `specs/311-resolve-proposal-delivery-records`.
Rationale: The active spec is valid and downstream artifacts can be created deterministically despite the helper's branch-name guard.
Alternatives considered: Renaming the branch was rejected because this managed step is limited to planning artifacts and should not mutate branch state.
Test implications: None beyond final artifact verification.

## FR-001 / Deterministic Submission-Time Policy Resolution

Decision: Treat proposal submission as the policy-resolution boundary and add a resolved delivery decision before service persistence.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` builds `EffectiveProposalPolicy`; `moonmind/workflows/tasks/task_contract.py` defines `TaskProposalPolicy` and `build_effective_proposal_policy()`; `moonmind/workflows/task_proposals/service.py` validates and persists proposals.
Rationale: Existing logic is split between activity and service and does not persist a complete resolved decision. MM-597 needs deterministic routing evidence before provider delivery.
Alternatives considered: Resolving policy during proposal generation was rejected because generation must remain side-effect-free and does not own delivery decisions.
Test implications: Unit tests for resolved decision output and boundary tests for activity-to-service arguments.

## FR-002 / Explicit Values Over Defaults

Decision: Preserve explicit candidate/task values over operator defaults and record which defaults were applied.
Evidence: `build_effective_proposal_policy()` merges task policy with defaults for targets, caps, severity floor, and default runtime; proposal submission stamps default runtime only when missing.
Rationale: Source section 4.3 requires explicit values to win over defaults while keeping routing auditable.
Alternatives considered: Applying defaults inside persistence only was rejected because the activity must know whether a candidate is eligible before calling side-effecting services.
Test implications: Unit tests for explicit-over-default and defaulted delivery decision cases.

## FR-003 / Allowlists, Capacity, Severity, and Tag Gates

Decision: Extend policy validation to cover destination allowlists and approved tag gates, not only capacity and severity.
Evidence: Current code consumes project and MoonMind slots and checks severity for MoonMind paths, but delivery provider/destination allowlists from source section 4.2 are not modeled in `TaskProposalPolicy`.
Rationale: The Jira brief explicitly requires allowlists, capacity limits, severity gates, and tag gates before delivery.
Alternatives considered: Deferring allowlists to provider adapters was rejected because the story requires deterministic delivery resolution before provider-specific issue delivery.
Test implications: Unit tests for allowlist rejection, capacity rejection, severity rejection, tag rejection, and successful delivery.

## FR-004 / Project Repository Preservation

Decision: Treat project-targeted candidates as repository-preserving unless policy rejects the destination.
Evidence: `TaskProposalService.create_proposal()` persists the repository derived from `taskCreateRequest.payload.repository`; existing activity tests assert project-style submissions preserve the payload repository.
Rationale: Current behavior appears correct but lacks direct MM-597 proof after the resolved-policy layer is added.
Alternatives considered: Rewriting project repositories through global defaults was rejected by source section 4.4.
Test implications: Unit test verifying project target repository remains the triggering/candidate repository.

## FR-005 / MoonMind Run-Quality Routing

Decision: Classify MoonMind run-quality candidates explicitly and rewrite only after category, severity, and tag gates pass.
Evidence: `TaskProposalService._enforce_moonmind_policy()` requires `run_quality`, approved signal tags, and trigger metadata when the repository is already MoonMind; `proposal_submit()` currently attempts project slot consumption before MoonMind slot/gate handling.
Rationale: The story needs deterministic target routing. MoonMind run-quality routing should not depend on project slot exhaustion.
Alternatives considered: Keeping repository-based implicit MoonMind detection was rejected because it cannot explain rejected or rewritten delivery decisions.
Test implications: Unit and boundary tests for project-targeted MoonMind repo candidates, run-quality rewrite, severity failure, and missing-tag failure.

## FR-006 / FR-007 / Delivery Record Field Set

Decision: Use the existing `task_proposals` table as the delivery record, extending it or documenting an explicit subset for provider/external identity, delivery/sync timestamps, and provider metadata.
Evidence: `TaskProposal` already stores id, status, title, summary, category, tags, repository, dedup key/hash, review priority, task snapshot, proposed-by fields, origin, decision, created_at, and updated_at. It does not expose provider, external_key, external_url, delivered_at, last_synced_at, task_snapshot_ref, or provider-specific metadata.
Rationale: The desired data model is a delivery/audit/idempotency record. Existing storage is close but incomplete for provider delivery semantics.
Alternatives considered: Creating a separate new table was rejected unless schema review proves the existing table cannot safely represent delivery records.
Test implications: Unit tests for service arguments/serialization and integration_ci DB tests if schema changes are made.

## FR-008 / Dedup Identity

Decision: Keep dedup identity based on canonical repository target and normalized title, and add direct regression coverage.
Evidence: `TaskProposalService._compute_dedup_fields()` lowercases repository, slugifies title, and hashes `repository:slug`.
Rationale: Existing behavior matches the source requirement, but final verification needs MM-597-specific evidence.
Alternatives considered: Including category or provider in the hash was rejected for this story because source section 5.2 names repository and normalized title.
Test implications: Unit tests for equivalent title normalization and repository-aware distinct hashes.

## FR-009 / FR-010 / Dedup-First Update Path

Decision: Search local open delivery records and provider metadata before creating a new reviewer-facing issue; update/link/comment on open duplicates.
Evidence: `TaskProposalRepository.list_similar()` finds open records by dedup hash only after a proposal exists; `TaskProposalService.create_proposal()` always calls `create_proposal()` for valid input.
Rationale: Current behavior can create duplicates and does not satisfy source section 5.2.
Alternatives considered: Showing similar proposals only in the UI was rejected because dedup must happen before external issue creation.
Test implications: Unit tests with mocked repository/provider metadata plus integration/boundary tests for local open duplicate update path.

## FR-011 / FR-012 / Workflow Origin Metadata

Decision: Normalize workflow-origin proposals to `origin.source = workflow`, `origin.id = workflow_id`, and snake_case metadata keys, while resolving the current UUID-only `origin_id` storage constraint in implementation planning.
Evidence: `TaskProposalOriginSource.WORKFLOW` exists. `proposal_submit()` passes `origin_id=None` and metadata containing `workflow_id`, `temporal_run_id`, `triggerRepo`, and `triggerJobId`; `tests/unit/workflows/temporal/test_proposal_activities.py` currently asserts camelCase trigger keys.
Rationale: Source section 7 and MM-597 require snake_case and durable workflow identity. Current behavior is partial and tests encode old semantics.
Alternatives considered: Keeping camelCase metadata was rejected because it conflicts with the MM-597 source requirement and existing spec.
Test implications: Unit tests update old camelCase expectations and boundary tests confirm stored metadata uses snake_case.

## FR-013 / Provider-Specific Metadata

Decision: Add or explicitly define a provider metadata container that is separate from canonical delivery-record fields.
Evidence: No provider-specific metadata field was found on `TaskProposal`; provider/external issue delivery is not represented in the current model.
Rationale: Separating provider data keeps canonical records portable and avoids overloading generic fields.
Alternatives considered: Storing provider fields in `origin_metadata` was rejected because origin and provider delivery metadata have different meanings.
Test implications: Unit tests and DB integration tests if persisted schema changes are added.

## Unit Test Strategy

Decision: Use focused pytest unit tests around `TaskProposalService`, `TaskProposalRepository` mocks, `TaskProposalPolicy`, and `TemporalProposalActivities.proposal_submit`.
Evidence: Existing tests live in `tests/unit/workflows/task_proposals/test_service.py` and `tests/unit/workflows/temporal/test_proposal_activities.py`.
Rationale: These tests can verify deterministic routing, repository rewrite, dedup identity, duplicate handling, metadata normalization, and provider metadata separation without external credentials.
Alternatives considered: Provider-verification tests were rejected because trusted Jira/GitHub delivery is not required for the hermetic story.
Test implications: Focused tests first, then `./tools/test_unit.sh` for final unit verification.

## Integration / Boundary Test Strategy

Decision: Add integration-style boundary coverage for real proposal persistence when schema or repository behavior changes, using hermetic local DB fixtures where available and `integration_ci` only when compose-backed dependencies are required.
Evidence: The repo already has `tests/unit/api/routers/test_task_proposals.py`, proposal service tests, and integration_ci taxonomy for compose-backed hermetic tests.
Rationale: Delivery-record semantics and workflow/activity boundary behavior are higher risk than isolated helpers.
Alternatives considered: Relying only on unit mocks was rejected because persisted field shape and duplicate update behavior can drift.
Test implications: Use unit service/activity tests plus a DB-backed repository/API test; run `./tools/test_integration.sh` if adding an `integration_ci` test.

# Research: Publish Remediation Audit Evidence

This research artifact supports Jira issue `MM-623` and the single-story feature "Publish remediation audit artifacts, summaries, and queryable events." Source coverage IDs are preserved for DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-028.

## Planning Setup

Decision: Continue from the active feature directory recorded in `.specify/feature.json`.
Evidence: `.specify/feature.json` points to `specs/323-publish-remediation-audit`; `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch is `run-jira-orchestrate-for-mm-623-publish-bce76a9b` rather than a `###-feature-name` branch.
Rationale: The feature directory and spec already exist and are the authoritative artifacts for this managed run.
Alternatives considered: Renaming or switching branches was rejected because this step is scoped to planning artifacts and should not mutate branch state.
Test implications: None beyond preserving the active feature pointer.

## FR-001 / DESIGN-REQ-022 - Applicable Remediation Artifact Set

Decision: Status is partial; complete path-aware publication and verification of all applicable remediation artifact types.
Evidence: `moonmind/workflows/temporal/remediation_context.py` defines `REMEDIATION_ARTIFACT_TYPES`; `RemediationLifecyclePublisher` publishes JSON artifacts; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_lifecycle_publisher_creates_required_artifacts` verifies several required types and metadata. Representative run-path coverage remains incomplete.
Rationale: The repository has reusable publishing primitives but the MM-623 requirement is stronger than primitive availability; every representative remediation run path needs evidence that applicable artifacts are present and non-applicable artifacts are explained.
Alternatives considered: Treating the publisher helper as complete was rejected because it does not prove all remediation lifecycle paths invoke it consistently.
Test implications: Unit tests for artifact classification helpers; integration tests for representative diagnosis-only, action-attempted, degraded, and escalated paths.

## FR-002 - Remediation Artifact Classification

Decision: Status is implemented_verified; preserve existing artifact classification behavior while extending scenario coverage.
Evidence: `REMEDIATION_ARTIFACT_TYPES` includes `remediation.context`, `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.verification`, and `remediation.summary`; existing unit coverage verifies artifact metadata and links.
Rationale: Current code and tests directly prove artifact classification for the required type family.
Alternatives considered: Reworking artifact taxonomy was rejected because the source design already names the target taxonomy and existing code implements it.
Test implications: No new standalone unit test is required for the constant set, but integration tests should ensure all representative publications preserve the metadata.

## FR-003 / SCN-002 - Decision Log Completeness

Decision: Status is partial; add outcome-matrix coverage for repair candidates and prevention/no-PR reasons.
Evidence: `build_remediation_decision_log()` redacts metadata and requires bounded decision entries; `publish_lifecycle_summary()` publishes the decision log artifact; tests cover attempted and cancellation examples.
Rationale: The existing builder is suitable, but the story requires attempted, skipped, denied, escalated, prevention, verification refs, and no-PR reasons to be reviewable.
Alternatives considered: Only using action authority audit payloads was rejected because operators need an artifact-backed decision log that is readable after run completion.
Test implications: Unit tests for every decision outcome shape; integration tests proving the log artifact is linked and referenced by the summary.

## FR-004 / DESIGN-REQ-028 - Stable Remediation Summary

Decision: Status is implemented_unverified; verify the full MM-623 field set and patch if gaps appear.
Evidence: `build_remediation_summary_block()` emits target IDs, phase, mode, authority, actions, resolution, lock conflicts, approvals, degraded state, escalation, unavailable evidence, fallback data, and resulting run ID. `build_remediation_final_summary()` attaches repair/prevention and decision refs. Existing tests cover repaired and escalated samples.
Rationale: The core behavior appears present, but MM-623 needs a complete representative field check across repair, prevention, degraded, and escalation outcomes.
Alternatives considered: Marking implemented_verified was rejected because current tests do not explicitly cover the entire story matrix.
Test implications: Verification-first unit and integration tests; implementation contingency if required fields or validation are missing.

## FR-005 / SCN-003 - Queryable Audit Events

Decision: Status is partial; add durable queryable audit event persistence for side-effecting remediation decisions.
Evidence: `build_remediation_audit_event()` produces bounded metadata and redacts unsafe fields, but repo search did not find a remediation-specific persistence/query path for those events. Existing control/audit surfaces exist for other domains, such as settings audit and managed-session control events.
Rationale: A builder alone does not satisfy "queryable audit events"; operators and control-plane queries need persisted compact records or a reused queryable event mechanism.
Alternatives considered: Storing audit events only inside summary artifacts was rejected because artifacts are the deep evidence trail, while the spec requires compact queryable control-plane records.
Test implications: Unit tests for audit event validation/redaction and integration tests proving events are persisted and queryable by remediation workflow/run or target identity.

## FR-006 / SCN-004 - Target-Side Mutation Annotations

Decision: Status is missing; add a publication path for supplemental target-side annotations when remediation mutates a target-managed session or workload.
Evidence: Managed-session control event artifacts exist in runtime supervisor/controller code, but no remediation-specific target-side annotation publication path was found in remediation action execution.
Rationale: Source design requires remediation audit trail to supplement subsystem-native artifacts. A side-effecting remediation action needs visible linkage on the target side without overwriting target-native continuity or control artifacts.
Alternatives considered: Treating remediation-side action artifacts as sufficient was rejected because the requirement explicitly mentions target-side annotations.
Test implications: Integration test for a side-effecting action that preserves native target records and adds a supplemental remediation annotation/ref.

## FR-007 / SCN-005 - Bounded Degraded, Skipped, Unsafe, and Escalated States

Decision: Status is partial; expand representative coverage and patch lifecycle serialization if any state is ambiguous.
Evidence: `normalize_remediation_phase()`, `normalize_remediation_resolution()`, `build_remediation_summary_block()`, and lifecycle tests cover degraded and escalated examples.
Rationale: The primitives normalize states, but the operator-facing story requires every missing/degraded/skipped/unsafe/escalated state to carry a bounded reason.
Alternatives considered: Relying on generic failure status was rejected because remediation evidence must explain the decision path.
Test implications: Unit tests for serialization and integration tests for representative no-action, unsafe, evidence-unavailable, and escalated runs.

## FR-008 / SC-005 - Artifact Presentation Safety

Decision: Status is implemented_unverified; add remediation-specific presentation checks against the existing artifact presentation contract.
Evidence: `docs/Artifacts/ArtifactPresentationContract.md` requires safe metadata, artifact refs instead of URLs, and redaction behavior. Remediation context artifacts use restricted redaction and artifact refs. Existing remediation tests check several redaction helpers.
Rationale: Existing platform rules should satisfy the requirement, but MM-623 needs direct proof for remediation artifact metadata and previews.
Alternatives considered: Duplicating artifact presentation logic in remediation services was rejected because the generic artifact contract owns presentation behavior.
Test implications: Unit and integration tests asserting remediation artifact metadata and preview/default-read data contain no secrets, raw local paths, raw storage keys, or presigned URLs.

## FR-009 / SC-006 - Jira Traceability

Decision: Status is implemented_verified for planning artifacts; preserve through downstream work.
Evidence: `specs/323-publish-remediation-audit/spec.md` preserves the original MM-623 preset brief; this plan, research, data model, contract, and quickstart preserve MM-623 and source IDs.
Rationale: Traceability is currently satisfied at the specification and planning stage.
Alternatives considered: Storing only the issue key was rejected because final verification must compare against the original preset brief.
Test implications: Final verification should run a traceability search across feature artifacts and delivery metadata.

## Test Strategy

Decision: Use focused unit tests for serialization, validation, and redaction; use hermetic integration tests for artifact persistence, audit queryability, and target-side annotation behavior.
Evidence: Existing repo instructions require `./tools/test_unit.sh` for unit tests and `./tools/test_integration.sh` for required hermetic integration tests. Existing remediation coverage already uses `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py`.
Rationale: The feature crosses service/activity and artifact boundaries, so isolated helper tests are not enough.
Alternatives considered: Provider verification tests were rejected because MM-623 does not require external provider credentials.
Test implications: Add or update unit tests first, then integration tests, then run the full unit suite and required hermetic integration suite before final verification.

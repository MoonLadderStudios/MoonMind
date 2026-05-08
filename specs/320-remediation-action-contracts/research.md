# Research: Remediation Action Contracts

## Story Scope And Classification

Decision: Treat MM-620 as a single-story runtime feature and continue from `specs/320-remediation-action-contracts/spec.md`.
Evidence: `/work/agent_jobs/mm:f14332d1-2a04-407d-acdd-23b4fa3c3448/repo/specs/320-remediation-action-contracts/spec.md` contains exactly one `## User Story - Typed Remediation Actions`, preserves `MM-620`, and maps `DESIGN-REQ-015`, `DESIGN-REQ-016`, `DESIGN-REQ-017`, and `DESIGN-REQ-026`.
Rationale: The Jira brief selects one independently testable runtime behavior: typed remediation action listing/request/result evidence.
Alternatives considered: Running `moonspec-breakdown` was rejected because the brief is not a broad multi-story design.
Test implications: none beyond final MoonSpec traceability verification.

## FR-001 And FR-002 Registry Listing Metadata

Decision: Status `implemented_verified`; preserve existing behavior.
Evidence: `moonmind/workflows/temporal/remediation_actions.py` defines `_ACTION_CATALOG` and `RemediationActionAuthorityService.list_allowed_actions()`. `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_action_authority_lists_canonical_mm483_action_registry` verifies canonical action kinds and required metadata: risk tier, target type, input metadata, preconditions, idempotency, verification hint, and audit payload shape.
Rationale: Current implementation and tests directly cover registry listing and metadata requirements.
Alternatives considered: Rebuilding a separate registry service was rejected because the existing authority service is already the runtime boundary.
Test implications: rerun focused unit tests; no new implementation expected.

## FR-003 Request Evaluation Inputs And Preconditions

Decision: Status `implemented_verified`; preserve action input and idempotency-shape validation.
Evidence: `RemediationActionAuthorityService.evaluate_action_request()` validates action kind, authority mode, permissions, security profile, approval, risk, dry-run, idempotency key, action-specific `inputMetadata`, and duplicate idempotency key request shape. `RemediationMutationGuardService.evaluate()` covers lock, ledger, budgets, nested remediation, and target freshness. `RemediationEvidenceToolService.prepare_action_request()` rereads target health before execution. `test_remediation_action_authority_validates_action_inputs`, `test_remediation_action_authority_cache_keys_include_request_shape`, and `test_remediation_action_authority_uses_prepared_action_context` verify these paths.
Rationale: The decision chain now rejects unsupported action parameters and denies reused idempotency keys with different request shapes before authorization.
Alternatives considered: Treating guard evaluation as complete input validation was rejected because guard checks do not cover every declared input shape.
Test implications: rerun focused unit tests and integration artifact-boundary tests.

## FR-004 V1 Action Request Evidence

Decision: Status `implemented_verified`; preserve v1 request artifact publication.
Evidence: `RemediationActionAuthorityResult.to_dict()` produces a v1 `request` object with `schemaVersion`, `actionId`, `actionKind`, requester, target, risk tier, dry-run flag, idempotency key, and params. `RemediationEvidenceToolService.execute_action()` publishes a redacted `remediation.action_request` artifact whose top-level payload is the v1 request contract plus authority and guard evidence. `test_remediation_execute_action_publishes_v1_request_and_result_artifacts` and `test_remediation_action_contract_publishes_request_result_and_verification` read the published artifact payload directly.
Rationale: The durable artifact boundary is now directly verified.
Alternatives considered: Relying on `to_dict()` tests alone was rejected because final verification must prove the published evidence artifact, not just an in-memory object.
Test implications: rerun focused unit tests and integration artifact-boundary tests.

## FR-005 V1 Action Result Evidence

Decision: Status `implemented_verified`; preserve the completed result artifact contract.
Evidence: `RemediationEvidenceToolService.execute_action()` publishes a `remediation.action_result` artifact with `schemaVersion`, `actionKind`, `actionId`, allowed `status`, user-safe `message`, `appliedAt` when applicable, before/after refs, `verificationRequired`, `verificationHint`, and redacted `sideEffects`. `test_remediation_execute_action_publishes_v1_request_and_result_artifacts` and `test_remediation_action_contract_publishes_request_result_and_verification` read and verify the published result artifact.
Rationale: The durable result artifact now matches the v1 contract and stays linked to verification evidence.
Alternatives considered: Keeping verification details only in a separate `remediation.verification` artifact was rejected because the result evidence contract must still indicate whether verification is required and how to perform it.
Test implications: rerun focused unit tests and integration artifact-boundary tests.

## FR-006 Status Enumeration

Decision: Status `implemented_verified`; preserve fail-fast status normalization/validation.
Evidence: `RemediationActionAuthorityResult.to_dict()` maps decisions to result statuses for authority outcomes. `RemediationEvidenceToolService.execute_action()` validates executor `status` against `applied`, `no_op`, `rejected`, `precondition_failed`, `approval_required`, `timed_out`, and `failed` before publishing result artifacts. `test_remediation_execute_action_rejects_unsupported_result_status` verifies unsupported statuses fail closed.
Rationale: Unsupported runtime result statuses cannot drift into durable artifacts.
Alternatives considered: Allowing arbitrary executor statuses was rejected by the compatibility policy and the spec's explicit status set.
Test implications: rerun focused unit tests.

## FR-007 High-Risk Actions

Decision: Status `implemented_verified`; preserve behavior.
Evidence: `_ACTION_CATALOG` marks high-risk actions such as `execution.force_terminate`, `session.terminate`, and `session.restart_container`; `test_remediation_action_authority_enforces_profile_permissions_and_risk` verifies high-risk approval-required behavior.
Rationale: Existing code and tests directly cover the requirement.
Alternatives considered: Deferring high-risk handling to external approval services was rejected because the action authority service must make bounded decisions before side effects.
Test implications: rerun focused unit tests.

## FR-008 Unsupported Raw Operations

Decision: Status `implemented_verified`; preserve explicit raw-operation denial coverage.
Evidence: `_RAW_ACCESS_ACTION_KINDS` and raw-prefix checks deny host shell, database, Docker, volume mount, network egress, secret-reading, storage-key, and redaction-bypass classes before side effects. `test_remediation_action_authority_denies_raw_access_and_unknown_targets` covers the explicit unit cases, and `test_remediation_raw_action_rejection_does_not_publish_side_effect_artifacts` verifies no side-effect artifacts are published for a raw action attempt.
Rationale: Explicit proof for every spec-listed class reduces risk that a future catalog addition accidentally exposes raw access.
Alternatives considered: Treating generic `unsupported_action_kind` as enough was rejected because the spec requires rejection by kind before side effect for sensitive raw operation classes.
Test implications: rerun focused unit tests and integration artifact-boundary tests.

## FR-009 Unsupported V1 Action Availability

Decision: Status `implemented_verified`; preserve omit/deny behavior.
Evidence: `list_allowed_actions()` filters actions by enabled catalog and security profile allowlist. `test_remediation_action_authority_rejects_legacy_action_aliases` and `test_remediation_action_authority_lists_policy_compatible_actions` verify unsupported/legacy actions are not advertised.
Rationale: The listing surface already omits unsupported actions and the evaluator denies unsupported kinds.
Alternatives considered: Advertising unavailable actions with raw fallback instructions was rejected by the source design.
Test implications: rerun focused unit tests.

## FR-010 Traceability

Decision: Status `implemented_verified`; preserve through later artifacts.
Evidence: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/remediation-action-contracts.md` preserve `MM-620`.
Rationale: Final verification must compare implementation and artifacts to the original Jira preset brief.
Alternatives considered: Relying on Jira links alone was rejected because MoonSpec verification consumes local artifacts.
Test implications: final MoonSpec verification.

## Unit Test Strategy

Decision: Use focused pytest tests through `./tools/test_unit.sh`, primarily `tests/unit/workflows/temporal/test_remediation_context.py` and related Temporal service tests.
Evidence: Existing remediation action, guard, context, and tool tests already live in that module and use local SQLite plus local artifact store fixtures.
Rationale: Unit tests can validate registry metadata, request/result serialization, status validation, redaction, idempotency, and no-side-effect denial without external services.
Alternatives considered: Adding a new test module was considered, but extending the existing remediation test module keeps related boundary fixtures together.
Test implications: unit tests first for every partial or unverified item.

## Integration Test Strategy

Decision: Add hermetic integration coverage for the real remediation action service/artifact boundary.
Evidence: Existing integration taxonomy requires `integration` + `integration_ci` for compose-backed hermetic checks; `RemediationEvidenceToolService.execute_action()` publishes real Temporal artifacts and updates remediation links; `tasks.md` includes T011 through T013 and T023 for this boundary.
Rationale: Unit tests cover most logic, but durable action request/result/verification artifact publication is an integration boundary required by DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.
Alternatives considered: Provider verification was rejected because this story does not require live external providers. Unit-only coverage was rejected because final evidence must prove the durable artifact boundary.
Test implications: add `integration` + `integration_ci` tests under `tests/integration/temporal/test_remediation_action_contracts.py` and run `./tools/test_integration.sh`.

## Storage And Migration Decision

Decision: Reuse existing execution remediation links and artifact storage; no new persistent table is planned.
Evidence: `TemporalExecutionRemediationLink` stores remediation relationship/action summary state; `RemediationLifecyclePublisher` and `TemporalArtifactService` already publish context, action, result, and verification artifacts.
Rationale: The story requires durable bounded evidence, which fits existing artifact-backed storage.
Alternatives considered: A dedicated action ledger table was rejected for this story because current guard/ledger behavior and durable artifacts cover the v1 evidence needs; a future analytics story can revisit persistence if required.
Test implications: tests should assert artifact contents and link updates, not a new migration.

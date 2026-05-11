# Research: Authoritative Task Snapshot Durability

## FR-001 Snapshot Association For Submitted Executions

Decision: Status `implemented_unverified`; verify every covered submission path associates a retrievable snapshot before action evaluation.
Evidence: `api_service/api/routers/executions.py` persists snapshots in task create, direct `MoonMind.Run` create, edit/rerun update, and rerun paths; `moonmind/workflows/temporal/worker_runtime.py` persists child Jira-Orchestrate run snapshots.
Rationale: The implementation exists in multiple paths, but MM-639 requires 100% coverage for covered task flows, which should be proven in one cohesive verification set.
Alternatives considered: Mark `implemented_verified`; rejected because evidence is spread across historical tests and not framed against MM-639's full entrypoint set.
Test implications: Unit tests for route helper calls and integration tests for actual artifact persistence.

## FR-002 Section 7 Field Completeness

Decision: Status `partial`; add explicit schema/contract coverage for all authored fields listed in the spec.
Evidence: `_build_original_task_input_snapshot_payload()` stores `repository`, `targetRuntime`, `requiredCapabilities`, `task`, `attachmentRefs`, and compact metadata. Existing tests assert instructions and attachment refs, but not every Section 7 field.
Rationale: Copying `task` is likely enough for several fields, but the requirement is explicit and should not depend on implicit pass-through behavior without contract coverage.
Alternatives considered: Introduce a new database table; rejected because existing artifact-backed snapshots satisfy persistence needs.
Test implications: Unit tests for payload construction and contract/integration tests for real stored artifact content.

## FR-003 Catalog Independence

Decision: Status `implemented_unverified`; add verification tests that reconstruction uses the snapshot even when live preset definitions change or are absent.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` rebuilds drafts from `taskInputSnapshot` artifact input; rerun update hydration reads input artifacts before persisting snapshots.
Rationale: The code path is designed for snapshot-first reconstruction, but live catalog divergence should be explicitly tested.
Alternatives considered: Re-resolve live presets on edit/rerun; rejected because the spec and source design forbid live catalog dependency for submitted work.
Test implications: Frontend unit tests and API/contract tests that avoid or mutate catalog fixtures.

## FR-004 Exact Full Rerun

Decision: Status `implemented_verified`; preserve current behavior.
Evidence: `tests/integration/temporal/test_full_retry_recovery_actions.py` and `tests/unit/workflows/temporal/test_temporal_service.py` verify rerun removes resume progress and starts from original refs.
Rationale: Existing tests directly match exact full rerun requirements.
Alternatives considered: Add more implementation; rejected pending final verification because current behavior is already covered.
Test implications: None beyond final verification and regression suite selection.

## FR-005 Edited Full Retry New Snapshot

Decision: Status `implemented_unverified`; add verification-first tests.
Evidence: The update route persists a snapshot with `source_kind="edit"` for `UpdateInputs` and source identity for task editing updates.
Rationale: Code appears present, but proof should show the edited execution gets a new snapshot and the source execution evidence remains unchanged.
Alternatives considered: Treat edited full retry as exact rerun with parameter patch; rejected because it would blur recovery intents.
Test implications: Unit route test plus integration/service test if available.

## FR-006 Resume Input Immutability

Decision: Status `implemented_verified`; preserve current behavior.
Evidence: `api_service/api/routers/executions.py` rejects edited task/runtime payload fields for failed-step Resume; `moonmind/workflows/temporal/service.py` requires the checkpoint snapshot ref to match the source snapshot; unit tests cover both behaviors.
Rationale: Existing route and service tests match the required Resume immutability and source pinning behavior.
Alternatives considered: Permit limited Resume edits; rejected by source design and spec.
Test implications: None beyond final verification.

## FR-007 Attachment-Aware Degraded State

Decision: Status `partial`; add degraded-state coverage and patch if any recovery path silently drops or synthesizes attachments.
Evidence: Missing snapshots disable edit/rerun actions with `original_task_input_snapshot_missing`; `TaskInputSnapshotDescriptorModel` can report `degraded_read_only`; frontend reconstruction throws when compact attachment bindings cannot be matched.
Rationale: The pieces exist, but MM-639 requires explicit behavior for attachment-aware executions across recovery action evaluation.
Alternatives considered: Allow fallback reconstruction from execution parameters; rejected because it can silently lose attachment targets.
Test implications: Unit tests for descriptor/action disabled reasons and frontend attachment binding failure; integration test for attachment-aware missing snapshot behavior.

## FR-008 Snapshot And Degraded Metadata Exposure

Decision: Status `implemented_unverified`; add verification tests for response shape and disabled reasons.
Evidence: `_task_input_snapshot_descriptor_from_record()` serializes availability, artifact ref, snapshot version, reconstruction mode, disabled reasons, and fallback refs; action disabled reasons identify missing snapshots.
Rationale: Existing coverage is close but should be asserted against MM-639 scenarios.
Alternatives considered: Add a separate endpoint; rejected because execution detail already carries the operator-facing descriptor.
Test implications: Unit/API contract tests.

## FR-009 Traceability

Decision: Status `implemented_verified`; preserve through downstream artifacts.
Evidence: `spec.md` preserves MM-639 and the canonical Jira preset brief; `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and this contract reference MM-639.
Rationale: Traceability is already present in feature artifacts.
Alternatives considered: Store traceability only in Jira comments; rejected because MoonSpec verification reads local artifacts.
Test implications: Final verification only.

## Source Design Requirements

Decision: Status mix: DESIGN-REQ-004 and DESIGN-REQ-011 are `partial`; DESIGN-REQ-012 and DESIGN-REQ-013 are `implemented_unverified`.
Evidence: `docs/Tasks/TaskArchitecture.md` sections 3.4, 5.5, 7, and 11 define authoritative snapshot, attachment target binding, recovery intent separation, and Resume invariants.
Rationale: The current implementation has the main architecture, but explicit field completeness and degraded attachment behavior need stronger proof.
Alternatives considered: Treat documentation as already complete; rejected because this is a runtime story.
Test implications: Unit + integration tests, with final verification preserving source IDs.

## Test Strategy

Decision: Use focused unit tests for serialization, payload construction, frontend draft reconstruction, action gating, and service validation; use hermetic integration/contract tests for persisted artifact content and recovery action behavior.
Evidence: Existing test surfaces include `tests/unit/api/routers/test_executions.py`, `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/contract/test_temporal_execution_api.py`, and `tests/integration/temporal/test_full_retry_recovery_actions.py`.
Rationale: MM-639 spans API, frontend reconstruction, artifact persistence, and Temporal service behavior, so isolated tests alone are insufficient.
Alternatives considered: Run provider verification; rejected because no external credentials are required for this story.
Test implications: Final commands are `./tools/test_unit.sh` and `./tools/test_integration.sh` after focused iteration.

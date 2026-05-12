# Research: Backend-Computed Resume Eligibility

Traceability: MM-643, `spec.md` FR-001 through FR-010.

## FR-001 / SCN-001 / SC-001

Decision: implemented_unverified; verify distinct Edit task, Rerun, and Resume capability combinations before implementation.
Evidence: `api_service/api/routers/executions.py` `_build_action_capabilities()` returns separate `canEditForRerun`, `canRerun`, and `canResumeFromFailedStep`; `frontend/src/entrypoints/task-detail.tsx` renders separate controls.
Rationale: The code supports the surface, but MM-643 needs a complete action matrix tied to backend capability state.
Alternatives considered: Mark implemented_verified; rejected because current tests do not cover all combinations as one MM-643 recovery matrix.
Test implications: unit + UI, with integration coverage for the serialized execution detail contract.

## FR-002 / DESIGN-REQ-001

Decision: implemented_unverified; prove the UI never infers Resume locally.
Evidence: Task Detail uses `actions.canResumeFromFailedStep`; unavailable copy uses `actions.disabledReasons.canResumeFromFailedStep`.
Rationale: The UI appears backend-driven, but a focused test should guard against future inference from status or label text.
Alternatives considered: No new UI test; rejected because the story explicitly calls out UI inference risk.
Test implications: UI test plus API unit fixture.

## FR-003

Decision: implemented_unverified; preserve per-execution capability fields and disabled reasons.
Evidence: `ExecutionActionCapabilityModel` serialization includes action booleans and `disabledReasons`.
Rationale: The payload exists, but the plan should ensure disabled reasons for all recovery actions remain bounded and operator-readable.
Alternatives considered: Treat disabled reasons as out of scope; rejected because FR-008 and SC-002 require operator-readable reasons.
Test implications: API unit and contract-style test.

## FR-004 / DESIGN-REQ-004

Decision: implemented_unverified; add negative tests for generic rerun and edited retry with partial Resume-shaped data.
Evidence: `TemporalExecutionService._full_rerun_parameters()` removes recovery carryover; MM-632 tests cover exact rerun omitting `resumeSource`.
Rationale: Existing behavior appears correct, but MM-643 specifically forbids reinterpreting generic rerun as Resume.
Alternatives considered: Reuse MM-632 evidence only; rejected because this story needs its own generic-rerun negative path.
Test implications: service unit + hermetic integration.

## FR-005 / FR-006 / DESIGN-REQ-003

Decision: partial; align accepted recovery submission representation with canonical recovery provenance and failed-step resume reference requirements.
Evidence: `TaskRecoveryProvenance` and `ResumeFromFailedStepRef` exist in `moonmind/workflows/tasks/task_contract.py`; `create_failed_step_resume_execution()` records `resumeSource` in execution parameters.
Rationale: Existing execution metadata carries much of the required information, but the spec names recovery provenance and failed-step resume reference fields that are not clearly emitted as the canonical task contract pair on accepted Resume.
Alternatives considered: Keep `resumeSource` as the only representation; acceptable only if the plan documents it as the execution-boundary representation and tests prove equivalence.
Test implications: task-contract unit, service unit, and integration boundary tests.

## FR-007 / DESIGN-REQ-002

Decision: partial; verify and tighten backend evidence categories.
Evidence: `_resume_evidence_disabled_reason()` checks checkpoint ref, failed-step id, completed-step refs, workspace checkpoint, and plan identity; `_build_action_capabilities()` separately requires task input snapshot.
Rationale: The current check covers many categories but does not clearly prove source workflow ID/source run ID pinning, ledger-derived failed-step identity, or completed-step completeness before availability.
Alternatives considered: Require full checkpoint hydration during detail reads; may be too expensive for polling, so implementation can use compact validated metadata if it is authoritative.
Test implications: API unit and integration tests for every required evidence category.

## FR-008 / SC-002

Decision: partial; extend unavailable and rejection reasons.
Evidence: Missing evidence reasons exist; checkpoint hydration maps authorization and corrupted payload errors; service mismatch errors map to bounded reasons.
Rationale: Stale evidence and some inconsistent cases are not clearly represented, and final behavior must fail before recovery work starts.
Alternatives considered: One generic unavailable reason; rejected because operators need actionable reasons.
Test implications: API route unit tests and integration rejection tests.

## FR-009 / SC-005

Decision: implemented_unverified; broaden forbidden field coverage.
Evidence: `resume_execution_from_failed_step()` rejects task, instructions, steps, attachments, runtime, publish, branch, presets, dependencies, model, effort, and artifact/plan mutation fields; Task Detail Resume sends only checkpoint and operator metadata.
Rationale: Behavior appears present, but a field-category matrix is needed to prove edits route to edited full retry instead of Resume.
Alternatives considered: Existing single test only; rejected because the spec enumerates many mutation categories.
Test implications: API unit + UI tests.

## FR-010 / SC-006

Decision: implemented_unverified; preserve MM-643 traceability through all artifacts.
Evidence: `spec.md` preserves MM-643 and the original Jira preset brief; this research preserves MM-643.
Rationale: Final verification requires comparing implementation evidence to the original preset brief.
Alternatives considered: Preserve only issue key; rejected because the original brief is required for verification.
Test implications: final verify.

## Test Strategy

Decision: use separate unit and hermetic integration strategies.
Evidence: Repo instructions require `./tools/test_unit.sh` for unit verification and `./tools/test_integration.sh` for `integration_ci`.
Rationale: Recovery behavior crosses API serialization, task contract normalization, Temporal service creation, and UI rendering, so isolated tests are insufficient.
Alternatives considered: Unit-only coverage; rejected because recovery submission is a boundary-sensitive workflow contract.
Test implications: unit + integration, with focused UI tests when Task Detail behavior changes.

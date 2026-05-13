# Phase 0 Research: Resume Execution Semantics

## FR-001 / Source Workflow And Run Identity

Decision: `implemented_unverified`; add workflow-boundary proof.
Evidence: `moonmind/workflows/tasks/task_contract.py` defines `TaskRecoveryProvenance` and `ResumeFromFailedStepRef`; `moonmind/workflows/temporal/service.py::create_failed_step_resume_execution()` sets canonical source workflow/run IDs; `tests/integration/temporal/test_backend_resume_eligibility.py::test_accepted_resume_carries_canonical_recovery_and_resume_refs` verifies accepted task payload refs.
Rationale: Service-level accepted Resume shape is covered, but MM-647 is specifically about `MoonMind.Run` execution semantics, so the workflow boundary must prove the source IDs are consumed before execution starts.
Alternatives considered: Mark verified from service tests alone; rejected because service creation can succeed while workflow initialization still mishandles source metadata.
Test implications: Unit and integration.

## FR-002 / Checkpoint Validation Before Execution

Decision: `partial`; keep service validation and add workflow fail-fast guard or boundary proof.
Evidence: `TemporalExecutionService.create_failed_step_resume_execution()` validates source workflow ID, source run ID, task input snapshot ref, plan ref/digest, and failed step ID against `ResumeCheckpointModel`.
Rationale: Validation exists before normal route-created resumed executions. Direct workflow parameters, replayed histories, or malformed persisted `resumeSource` payloads still need a defensible `MoonMind.Run` behavior.
Alternatives considered: Depend only on the API/service preflight; rejected because Temporal workflow payloads are compatibility-sensitive and may already be in flight.
Test implications: Unit and integration.

## FR-003 / Original Task Input Snapshot

Decision: `partial`; verify unchanged snapshot use at workflow initialization.
Evidence: `ResumeFromFailedStepRef` requires `taskInputSnapshotRef`; API route tests reject edited Resume payload fields; service creates resumed task payload from source parameters and checkpoint snapshot ref.
Rationale: The route prevents edited Resume submissions, but the workflow should preserve or validate original snapshot semantics when starting from failed-step Resume metadata.
Alternatives considered: Treat API rejection as sufficient; rejected because the spec requires execution semantics, not only submission semantics.
Test implications: Unit and integration.

## FR-004 / Preserved Prior Steps

Decision: `implemented_unverified`; add real workflow proof.
Evidence: `moonmind/workflows/temporal/step_ledger.py::materialize_preserved_steps()` marks preserved rows; `moonmind/workflows/temporal/workflows/run.py::_initialize_step_ledger()` applies `resumeSource.preservedSteps`; helper and integration tests cover materialization and readiness.
Rationale: The helper behavior is covered, but no end-to-end `MoonMind.Run` scenario proves preserved steps are skipped in a resumed run.
Alternatives considered: Mark verified from helper tests; rejected because the workflow may not call the helper in all resume entry paths.
Test implications: Integration first, unit if implementation changes.

## FR-005 / Preserved Provenance

Decision: `implemented_unverified`; add workflow projection proof.
Evidence: Preserved rows include `preservedFrom.workflowId`, `runId`, `logicalStepId`, and `attempt`; tests assert the helper output.
Rationale: Provenance exists in helper data but must survive workflow initialization and projection used by operators.
Alternatives considered: Treat helper output as sufficient; rejected because projection and workflow state can diverge.
Test implications: Unit and integration.

## FR-006 / Workspace Restoration

Decision: `missing`; implement or wire a pre-failed-step workspace restoration boundary.
Evidence: `ResumeSourceModel` carries `resumeWorkspace`; search found no `MoonMind.Run` code that materializes `resumeWorkspace` before the failed step starts.
Rationale: The checkpoint can describe workspace state, but execution semantics require the run to restore or verify that state before retry.
Alternatives considered: Rely on provider/runtime session state; rejected because the source design requires explicit restoration and explicit failure when incomplete.
Test implications: Unit and integration.

## FR-007 / Preserved Step Display

Decision: `implemented_unverified`; add projection proof from actual resumed run state.
Evidence: Step ledger rows contain `preservedFrom`; `tests/unit/api/routers/test_executions.py` verifies target diagnostics can expose recovery preserved steps.
Rationale: Existing diagnostics prove a manually shaped record can serialize recovery; actual resumed-run projection needs coverage.
Alternatives considered: Mark verified from serialization test; rejected because the source of projected rows matters.
Test implications: Unit plus API/UI or integration.

## FR-008 / Preserved Output Injection

Decision: `partial`; add verification-first tests for input composition.
Evidence: `materialize_preserved_steps()` retains artifact refs in preserved rows; no proof found that failed/downstream step input composition consumes preserved outputs.
Rationale: Ledger preservation is necessary but insufficient if downstream prompts or step contracts cannot see preserved outputs.
Alternatives considered: Treat row artifacts as the injection mechanism; rejected until a step input/prompt composition test proves it.
Test implications: Unit and integration.

## FR-009 / Failed Step First

Decision: `implemented_unverified`; add workflow ordering proof.
Evidence: `refresh_ready_steps()` marks the dependent failed step ready after preserved prior steps; helper tests assert readiness.
Rationale: The first newly executed step is a workflow scheduling behavior, not just row readiness.
Alternatives considered: Mark verified from readiness tests; rejected because actual execution loops may still schedule incorrectly.
Test implications: Integration.

## FR-010 / Downstream Continuation

Decision: `implemented_unverified`; add resumed-run downstream scenario.
Evidence: Step ledger dependency readiness can unblock later steps after a predecessor succeeds.
Rationale: Downstream continuation after a retried failed step needs real run evidence.
Alternatives considered: Infer from generic dependency handling; rejected because Resume has preserved rows and retry state.
Test implications: Integration.

## FR-011 / Fresh Resumed-Run Evidence

Decision: `partial`; verify retried and later steps produce fresh evidence.
Evidence: `run.py::_record_step_result_evidence()` and `_record_step_checkpoint_evidence()` can record artifacts and checkpoint refs; source/resumed evidence separation is not fully tested.
Rationale: A resumed run must not reuse source evidence for new attempts.
Alternatives considered: Use existing step-ledger tests only; rejected because source-vs-resumed provenance is the key behavior.
Test implications: Unit and integration.

## FR-012 / Explicit Invalid Restoration Failure

Decision: `partial`; add direct workflow no-fallback checks.
Evidence: API/service reject missing, stale, unauthorized, invalid, or inconsistent checkpoint evidence before creating a resumed execution.
Rationale: Service preflight is strong but does not prove `MoonMind.Run` itself fails explicitly if restoration fails or malformed resume metadata reaches the workflow.
Alternatives considered: Treat preflight as the only supported path; rejected because workflow payload compatibility and replay safety require explicit behavior.
Test implications: Unit and integration.

## FR-013 / Reject Edited Resume Input

Decision: `implemented_verified`; preserve existing behavior.
Evidence: `tests/unit/api/routers/test_executions.py::test_failed_step_resume_request_rejects_edited_task_payload_fields` covers task, runtime, attachment, publish, branch, preset, dependency, model, and artifact-field edits.
Rationale: The route-level no-edit contract is explicit and covered.
Alternatives considered: Add redundant tests now; not needed unless implementation touches the route or task contract.
Test implications: None beyond final verify unless touched.

## FR-014 / Jira Traceability

Decision: `implemented_verified`; preserve through downstream artifacts.
Evidence: `spec.md` and this planning artifact preserve `MM-647` and the original Jira preset brief.
Rationale: Traceability exists and must be carried forward.
Alternatives considered: None.
Test implications: Final MoonSpec verification.

## Testing Strategy

Decision: Use focused unit tests for model/helper/route guards and hermetic integration tests for `MoonMind.Run` resume execution.
Evidence: Repository instructions require `./tools/test_unit.sh`; hermetic integration uses `./tools/test_integration.sh`; existing tests already live in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/api/routers/test_executions.py`, `tests/integration/temporal/test_backend_resume_eligibility.py`, and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`.
Rationale: The high-risk behavior is a Temporal workflow boundary with payload compatibility concerns, so unit-only coverage is insufficient.
Alternatives considered: Full integration suite only; rejected because unit tests are needed for red-first diagnostics and fast iteration.
Test implications: Both unit and integration.

# Research: Gate Resume on Durable Checkpoint Evidence

Traceability: MM-633, source coverage IDs DESIGN-REQ-013 and DESIGN-REQ-016, spec FR-001 through FR-013.

## FR-001 / SCN-001 / SC-001 / DESIGN-REQ-001 - Backend Evidence-Based Eligibility

Decision: `partial`; backend eligibility exists but is not yet complete evidence validation.
Evidence: `_build_action_capabilities()` in `api_service/api/routers/executions.py` sets `canResumeFromFailedStep` for failed `MoonMind.Run` executions with a task input snapshot and resume checkpoint ref. `ExecutionResumeSummaryModel` exposes availability, checkpoint ref, failed step id, source run id, and disabled reason.
Rationale: The UI does not infer Resume by itself, but the backend gate currently treats checkpoint-ref presence as sufficient. MM-633 requires backend evaluation of all required evidence before offering Resume.
Alternatives considered: Deferring full validation to submission was rejected because the acceptance criteria require Resume to be offered only when the backend can prove recoverability.
Test implications: Unit matrix for availability and disabled reasons; integration coverage for valid and invalid evidence paths.

## FR-002 / FR-003 - Original Snapshot And Pinned Source Identity

Decision: `implemented_unverified`; service and model checks exist, but targeted coverage should prove both availability and submission behavior.
Evidence: `_build_action_capabilities()` requires `_task_input_snapshot_ref_from_memo()` for Resume availability. `ResumeCheckpointSourceModel` requires `workflowId` and `runId`; `TemporalExecutionService.create_failed_step_resume_execution()` rejects missing source run id, workflow mismatch, run mismatch, and task snapshot mismatch.
Rationale: The core validation is present. The plan still requires focused tests to prove missing snapshot/source identity blocks with operator-readable reasons at both surfaces.
Alternatives considered: Marking implemented_verified was rejected because current tests focus on selected service mismatch cases and not the full availability-to-submission contract.
Test implications: Unit route/service tests; one integration test for source identity mismatch at the boundary.

## FR-004 / FR-005 / FR-010 - Failed-Step Ledger And Completed-Step Refs

Decision: `partial`; checkpoint models validate preserved steps when present, but no source-ledger completeness check exists.
Evidence: `ResumeCheckpointFailedStepModel` requires `logicalStepId`, order, and attempt. `ResumeCheckpointPreservedStepModel` rejects preserved steps without artifact refs. `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` verifies materializing a preserved step unblocks a failed step.
Rationale: The data shape supports failed and preserved step evidence, but MM-633 requires proving the last failed step and all completed prior work from source ledger evidence. Current code does not compare checkpoint preserved steps against completed source ledger rows.
Alternatives considered: Trusting checkpoint artifact contents was rejected because corrupted or incomplete checkpoints must block before execution.
Test implications: Unit tests for ledger/checkpoint comparison and integration tests proving incomplete preserved refs block Resume.

## FR-006 / SCN-005 / SC-006 - Workspace Or Branch Checkpoint And Compact Refs

Decision: `missing` for required workspace evidence and `partial` for large/binary ref handling.
Evidence: `ResumeCheckpointModel.resume_workspace` defaults to `{}` and `test_resume_checkpoint_model_allows_empty_optional_resume_sections` explicitly allows empty optional resume sections. General payload policy tests cover large bodies, but checkpoint-specific inline content enforcement is not present.
Rationale: The spec requires recoverable workspace, branch, commit, or equivalent checkpoint evidence. Optional empty workspace evidence does not satisfy that contract.
Alternatives considered: Treating prepared artifact refs as enough was rejected because the source design calls out workspace/branch/commit checkpoint separately.
Test implications: Unit model/service tests requiring checkpoint evidence and rejecting inline large/binary checkpoint payloads.

## FR-007 / SCN-007 - Plan Identity Or Digest

Decision: `missing`; plan identity is currently optional.
Evidence: `ResumeCheckpointModel` has optional `planRef` and `planDigest`. `ResumeSourceModel` carries optional `sourcePlanRef` and `sourcePlanDigest`. The service passes `record.plan_ref` and checkpoint digest when present but does not require or compare a plan identity.
Rationale: MM-633 requires proof that restored progress belongs to the same planned step graph, so plan identity/digest must be required and validated.
Alternatives considered: Relying on task input snapshot identity alone was rejected because the same task input can be planned differently.
Test implications: Unit tests for missing and mismatched plan identity; integration test for stale plan digest blocking before execution.

## FR-008 / SCN-006 / SC-005 - Idempotent Checkpoint Writes

Decision: `missing`; resumed execution creation is idempotent, but checkpoint creation/write idempotency is not yet defined for Resume evidence.
Evidence: `TemporalExecutionService._resume_create_idempotency_key()` derives idempotency for the follow-up execution. No equivalent checkpoint write service or test was found for step-boundary Resume checkpoint evidence.
Rationale: The spec explicitly requires checkpoint creation and writes to be idempotent, independent of follow-up execution idempotency.
Alternatives considered: Counting execution idempotency was rejected because retries may occur while recording source checkpoint evidence before any Resume request is submitted.
Test implications: Unit tests for repeat checkpoint writes resolving to the same evidence ref; integration test around retry-safe checkpoint recording if a workflow activity boundary exists.

## FR-009 - Large Or Binary Checkpoint Content Behind Refs

Decision: `partial`; general payload policy exists, but Resume checkpoint-specific validation is incomplete.
Evidence: `moonmind/schemas/temporal_payload_policy.py` and `tests/schemas/test_temporal_payload_policy.py` address large bodies and checkpoint refs generally. `ResumeCheckpointModel` allows arbitrary dict values in `resume_workspace` and preserved-step artifact values.
Rationale: The story requires checkpoint-specific assurance that large or binary content stays behind refs.
Alternatives considered: Relying only on broad payload policy was rejected because checkpoint artifacts are their own contract and can be hydrated before submission.
Test implications: Unit schema/service tests rejecting inline checkpoint bodies and accepting compact refs.

## FR-011 / SCN-003 / SC-003 - Explicit Pre-Execution Failure

Decision: `partial`; selected failure paths exist, but the invalid-evidence matrix is incomplete.
Evidence: `_hydrate_resume_checkpoint_payload()` fails checkpoint hydration with `resume_not_available`; service tests reject invalid/missing payloads, noncanonical checkpoint refs, and run mismatches. Route errors currently collapse many checkpoint failures to `resume_checkpoint_missing`.
Rationale: MM-633 requires missing, stale, unauthorized, corrupted, inconsistent, and stale plan evidence to block before execution with useful operator-readable reasons.
Alternatives considered: Generic failure was rejected because operators need to understand why Resume is unavailable.
Test implications: Unit route/service tests for each reason and integration coverage for no follow-up execution on invalid evidence.

## FR-012 / SC-004 - No Full-Rerun Fallback

Decision: `implemented_unverified`; behavior appears aligned, but explicit proof is needed.
Evidence: `create_failed_step_resume_execution()` raises before `create_execution()` on validation errors. Existing tests assert exceptions for some invalid evidence; no test explicitly asserts that no full rerun or substitute execution is created.
Rationale: The code structure suggests no fallback, but MM-633 needs direct boundary evidence.
Alternatives considered: Inferring from exceptions was rejected because a future catch-and-fallback path would violate the story.
Test implications: Unit and integration assertions that invalid Resume leaves execution creation uncalled and no new run appears.

## FR-013 / SC-007 - Jira Traceability

Decision: `implemented_unverified`; preserve through downstream artifacts and final verification.
Evidence: `specs/327-gate-resume-checkpoint-evidence/spec.md` preserves the MM-633 Jira preset brief verbatim; this plan, research, data model, contract, and quickstart reference MM-633.
Rationale: Final verification must compare implementation against the original Jira brief.
Alternatives considered: Storing only the issue key was rejected because final verification requires the original brief.
Test implications: Final verification and artifact traceability check.

## Test Tooling Strategy

Decision: Use distinct unit and hermetic integration strategies.
Evidence: Repo instructions define `./tools/test_unit.sh`, `./tools/test_unit.sh --ui-args <path>`, and `./tools/test_integration.sh`. Existing relevant tests live in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/schemas/test_temporal_payload_policy.py`, `frontend/src/entrypoints/task-detail.test.tsx`, and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`.
Rationale: Model and service validation are fast unit concerns, while source-ledger preservation and no-execution-on-invalid-evidence need boundary-level integration coverage.
Alternatives considered: API-only tests were rejected because evidence gating spans schemas, artifact hydration, service creation, step ledger, and UI availability.
Test implications: Write failing unit tests first for each missing/partial evidence rule, then add hermetic integration coverage for the complete valid path and at least one invalid-evidence path.

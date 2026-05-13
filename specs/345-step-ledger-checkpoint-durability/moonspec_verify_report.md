# MoonSpec Verification Report

**Feature**: `345-step-ledger-checkpoint-durability`
**Spec**: `/work/agent_jobs/mm:017bbd8c-2454-4c77-ac2c-f8d42e1c7916/repo/specs/345-step-ledger-checkpoint-durability/spec.md`
**Original Request Source**: `spec.md` `Input` for Jira issue MM-646
**Verdict**: ADDITIONAL_WORK_NEEDED
**Confidence**: HIGH

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Preflight | `SPECIFY_FEATURE=345-step-ledger-checkpoint-durability .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` | PASS | Resolved the expected feature directory and required artifacts. |
| Skill projection | `test ! -L .agents/skills && test ! -L .gemini/skills && (test ! -e skills_active \|\| test -L skills_active); git status --porcelain -- .agents/skills .gemini/skills skills_active` | PASS | No active skill projection contamination detected. |
| Focused unit | `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py` | PASS | `149 passed`; frontend suite invoked by runner also passed with `20 passed`, `343 passed \| 229 skipped`. |
| Focused integration-boundary | `./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` | PASS | `8 passed`; frontend suite invoked by runner also passed with `20 passed`, `343 passed \| 229 skipped`. |
| Full unit | `./tools/test_unit.sh` | PASS | Existing verification evidence reports `4938 passed, 1 xpassed, 115 warnings, 16 subtests passed`; not rerun during this verify step because it was already run after the current code changes. |
| Hermetic integration | `./tools/test_integration.sh` | NOT RUN | Blocked by environment: Docker compose build path reached daemon `403 Forbidden` administrative rule and reported missing buildx plugin warning. |
| Diff hygiene | `git diff --check` | PASS | No whitespace errors. |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 | `prepared_context.py` builds compact prepared refs; `run.py` captures them; focused unit test validates snapshot exposure. | VERIFIED | Prepared input refs are compact and exposed on parent step ledger evidence. |
| FR-002 | `run.py::_record_step_result_evidence`; `step_ledger.py::_has_semantic_output_ref`; unit tests cover output refs. | VERIFIED | Semantic output refs are projected into step artifacts. |
| FR-003 | `run.py::_record_step_checkpoint_evidence` and checkpoint extraction from runtime outputs. | PARTIAL | Runtime-returned checkpoint refs are recorded, but when no runtime checkpoint exists, the workflow synthesizes an `artifact://resume-checkpoints/...` ref instead of proving a durable workspace/branch/commit checkpoint exists. |
| FR-004 | `run.py::_deterministic_step_checkpoint_ref`; `test_run_records_prepared_refs_and_idempotent_checkpoint_evidence`. | PARTIAL | Idempotency is covered for a returned checkpoint ref; synthesized fallback identity is deterministic but not proven to correspond to durable recoverable evidence. |
| FR-005 | `ResumeCheckpointModel` compact validation and inline checkpoint payload rejection; focused tests pass. | VERIFIED | Inline payload guard exists for `inlineCheckpointPayload`; refs stay compact in covered paths. |
| FR-006 | `TemporalExecutionService.create_failed_step_resume_execution`; `ResumeCheckpointModel`; focused integration-boundary test. | PARTIAL | Resume consumes durable refs when present, but source production can mark a synthesized checkpoint ref eligible without proving durable checkpoint evidence. |
| FR-007 | `StepLedgerResumePreservationModel`; `mark_step_checkpoint_evidence`; unit tests. | PARTIAL | Eligibility marker exists, but one source path can mark a completed step eligible with a synthesized checkpoint ref. |
| FR-008 | `mark_step_checkpoint_evidence` can report `missing_state_checkpoint`. | PARTIAL | The helper supports the right reason, but `MoonMind.Run` calls `_record_step_checkpoint_evidence()` after success, which supplies a synthesized checkpoint ref when no real checkpoint exists. No test covers "output refs present, checkpoint absent" at the run boundary. |
| FR-009 | Parent refs/artifacts/checkpoint projection in `run.py`; delegated child unit coverage. | VERIFIED | Parent-owned child/runtime evidence is represented when runtime outputs include refs. |
| FR-010 | `spec.md` preserves MM-646 and Jira preset brief; this report preserves MM-646. | VERIFIED | Pull request and commit metadata are outside this verify step. |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|
| SCN-001 | Prepared refs helper and step ledger query exposure. | VERIFIED | Covered by focused unit test. |
| SCN-002 | Step result evidence projection. | VERIFIED | Output refs become semantic artifacts. |
| SCN-003 | Runtime checkpoint ref extraction. | PARTIAL | Returned checkpoint refs are recorded; absent checkpoint refs are replaced with synthesized refs. |
| SCN-004 | Repeated checkpoint recording test. | PARTIAL | Idempotency is tested for returned runtime checkpoint refs; not for actual durable checkpoint writes. |
| SCN-005 | Compact model validation and inline checkpoint rejection. | VERIFIED | Covered by focused unit/integration-boundary tests. |
| SCN-006 | Missing evidence ineligibility helper. | PARTIAL | Missing output refs are covered. Missing checkpoint evidence with output refs is not covered and is currently bypassed by synthesized checkpoint refs. |
| SCN-007 | Parent-owned delegated evidence projection. | VERIFIED | Covered by unit tests for parent-owned child evidence when child refs are present. |

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-001 | Parent `MoonMind.Run` owns ledger query and evidence projection. | PARTIAL | Parent ownership exists, but durable checkpoint evidence is not guaranteed for synthesized refs. |
| DESIGN-REQ-002 | Prepared refs captured from task payload and exposed in ledger snapshot. | VERIFIED | `build_resume_prepared_artifact_refs` and `_capture_prepared_input_refs`. |
| DESIGN-REQ-003 | Semantic output refs projected into step artifacts. | VERIFIED | `run.py::_record_step_result_evidence`. |
| DESIGN-REQ-004 | Checkpoint refs and no-inline payload validation. | PARTIAL | Ref-only behavior is present, but synthesized checkpoint refs are not durable checkpoint writes. |
| DESIGN-REQ-005 | Ineligible marker for missing recoverable output/checkpoint evidence. | PARTIAL | The helper can produce `missing_state_checkpoint`, but the workflow success path can avoid that state by synthesizing a checkpoint ref. |
| DESIGN-REQ-006 | Parent task run as canonical source. | VERIFIED | Parent ledger remains source of truth for projected child/runtime refs. |
| DESIGN-REQ-007 | Resume offered only from durable refs/checkpoints. | PARTIAL | Resume validation requires checkpoint payload evidence, but source ledger eligibility can overstate recoverability. |
| CC-IX Resilient by Default | Focused workflow/helper tests and idempotency logic. | PARTIAL | Retry-safe identity is present, but durable checkpoint write evidence is incomplete. |
| CC-XI Spec-Driven Development | Spec, plan, tasks, verification artifacts exist and trace MM-646. | VERIFIED | Current verification is this final gate. |

## Original Request Alignment

The implementation aligns with the request to preserve prepared refs, semantic output refs, parent-owned evidence, and compact checkpoint payloads. It does not fully satisfy the request that workspace/branch/commit checkpoints be recorded around mutating step boundaries and that steps without required checkpoint evidence be marked Resume-ineligible, because the current workflow can fabricate a deterministic checkpoint ref when no checkpoint was returned by the runtime.

## Gaps

- `MoonMind.Run` synthesizes a checkpoint ref in `_record_step_checkpoint_evidence()` when neither an explicit `state_checkpoint_ref` nor a runtime-provided checkpoint ref exists. Because the success path calls this helper after every accepted step, a completed step with semantic output refs but no real checkpoint can be marked `resumePreservation.eligible == true`.
- No test covers the acceptance-critical case: completed step has recoverable output refs but no runtime/workspace checkpoint ref, and therefore must be marked `missing_state_checkpoint`.
- `./tools/test_integration.sh` could not run in this managed environment due Docker daemon administrative restrictions, so full hermetic integration evidence is unavailable here.

## Remaining Work

1. Change the `MoonMind.Run` success path so it does not synthesize checkpoint refs as recoverable evidence when no runtime/workspace checkpoint was produced. A deterministic key may be used only for an actual persisted checkpoint write, not as proof by itself.
2. Add unit coverage in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` for a completed step with `outputPrimary`/semantic output refs and no checkpoint ref, asserting `resumePreservation.reason == "missing_state_checkpoint"`.
3. Add integration-boundary coverage for the same missing-checkpoint-with-output case under the parent run or step ledger boundary.
4. Rerun focused unit, focused integration-boundary, full unit, and `./tools/test_integration.sh` in an environment where Docker compose builds are allowed.

## Decision

The final verdict is `ADDITIONAL_WORK_NEEDED`. Use this report as the mandatory remediation input for the next implementation step before PR creation.

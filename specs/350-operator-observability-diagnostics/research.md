# Research: Operator Observability Diagnostics

## FR-001 Target Attachment Grouping

Decision: implemented_verified.
Evidence: `api_service/api/routers/executions.py` builds target diagnostics from objective and step attachment payloads; `tests/unit/api/routers/test_executions.py::test_describe_execution_exposes_target_attachment_and_recovery_diagnostics`; `frontend/src/entrypoints/task-detail.test.tsx` target diagnostics rendering test.
Rationale: Current backend and frontend tests prove objective and step attachment metadata reaches task detail.
Alternatives considered: Rebuilding diagnostics from raw workflow history was rejected because the current compact projection already exists.
Test implications: none beyond final verify unless touched.

## FR-002 Empty Target Distinction

Decision: partial; add tests first with implementation contingency.
Evidence: `frontend/src/entrypoints/task-detail.tsx` displays "No attachments recorded for this target" when a target appears, but `api_service/api/routers/executions.py` only emits some empty targets when a diagnostics block is present.
Rationale: Operators need to distinguish populated and empty targets consistently. Current behavior is useful but not fully proven for all target combinations.
Alternatives considered: Treat empty targets as out of scope; rejected because FR-002 explicitly requires distinction.
Test implications: backend unit and frontend unit tests should cover one populated target and one empty target.

## FR-003 and FR-004 Diagnostic Refs

Decision: FR-003 implemented_verified; FR-004 implemented_unverified.
Evidence: Backend/frontend tests assert manifest refs. `tests/integration/schemas/test_execution_target_diagnostics_boundary.py` preserves `generated_context`, but execution-detail route and UI tests do not explicitly verify generated context refs.
Rationale: The existing ref model is generic enough for generated context, but route/UI proof is thin.
Alternatives considered: Add a new ref type model; rejected because `refKind` already supports bounded labels without schema churn.
Test implications: add route and frontend tests for `refKind: generated_context`.

## FR-005 and FR-006 Attachment Failure Target and Phase

Decision: implemented_verified.
Evidence: `_normalize_target_failures()` and `_normalize_attachment_failure_phase()` in `api_service/api/routers/executions.py`; backend test covers materialization and degraded fallback; frontend renders failure phase and evidence ref.
Rationale: Bounded phase normalization and target nesting are already covered.
Alternatives considered: Expose raw provider phases; rejected because operators need bounded phases.
Test implications: none beyond final verify unless phase normalization changes.

## FR-007 Step-Aware Attachment Context

Decision: implemented_verified.
Evidence: Backend target diagnostics include step `stepId` and label; frontend renders step target cards; tests cover step target diagnostics.
Rationale: Step context is separated from objective context in the current model.
Alternatives considered: Collapse all attachments into one list; rejected by source design and existing implementation.
Test implications: final verify.

## FR-008 and FR-009 Resume Provenance

Decision: implemented_verified.
Evidence: `_preserved_steps_from_resume_source()` in `api_service/api/routers/executions.py`; `moonmind/workflows/temporal/service.py` creates `resumeSource`; backend/frontend tests assert source run and preserved steps.
Rationale: Existing projection already exposes resumed execution provenance and preserved prior steps.
Alternatives considered: Link only to related runs; rejected because the story requires preserved-step provenance in task detail.
Test implications: final verify.

## FR-010 Raw-History Avoidance

Decision: partial.
Evidence: Current task detail renders compact target diagnostics and raw diagnostics remain separately available. Generated context and complete failed Resume phase coverage need stronger proof.
Rationale: The product direction is correct, but success depends on closing the remaining coverage gaps.
Alternatives considered: Require operators to open raw diagnostics for edge cases; rejected by the Jira brief.
Test implications: unit, frontend, and integration verification after missing pieces are covered.

## FR-011 and FR-012 Compatibility Semantic Non-Drift

Decision: implemented_unverified.
Evidence: Backend accepts camel/snake field aliases and multiple attachment field names; `tests/integration/schemas/test_execution_target_diagnostics_boundary.py` covers alias serialization. Direct no-retarget/no-merge tests are insufficient.
Rationale: The code likely preserves target meaning, but this is a compatibility-sensitive rule and needs explicit regression coverage.
Alternatives considered: Remove alias tolerance; rejected for this story because the current API already accepts compact historical shapes and the spec requires semantic preservation.
Test implications: backend unit plus schema/integration tests for objective and step alias-shaped inputs.

## FR-013 Failed Resume Phase Coverage

Decision: partial; add tests first with implementation contingency.
Evidence: Frontend schema supports `checkpoint_validation`, `workspace_restoration`, `preserved_output_injection`, and `failed_step_execution`; backend maps several disabled reasons to the first three phases but does not visibly derive `failed_step_execution`.
Rationale: The bounded phase set is present, but one required phase lacks route-level proof and likely needs backend support.
Alternatives considered: Treat failed-step execution as raw task failure status; rejected because the spec requires the phase to be visible in Resume diagnostics.
Test implications: backend unit, frontend unit, and integration-boundary coverage.

## FR-014 Traceability

Decision: implemented_verified.
Evidence: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and contract artifacts preserve `MM-651`.
Rationale: Traceability is already present and must be maintained through later artifacts.
Alternatives considered: Store traceability only in Jira; rejected because MoonSpec verification compares local artifacts.
Test implications: final verify.

## Test Strategy

Decision: Use test-first changes where implementation gaps remain.
Evidence: Repo instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration CI. Existing frontend tests use Vitest through `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`.
Rationale: The story spans backend projection, Pydantic/OpenAPI schema, and React task detail rendering, so unit and integration strategies must be separate.
Alternatives considered: Rely only on frontend tests; rejected because target semantics are produced at backend/schema boundaries.
Test implications: focused unit tests first, focused frontend tests second, integration schema/route boundary tests third, then full required runners.

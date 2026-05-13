# Research: Target-Aware Step Execution Scope

## FR-001 / FR-002 / FR-004 / FR-008 / DESIGN-REQ-001

Decision: Treat step prepared-context selection as already implemented and verified for the core current-step-only behavior.
Evidence: `moonmind/workflows/tasks/prepared_context.py` defines `PreparedInputManifest`, `StepPreparedContext`, and `select_step_prepared_context()`. `tests/unit/workflows/tasks/test_prepared_context.py` verifies objective and current-step refs are included while unrelated step refs are absent. `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py` verifies parent request assembly excludes `report-notes`/other-step refs.
Rationale: The implementation matches explicit target metadata rather than filename, path position, step order, or instruction text. Existing tests cover reorder/text edit stability and unrelated-step exclusion.
Alternatives considered: Rewriting context selection in workflow code. Rejected because the task helper already centralizes the contract and is easier to test deterministically.
Test implications: Preserve existing unit coverage and rerun focused unit/integration tests during implementation and final verification.

## FR-003 / SCN-002 / SC-003

Decision: Classify same-workspace materialization as implemented_unverified.
Evidence: `PreparedInputEntry.workspace_path` records target-specific paths, and `tests/unit/workflows/tasks/test_prepared_context.py` checks stable objective and step workspace paths. Current integration coverage verifies unrelated step refs are excluded from workflow request payloads.
Rationale: The behavior is strongly implied, but MM-649 specifically calls out no leakage even when preparation materialized all attachments in one workspace. A focused verification test should make that scenario explicit.
Alternatives considered: Mark implemented_verified based on existing path and request filtering tests. Rejected because the requirement names a concrete failure mode that deserves direct evidence.
Test implications: Add unit and integration verification for a manifest/request with multiple prepared attachments available in the same task preparation set; patch only if the test exposes leakage.

## FR-005 / SCN-003

Decision: Treat AgentRun represented-step request scoping as implemented_verified for unit-level request construction.
Evidence: `moonmind/workflows/temporal/workflows/run.py` builds an `AgentExecutionRequest` containing selected prepared context before invoking `"MoonMind.AgentRun"`. `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py::test_child_agent_run_request_receives_only_represented_step_context` verifies a request for `write-report` includes `report-notes` and excludes `collect-notes`.
Rationale: The parent request object is the child workflow input shape used by `workflow.execute_child_workflow("MoonMind.AgentRun", request, ...)`.
Alternatives considered: Add a new child workflow schema. Rejected because `AgentExecutionRequest` is already the boundary contract.
Test implications: Keep existing unit coverage and add integration/boundary evidence for the child workflow input path if tasks require stronger SC-002 proof.

## FR-006 / DESIGN-REQ-002 Parent Authority

Decision: Classify parent target-binding authority as implemented_unverified.
Evidence: Parent `MoonMind.Run` builds prepared context from task payload and injects `metadata.moonmind.preparedContext` before child dispatch. `MoonMind.AgentRun` preserves metadata and enriches results without building a new target-binding model.
Rationale: The design is correct, but the tests should explicitly assert that the parent-provided metadata is the authority and no child-side input can broaden it.
Alternatives considered: Introduce a separate authority marker model. Rejected for planning because existing `moonmind.preparedContext` metadata can carry the authority semantics if verified.
Test implications: Add a focused unit or integration boundary test for parent-owned target binding metadata. Patch metadata naming or child enrichment only if the test fails.

## FR-007 / SCN-004 / SC-004 Diagnostics

Decision: Classify diagnostic non-redefinition as implemented_unverified.
Evidence: `moonmind/workflows/temporal/workflows/agent_run.py` preserves `metadata["moonmind"]` and adds step ledger/report output context, but current tests do not directly assert that child logs/diagnostics cannot redefine target binding semantics.
Rationale: This is an operator-evidence requirement, not only a request filtering requirement. It needs explicit proof that diagnostics report consumed context or parent-provided refs without introducing alternate target rules.
Alternatives considered: Treat absence of child-side target parsing as sufficient. Rejected because the spec asks for logs/diagnostics behavior.
Test implications: Add a unit test around AgentRun result enrichment or request metadata preservation. If diagnostics can overwrite target semantics, patch child result metadata to preserve parent authority and reject or ignore child-side redefinitions.

## FR-009 / SC-005 Traceability

Decision: Traceability is missing until downstream tasks, implementation notes, verification output, commit text, and PR metadata exist.
Evidence: `specs/348-target-aware-step-scope/spec.md` preserves MM-649 and the canonical Jira preset brief. No tasks or verification artifacts exist yet.
Rationale: Planning can preserve traceability, but completion depends on later MoonSpec stages.
Alternatives considered: Mark verified because spec preservation exists. Rejected because FR-009 includes later artifacts not created in this step.
Test implications: `tasks.md` and final verification must include traceability checks for MM-649 and the canonical brief.

## Test Tooling

Decision: Use `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration verification. Use focused pytest/Vitest commands only for iteration when needed.
Evidence: Repo instructions require `./tools/test_unit.sh` and `./tools/test_integration.sh`; existing tests are pytest-based Python workflow tests.
Rationale: The story touches workflow and activity boundaries, so both unit and hermetic integration checks are required.
Alternatives considered: Provider verification tests. Rejected because this story does not require live external provider credentials.
Test implications: Tasks should add or update pytest tests first, confirm failures where behavior is missing, then run focused tests and final suite scripts.

## Constitution And Compatibility Constraints

Decision: Treat workflow/activity payload shapes as compatibility-sensitive and avoid adding compatibility aliases.
Evidence: Constitution principle IX requires boundary regression coverage for workflow/activity contracts; principle XIII requires deleting superseded internal contracts rather than preserving aliases.
Rationale: Prepared-context metadata crosses the parent-child workflow boundary and may be present in in-flight histories.
Alternatives considered: Hidden fallback translation for alternate target fields. Rejected because fallback transforms could hide target-binding errors.
Test implications: Add boundary-level regression coverage for request metadata shape and degraded/invalid prepared context inputs.

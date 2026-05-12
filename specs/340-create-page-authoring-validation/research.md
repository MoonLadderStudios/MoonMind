# Research: Create Page Authoring Validation

## FR-001 / DESIGN-REQ-001 Task-First Authoring

Decision: implemented_unverified. Preserve the existing task-first Create page flow while verifying the relocated controls still read as task authoring rather than workflow internals.
Evidence: `frontend/src/entrypoints/task-create.tsx`; `docs/Tasks/TaskArchitecture.md` section 3.1.
Rationale: The page already presents task creation, instructions, steps, runtime, presets, and publish controls in one flow, but MM-641 changes the placement of repository/branch/publish controls and needs explicit coverage.
Alternatives considered: Treating the move as purely cosmetic was rejected because placement is a user-visible task authoring contract.
Test implications: unit.

## FR-002 / FR-007 / SC-004 Coherent Draft and Normalized Submission

Decision: partial. Existing frontend and backend code preserve many draft parts, but MM-641 needs an integrated proof that text, presets, Jira imports, attachments, dependencies, runtime, repository, branch, and publish mode survive one valid submission.
Evidence: `frontend/src/entrypoints/task-create.tsx`; `frontend/src/entrypoints/task-create.test.tsx`; `api_service/api/routers/executions.py`; `tests/integration/temporal/test_task_shaped_submission_normalization.py`.
Rationale: Existing tests are broad but spread across behaviors. The planned UI move could disturb selectors, disabled states, default branch resolution, or submit shaping.
Alternatives considered: Relying on existing isolated tests was rejected because the spec requires coherent round-trip behavior across the complete draft.
Test implications: unit + integration.

## FR-003 / FR-006 / SC-002 Validation Before Submission

Decision: partial. Validation logic already rejects invalid repository, runtime, publish, branch, attachment, and dependency states, but representative post-relocation scenario coverage is required.
Evidence: `frontend/src/entrypoints/task-create.tsx` validation paths; `frontend/src/entrypoints/task-create.test.tsx` repository, branch, publish, attachment, and dependency tests.
Rationale: Moving controls into the Steps card must not weaken blocking behavior or error copy. Backend validation remains the final boundary for task-shaped payloads.
Alternatives considered: Backend-only validation was rejected because the spec requires Create page rejection before submission.
Test implications: unit + integration.

## FR-004 / SC-001 / DESIGN-REQ-003 Steps-Card Placement

Decision: partial, requiring implementation. Repository, Branch, and Publish Mode are currently grouped together in the floating submit controls, and existing tests assert they are outside the Steps section.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders controls in `.queue-floating-bar`; `frontend/src/entrypoints/task-create.test.tsx` asserts `repoInput.closest('[data-canonical-create-section="Steps"]')` is null.
Rationale: MM-641 requires these controls inside the Steps card. This is the primary gap.
Alternatives considered: Keeping the floating submit rail as-is was rejected because it conflicts with the spec and source design.
Test implications: unit.

## FR-005 / DESIGN-REQ-005 Attachment Target Binding

Decision: implemented_unverified. Existing Create page and backend paths preserve objective and step attachment refs; regression coverage should ensure relocation does not affect attachment validation or payload shape.
Evidence: `frontend/src/entrypoints/task-create.tsx` attachment target validation and `inputAttachments` shaping; `frontend/src/entrypoints/task-create.test.tsx` attachment submission tests; `api_service/api/routers/executions.py` attachment normalization.
Rationale: The planned layout change does not intentionally touch attachments, but task submission is shared and must remain stable.
Alternatives considered: No new attachment test was rejected because the spec includes attachment-policy validation and target preservation.
Test implications: unit.

## FR-008 / SC-003 / DESIGN-REQ-004 Canonical Branch Semantics

Decision: implemented_verified. Current frontend, backend, and integration tests already prove new submissions use `task.git.branch` and reject or remove legacy `targetBranch` forms.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` branch submission test; `api_service/api/routers/executions.py` rejects `payload.task.git.targetBranch`; `tests/integration/temporal/test_task_shaped_submission_normalization.py`; `tests/integration/api/test_task_contract_normalization.py`.
Rationale: This invariant is already covered and should be preserved during UI relocation.
Alternatives considered: Reworking backend branch semantics was rejected because it would create unnecessary contract churn.
Test implications: none beyond final verify unless relocation breaks frontend tests.

## FR-009 / FR-010 Publish Mode Semantics

Decision: partial. Publish Mode already submits separately from branch and supports none/branch/pr semantics, but the control must move without changing submission behavior.
Evidence: `frontend/src/entrypoints/task-create.tsx` `publish.mode` shaping; existing `task-create.test.tsx` publish-mode tests; `docs/Tasks/TaskArchitecture.md` branch/publish invariants.
Rationale: Visual placement is explicitly not a semantic change, so tests must compare payload behavior before and after relocation.
Alternatives considered: Moving publish behavior into branch-specific code was rejected because Publish Mode remains independent submission data.
Test implications: unit + integration.

## FR-011 Authoring Provenance

Decision: implemented_unverified. Authored presets, applied step templates, Jira provenance, and step source metadata are already modeled and tested, but MM-641 needs combined preservation evidence.
Evidence: `frontend/src/entrypoints/task-create.tsx` `authoredPresetsFromAppliedTemplates`; `api_service/api/routers/executions.py` preserves `authoredPresets` and `appliedStepTemplates`; related frontend tests around Jira Orchestrate and schema presets.
Rationale: The requirement is not new storage; it is preservation through draft normalization and submission.
Alternatives considered: Treating provenance as out of scope was rejected because it is explicit in the Jira preset brief.
Test implications: unit.

## FR-012 / SC-005 Traceability

Decision: implemented_verified for specify and plan; remains a final verification requirement for later stages.
Evidence: `specs/340-create-page-authoring-validation/spec.md` preserves `MM-641` and the original preset brief; this plan preserves requirement status and source mappings.
Rationale: Final verification needs a stable source trail.
Alternatives considered: External artifact-only traceability was rejected because the spec itself must preserve the source request.
Test implications: final verify.

## Test Tooling

Decision: use Vitest/Testing Library for frontend unit coverage, pytest unit/API tests where backend shaping changes, and hermetic integration tests for execution-visible task payload boundaries.
Evidence: `frontend/src/entrypoints/task-create.test.tsx`; `tests/unit/api/routers/test_executions.py`; `tests/integration/temporal/test_task_shaped_submission_normalization.py`; repo instructions for `./tools/test_unit.sh` and `./tools/test_integration.sh`.
Rationale: The story spans Create page behavior and execution-facing payload contracts.
Alternatives considered: Browser-only/manual verification was rejected because the project requires repeatable unit and integration evidence.
Test implications: both unit and integration.

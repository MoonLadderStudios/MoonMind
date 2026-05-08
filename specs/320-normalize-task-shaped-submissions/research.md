# Research: Normalize Task-Shaped Submissions

## Setup Script

Decision: Proceed manually with `specs/320-normalize-task-shaped-submissions` as the active feature directory.
Evidence: `.specify/feature.json` points at `specs/320-normalize-task-shaped-submissions`; `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch name is `run-jira-orchestrate-for-mm-627-normaliz-0f1ed32a`, not a numeric feature branch.
Rationale: The feature directory and spec already exist and pass the specify gate; the script failure is a branch naming guard, not a missing artifact.
Alternatives considered: Renaming or switching branches was rejected because this managed step is only authorized to plan.
Test implications: None.

## FR-001 / DESIGN-REQ-001 Task-Shaped Intent

Decision: Implemented unverified.
Evidence: `frontend/src/entrypoints/task-create.tsx` builds `payload.task`; `api_service/api/routers/executions.py` creates `initial_parameters["task"]` from normalized task-shaped input.
Rationale: The path exists, but MM-627 requires create, edit, and rerun preservation as one story-level contract.
Alternatives considered: Marking implemented verified was rejected because existing tests are field-specific and do not prove the whole cross-flow contract.
Test implications: Unit and integration.

## FR-002 / FR-003 / DESIGN-REQ-003 Explicit Attachment Targets

Decision: Partial.
Evidence: `task-create.test.tsx` covers objective attachments, step attachments, same-name step attachments, and step reorder behavior; `api_service/api/routers/executions.py` normalizes objective `inputAttachments` and step `steps[n].inputAttachments`.
Rationale: Create-time target separation is well covered. Edit/rerun, prepare, prompt composition, and detail rendering need one boundary-oriented verification path for the MM-627 contract.
Alternatives considered: Treating objective/step arrays as fully verified was rejected because the spec explicitly spans create, edit, rerun, prepare, prompt composition, and detail rendering.
Test implications: Unit and integration.

## FR-004 / FR-010 / DESIGN-REQ-006 Validation

Decision: Partial.
Evidence: `tests/unit/api/routers/test_executions.py` covers disabled attachment policy, unknown fields, unsupported runtime with attachments, attachment limits, and dependency normalization; `api_service/api/routers/executions.py` validates attachment artifact metadata and runtime values.
Rationale: Several invalid classes already fail explicitly, but repository validation and target-binding edge cases need focused proof.
Alternatives considered: Adding only frontend validation was rejected because the API boundary must remain authoritative.
Test implications: Unit and integration.

## FR-005 / DESIGN-REQ-008 Field Preservation

Decision: Partial.
Evidence: `frontend/src/entrypoints/task-create.tsx` includes instructions, runtime, publish, git branch, steps, dependencies, and applied templates; `api_service/api/routers/executions.py` preserves many but not all task fields in `normalized_task_for_planner`.
Rationale: Canonical preservation is not complete until Jira provenance and preset metadata survive the backend normalization boundary.
Alternatives considered: Relying on frontend-only payload evidence was rejected because execution receives the backend-normalized task payload.
Test implications: Unit and integration.

## FR-006 / SC-003 Canonical Branch Semantics

Decision: Partial.
Evidence: `task-create.test.tsx` verifies create-page submissions use `task.git.branch` and do not include `targetBranch`; `api_service/api/routers/executions.py` still carries `git.targetBranch` and top-level `targetBranch` into normalized task payloads when supplied.
Rationale: New browser submissions are aligned, but the API normalization boundary still permits legacy branch output for task-shaped submissions.
Alternatives considered: Keeping compatibility aliases was rejected because the spec and repository compatibility policy require removing superseded internal semantics instead of hiding translation layers.
Test implications: Unit and integration.

## FR-007 / FR-008 Preset Provenance

Decision: Partial.
Evidence: Frontend tests cover applied template payloads, template step identity preservation, detachment, and Jira attachment import provenance; API serialization can derive primary skill from `appliedStepTemplates` when present in stored parameters.
Rationale: Backend create normalization does not clearly preserve authored preset bindings and applied template metadata into the canonical task payload, so downstream execution may not receive the full authored provenance.
Alternatives considered: Treating snapshot shape detection as sufficient was rejected because the spec requires the normalized task output itself to preserve preset metadata.
Test implications: Unit and integration.

## FR-009 / SC-005 No Hidden Retargeting

Decision: Partial.
Evidence: `task-create.test.tsx` covers reorder preservation and blocks ambiguous preset-step attachment retargeting during submit-time expansion.
Rationale: The UI guard exists, but backend/workflow-boundary tests are needed so retargeting cannot bypass Mission Control client behavior.
Alternatives considered: UI-only coverage was rejected because API submission is a public control-plane boundary.
Test implications: Unit and integration.

## FR-011 Binary Content Stays Structured

Decision: Implemented verified.
Evidence: `task-create.test.tsx` asserts instructions are not rewritten with attachment text and submitted attachments are structured refs; API attachment normalization accepts refs and validates metadata.
Rationale: Current frontend and backend behavior align with the invariant that binary content is not embedded in instruction text.
Alternatives considered: Additional implementation was rejected because existing evidence is direct and sufficient.
Test implications: None beyond final verification unless adjacent changes disturb this behavior.

## FR-012 / SC-006 Traceability

Decision: Implemented verified.
Evidence: `specs/320-normalize-task-shaped-submissions/spec.md` preserves MM-627, the original Jira preset brief, and all in-scope design requirement IDs; this plan preserves the same traceability.
Rationale: Traceability is already present in the MoonSpec artifacts.
Alternatives considered: None.
Test implications: Final traceability check.

## Test Tooling

Decision: Use repo-standard focused unit iteration plus required final test runners.
Evidence: AGENTS.md requires `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration CI; frontend targeted tests can run through `./tools/test_unit.sh --ui-args <path>` after JS deps are prepared.
Rationale: The story crosses frontend, API, and execution boundaries, so unit and integration strategies must be separate.
Alternatives considered: Running nested Docker unit tests was rejected by managed-agent test guidance.
Test implications: Unit tests first, integration boundary second, full unit suite before final verification.

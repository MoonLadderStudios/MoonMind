# Research: Normalize Step Type API and Executable Submission Payloads

## Input Classification

Decision: MM-566 is a single-story runtime feature request.
Evidence: The preserved Jira brief identifies one API consumer story with one cohesive boundary: draft payloads may contain Tool, Skill, and Preset, while executable submissions normally contain Tool and Skill only.
Rationale: The source design sections cover a single Step Type payload normalization path rather than multiple independent product stories.
Alternatives considered: Reusing `specs/279-submit-discriminated-executable-payloads` was rejected because that spec preserves MM-559, not MM-566.
Test implications: The new feature must preserve MM-566 in artifacts and final verification even when implementation evidence comes from MM-559-era code.

## Executable Submission Boundary

Decision: Treat FR-002, FR-003, FR-005, DESIGN-REQ-014, SCN-004, SCN-005, and SC-002 as implemented and verified by existing task contract tests.
Evidence: `moonmind/workflows/tasks/task_contract.py` validates explicit executable step types and rejects Preset, Activity, and conflicting payloads. `tests/unit/workflows/tasks/test_task_contract.py` covers accepted Tool/Skill payloads, rejected non-executable Step Types, and mixed payload errors.
Rationale: The acceptance behavior is already enforced at the executable payload boundary and should not be reimplemented for MM-566.
Alternatives considered: Duplicating backend validators was rejected because it would increase contract surface without changing behavior.
Test implications: Rerun the focused backend task contract tests and full unit suite for final evidence.

## Draft Reconstruction Gap

Decision: Implement explicit Step Type preservation in `frontend/src/lib/temporalTaskEditing.ts`.
Evidence: `TemporalSubmissionDraft.steps` currently stores `skillId` and `skillArgs` but not `stepType`, `tool`, or `preset`; `draftStepFrom` derives all non-tool steps as Skill-like editable rows.
Rationale: MM-566 requires draft APIs to represent ToolStep, SkillStep, and PresetStep explicitly. Executable submission validation alone does not satisfy draft-oriented Preset representation.
Alternatives considered: Modeling Preset only in Create-page local state was rejected because Temporal edit/rerun reconstruction is the API consumer boundary for stored drafts.
Test implications: Add frontend unit coverage for explicit Tool, Skill, Preset draft reconstruction and legacy Skill readability.

## Legacy Readability

Decision: Preserve existing legacy inference while adding explicit discriminators for new output.
Evidence: Existing tests reconstruct older steps from `tool`, `skill`, and template fields; the source design allows migration phases and legacy readers where necessary.
Rationale: Removing legacy readability would break edit/rerun of already-stored task inputs and exceed MM-566 scope.
Alternatives considered: Rejecting legacy steps during draft reconstruction was rejected because the Jira brief explicitly allows migration phases.
Test implications: Add assertions that legacy Skill-shaped steps still reconstruct as editable Skill steps.

## Documentation Convergence

Decision: Fix the duplicated runtime migration bullet in `docs/Steps/StepTypes.md` and verify the doc keeps Step Type terminology as the primary discriminator.
Evidence: Section 14 repeats "Compile executable steps into the canonical plan format" twice.
Rationale: The duplicate is minor but contradicts the "new API outputs and docs converge" acceptance criterion because the migration guidance is visibly inconsistent.
Alternatives considered: Leaving docs untouched was rejected because MM-566 explicitly includes docs convergence.
Test implications: Use targeted text inspection plus final review.

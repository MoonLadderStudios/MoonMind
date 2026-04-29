# Research: Preview and Apply Preset Steps Into Executable Steps

## Classification

Decision: MM-565 is a single-story runtime feature request.  
Evidence: `artifacts/moonspec-inputs/MM-565-canonical-moonspec-input.md` contains one actor, one authoring workflow, and one independent preview/apply validation path.  
Rationale: The story is independently testable through the Create page step editor and submission payload.  
Alternatives considered: Treating `docs/Steps/StepTypes.md` as a broad design was rejected because the Jira brief selects specific Preset preview/apply sections and one story.  
Test implications: Use focused Create page frontend tests as unit and integration boundary evidence.

## Existing Artifact Reuse

Decision: Do not reuse `specs/278-preview-apply-preset-steps` as the active MM-565 spec because it preserves Jira source `MM-558`.  
Evidence: `specs/278-preview-apply-preset-steps/spec.md` explicitly records `MM-558`; no existing spec directory preserves `MM-565`.  
Rationale: The issue key must be preserved in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.  
Alternatives considered: Updating the MM-558 spec in place was rejected because it would erase earlier source traceability.  
Test implications: Create MM-565 artifacts and verify against existing code/test evidence.

## FR-001, FR-010, DESIGN-REQ-007

Decision: Implemented and verified by existing Create page behavior.  
Evidence: `frontend/src/entrypoints/task-create.tsx` renders the per-step Preset selector and preview/apply controls; `frontend/src/entrypoints/task-create.test.tsx` includes "previews and applies a step preset from the step editor without using Task Presets".  
Rationale: Preset use is available in the step editor and does not require the management section.  
Alternatives considered: Adding a new Presets management flow was rejected as out of scope.  
Test implications: Rerun focused Create page tests.

## FR-002, FR-003, FR-004, FR-005, DESIGN-REQ-010, DESIGN-REQ-017

Decision: Implemented and verified by existing preview state and tests.  
Evidence: `task-create.tsx` has `handlePreviewStepPreset`, preset preview state, generated step list rendering, and warning/error rendering; tests cover preview without draft mutation and failed expansion.  
Rationale: The existing implementation calls the expansion source before mutation and displays results before apply.  
Alternatives considered: Adding backend-specific tests was rejected because this story does not change backend expansion contracts.  
Test implications: Rerun focused Create page tests.

## FR-006, FR-007, FR-008, FR-009, DESIGN-REQ-011

Decision: Implemented and verified by existing apply/submission behavior.  
Evidence: Tests cover applying preview to replace the Preset step, editing generated steps, submitting preset-generated Tool bindings, and blocking unresolved Preset submission.  
Rationale: The story's executable-submission risk is covered at the UI payload boundary.  
Alternatives considered: Adding a new runtime plan test was rejected because unresolved Preset steps are blocked before executable submission.  
Test implications: Rerun focused Create page tests.

## FR-011 and SC-006

Decision: Partially verified by existing reapply/stale preset tests; no code change planned unless verification exposes a regression.  
Evidence: Existing tests reference "Preset instructions changed. Reapply the preset to regenerate preset-derived steps." and reapply behavior near `frontend/src/entrypoints/task-create.test.tsx`.  
Rationale: MM-565 requires explicit, previewed updates to newer versions. Existing behavior has explicit reapply messaging; version-specific update preview may remain a residual risk if not isolated by a current test.  
Alternatives considered: Expanding scope into full preset version management was rejected because the Jira brief says updates must be explicit and previewed, not that catalog management must be rebuilt.  
Test implications: Rerun focused Create page tests and record any residual gap conservatively in verification.

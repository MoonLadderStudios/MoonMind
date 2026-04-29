# Research: Preview and Apply Preset Steps

## FR-001 / DESIGN-REQ-007

Decision: Existing Step Type `Preset` controls are implementation evidence but require MM-558-specific verification.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders a Step Type select with Tool, Skill, and Preset; existing tests around Step Type selection live in `frontend/src/entrypoints/task-create.test.tsx`.
Rationale: The core authoring surface from MM-556 is present, so MM-558 should not rebuild it.
Alternatives considered: Replacing the Task Presets area entirely; rejected because this story only requires preset use inside step authoring and existing management/global apply behavior may remain temporarily.
Test implications: Frontend unit tests.

## FR-004 / FR-005 / DESIGN-REQ-009

Decision: Preview-before-apply is missing and must be added.
Evidence: `handleApplyStepPreset` calls `applyPresetToDraft` directly; `applyPresetToDraft` immediately maps expanded steps and updates draft state. No preview state or generated-step list is rendered before apply.
Rationale: The acceptance criteria require a visible generated-step preview before apply, not just selecting and applying a preset.
Alternatives considered: Treat existing direct apply as preview because it uses the expand endpoint; rejected because it mutates the draft immediately.
Test implications: Red-first frontend tests for preview list, warnings, no draft mutation before apply, and apply from preview.

## FR-002 / FR-003 / FR-009 / DESIGN-REQ-017

Decision: Reuse existing task-template detail and expand API for validation; surface failures and warnings at preview time.
Evidence: `loadPresetDetail`, `resolveTemplateInputs`, and `applyPresetToDraft` already prepare inputs and call the expand endpoint with `enforceStepLimit`. Backend preset include validation exists in `api_service/services/task_templates/catalog.py`.
Rationale: Preview can use the same deterministic expansion path as apply, avoiding a second expansion semantics path.
Alternatives considered: Client-side expansion from template details; rejected because backend expansion is authoritative and already validates includes and limits.
Test implications: Frontend unit tests mocking expand success, warnings, and failure.

## FR-006 / FR-007

Decision: Applying a previewed per-step preset should replace the selected temporary Preset step with the generated steps, then those steps should remain editable through existing `StepState` controls.
Evidence: Existing global apply appends or replaces an empty default draft; existing per-step apply calls the same append-oriented helper, so it does not specifically replace the selected Preset step.
Rationale: Source design says applying a preset replaces the temporary Preset step.
Alternatives considered: Continue appending generated steps after the Preset step; rejected because it leaves unresolved authoring placeholders and makes provenance unclear.
Test implications: Frontend unit test for replacing the selected step and editing generated instructions.

## FR-008 / DESIGN-REQ-010

Decision: Preserve existing source/provenance data and expose available origin/warning information; do not invent unsupported detach/compare/update actions.
Evidence: Expanded steps can carry source metadata and applied template state exists, but current per-step UI has only status text.
Rationale: The spec says supported provenance actions are conditional on source data.
Alternatives considered: Implementing full compare/detach/update workflows now; rejected as larger than the preview/apply slice and not backed by existing source data contracts.
Test implications: Frontend unit tests for visible origin/provenance when source metadata is present.

## FR-010 / DESIGN-REQ-019

Decision: Block task submission when unresolved Preset steps remain.
Evidence: Tool submissions without selected Tool are already blocked; Preset unresolved submission needs equivalent coverage.
Rationale: Presets are authoring-time placeholders by default and linked-preset mode is not present.
Alternatives considered: Auto-apply presets during submit; rejected because the requirement says preview/apply must be explicit and transparent.
Test implications: Frontend unit submission test.

## Test Strategy

Decision: Use frontend unit tests as the primary TDD boundary.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` already mocks task-template catalog/detail/expand and verifies submission payloads.
Rationale: The story is a Create page authoring behavior slice, and backend expansion remains unchanged.
Alternatives considered: Compose-backed integration tests; rejected unless backend contract changes become necessary.
Test implications: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`, then full `./tools/test_unit.sh` when feasible.

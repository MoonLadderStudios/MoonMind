# Research: Step Type Authoring Controls

## FR-001 / DESIGN-REQ-001

Decision: Add a `Step Type` control to each rendered step with Tool, Skill, and Preset choices.
Evidence: `frontend/src/entrypoints/task-create.tsx` currently renders per-step instructions and `Skill (optional)` without a Step Type discriminator.
Rationale: The source design requires one user-facing discriminator per authored step.
Alternatives considered: Keep the separate Task Presets section and Skill field as-is; rejected because the user must learn separate authoring concepts.
Test implications: Frontend unit tests must assert accessible Step Type options.

## FR-002 / DESIGN-REQ-002

Decision: Store selected Step Type in step draft state and use it to render one type-specific configuration area.
Evidence: Existing `StepState` carries skill fields and preset state is page-level, not step-selected.
Rationale: Conditional rendering directly satisfies the selected-type-controls-fields invariant.
Alternatives considered: Infer type from non-empty fields; rejected because it creates hidden state and ambiguous authoring.
Test implications: Tests must switch Tool, Skill, and Preset and assert only relevant controls are visible.

## FR-003

Decision: Keep existing Skill selector and advanced Skill fields, but show and submit them only while Step Type is Skill.
Evidence: Existing tests already validate advanced Skill fields and hidden advanced-field submission.
Rationale: This preserves current Skill behavior while making Skill one Step Type rather than the implicit default.
Alternatives considered: Rename Skill to Tool internally; rejected because Tool and Skill are distinct product concepts.
Test implications: Update existing tests to select Skill when needed and add a regression for switching away from Skill.

## FR-004

Decision: Render the existing preset selection, inputs, and Apply action inside the primary step when Step Type is Preset, and remove the separate canonical Task Presets authoring section.
Evidence: Existing Task Presets section is a separate authoring surface in `task-create.tsx`.
Rationale: Preset use belongs inside step authoring; preset management can remain separate elsewhere.
Alternatives considered: Duplicate preset controls in both places; rejected because it would violate exactly-one authoring control.
Test implications: Canonical Create page section order should omit `Task Presets`; preset tests should scope to the step editor.

## FR-005 / DESIGN-REQ-015

Decision: Use `Step Type` as the discriminator label and Tool, Skill, Preset as option labels. Keep `requiredCapabilities` terminology only in advanced technical fields if necessary, not as the step discriminator.
Evidence: `Skill (optional)` and `Task Presets` currently act as authoring concepts.
Rationale: The source design only forbids internal terms as the Step Type label; capability remains a technical advanced field.
Alternatives considered: Rename all capability fields globally; rejected as broader than MM-556.
Test implications: Assert Step Type label is present and forbidden terms are not option labels.

## FR-006

Decision: Preserve instructions across Step Type changes and ignore/clear hidden Skill fields when the selected Step Type is not Skill.
Evidence: `updateStep` already clears Skill args when advanced options are hidden, but submission still derives primary skill defaults.
Rationale: Instructions are compatible across types; Skill args/capabilities are not compatible when the step is Tool or Preset.
Alternatives considered: Prompt before every Step Type change; rejected because the current scope can safely clear hidden incompatible fields.
Test implications: Test that instructions persist and hidden Skill args/capabilities are not submitted after switching to Tool.

# Feature Specification: Preview and Apply Preset Steps

**Feature Branch**: `278-preview-apply-preset-steps`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-558 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-558` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-558` and local artifact `artifacts/moonspec-inputs/MM-558-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-558` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-558 MoonSpec Orchestration Input

## Source

- Jira issue: MM-558
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preview and apply Preset steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-558-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-558 from MM project
Summary: Preview and apply Preset steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-558 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-558: Preview and apply Preset steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.3 `preset`
- 6.5 Preset picker
- 6.6 Preset preview and apply
- 8.4 Preset validation
- 9. Jira Example
- 12. Preset Management vs Preset Use
- 16. Open Design Decisions

Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-017
- DESIGN-REQ-019

As a task author, I want to configure a Preset step, preview its generated steps, and apply it into executable Tool and Skill steps so reusable workflows stay transparent and editable.

Acceptance Criteria
- Preset use is available inside the step editor and there is no separate Presets section for choosing and applying a preset to the current task.
- Preview lists the generated steps before apply and exposes expansion warnings.
- Apply replaces the temporary Preset step with concrete Tool and/or Skill steps that pass their own validation.
- Undo, show origin, detach provenance, compare, and explicit update actions are available where supported by source data.
- Unresolved Preset steps cannot be submitted unless a future linked-preset mode is explicitly introduced and visibly different.

Requirements
- Preset steps are authoring-time placeholders by default.
- Expansion is deterministic and validated before execution.
- Generated steps are editable like ordinary steps after apply.
- Linked presets remain outside the default behavior.
```

## User Story - Preview and Apply Preset Steps

**Summary**: As a task author, I want to configure a Preset step, preview its generated steps, and apply it into executable Tool and Skill steps so reusable workflows stay transparent and editable.

**Goal**: Task authors can choose a Preset while editing a task step, review the deterministic expansion before accepting it, and replace the temporary Preset step with editable executable steps that retain visible preset provenance.

**Independent Test**: Render the task authoring experience, choose Step Type `Preset`, select and configure an available preset, preview the generated Tool and Skill steps with warnings, apply the preset, and verify the draft contains editable executable steps instead of an unresolved Preset step.

**Acceptance Scenarios**:

1. **Given** a task author is editing a step, **When** they choose Step Type `Preset`, **Then** the step editor lets them select and configure a preset from the same authoring surface used for Tool and Skill steps.
2. **Given** a configured Preset step, **When** the author requests preview, **Then** the system shows the generated step list before application and includes any expansion warnings that affect the draft.
3. **Given** the previewed expansion is valid, **When** the author applies the preset, **Then** the temporary Preset step is replaced by concrete Tool and/or Skill steps that are editable like ordinary steps.
4. **Given** generated steps come from a preset, **When** the author inspects them after application, **Then** the UI exposes preset origin and supports supported provenance actions such as undo, detach, compare, or explicit update when source data is available.
5. **Given** a Preset step remains unresolved, **When** the author submits the task, **Then** submission is blocked unless an explicit future linked-preset mode is available and visibly different from default preset application.
6. **Given** the Presets section is visible elsewhere, **When** the author is choosing a preset for the current task, **Then** preset use happens inside the step editor and the separate Presets section is not required for applying a preset to the draft.

### Edge Cases

- A selected preset is missing, inactive, or not previewable; preview and apply are blocked with visible errors.
- Preset inputs fail the preset input schema; the author sees input-level validation before expansion.
- Expansion succeeds but one or more generated steps fail Tool or Skill validation; apply is blocked and the failing generated steps are identified.
- Expansion returns warnings without hard validation failures; warnings remain visible before apply and after apply where relevant.
- The author cancels or undoes an expansion; the draft returns to the prior Preset step state when the prior state is still available.
- A future linked-preset mode exists; it must be explicitly labeled and must not be confused with default authoring-time expansion.

## Assumptions

- Runtime mode applies: this story changes task authoring behavior and validation, not only documentation.
- MM-556 Step Type authoring controls and MM-557 executable step validation are treated as prerequisites or adjacent behavior; this story focuses on Preset preview/application.
- Preset management remains separate from preset use. This story does not require building the preset catalog management experience.
- Supported provenance actions may be conditionally available based on source data; unsupported actions should not be presented as available.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | docs/Steps/StepTypes.md section 5.3 | Preset steps select reusable templates, configure inputs, and are temporary authoring states for preview and application rather than normally executable runtime steps. | In scope | FR-001, FR-002, FR-003, FR-010 |
| DESIGN-REQ-007 | docs/Steps/StepTypes.md section 6.5 | Preset selection for use belongs in the same step-authoring surface as Tool and Skill, not in a separate Presets section. | In scope | FR-001, FR-011 |
| DESIGN-REQ-009 | docs/Steps/StepTypes.md section 6.6 | The UI must preview generated steps before apply and replace the temporary Preset step with editable ordinary steps on apply. | In scope | FR-004, FR-005, FR-006 |
| DESIGN-REQ-010 | docs/Steps/StepTypes.md section 6.6 | Preset application should support undo, show origin, detach provenance, compare generated steps, and update to newer preset versions only as explicit user actions where supported. | In scope | FR-007, FR-008 |
| DESIGN-REQ-017 | docs/Steps/StepTypes.md section 8.4 | Preset validation requires existing/previewable preset versions, schema-valid inputs, deterministic expansion, valid generated Tool/Skill steps, policy limits, and visible warnings. | In scope | FR-002, FR-003, FR-004, FR-009 |
| DESIGN-REQ-019 | docs/Steps/StepTypes.md section 16 | Linked presets are not the default; any future linked-preset mode must be explicit and visibly different from ordinary preset application. | In scope | FR-010 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST allow authors to select Step Type `Preset` and choose a preset from the same step-authoring surface used for Tool and Skill steps.
- **FR-002**: The system MUST validate that the selected preset exists and that its version is active or explicitly previewable before preview or apply can succeed.
- **FR-003**: The system MUST validate Preset inputs against the preset input schema before expansion.
- **FR-004**: The system MUST expand configured Preset steps deterministically for preview and MUST surface expansion warnings before application.
- **FR-005**: The preview MUST list the generated steps before apply, including their user-visible titles and Step Types.
- **FR-006**: Applying a valid Preset expansion MUST replace the temporary Preset step with concrete Tool and/or Skill steps in the draft.
- **FR-007**: Generated steps MUST remain editable like ordinary steps after application.
- **FR-008**: Preset-derived generated steps MUST expose source provenance sufficient for supported origin, detach, compare, undo, and explicit update actions.
- **FR-009**: The system MUST validate generated steps under their own Tool or Skill rules before allowing application or submission.
- **FR-010**: The system MUST block task submission when unresolved Preset steps remain unless an explicit future linked-preset mode is available and visibly different from default preset application.
- **FR-011**: The Presets management section MUST NOT be required for choosing and applying a preset to the current task draft.

### Key Entities

- **Preset Step Draft**: A temporary authored step with Step Type `Preset`, selected preset identity/version, and preset input values.
- **Preset Expansion Preview**: The deterministic generated step list and warnings produced from a configured Preset step before application.
- **Preset-Derived Step**: A concrete Tool or Skill step inserted by applying a preset, including optional source provenance.
- **Preset Provenance**: Metadata that connects generated steps back to the preset, version, include path, or original step when source data is available.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying the expansion into the draft.
- **SC-002**: Validation tests cover missing preset, invalid preset inputs, generated step validation failure, and unresolved Preset submission blocking.
- **SC-003**: Preview shows generated step titles, Step Types, and expansion warnings before apply.
- **SC-004**: Applied preset-derived steps are editable as ordinary Tool and Skill steps and preserve visible origin/provenance where source data exists.
- **SC-005**: The task author can apply a preset from the step editor without using a separate Presets management section.

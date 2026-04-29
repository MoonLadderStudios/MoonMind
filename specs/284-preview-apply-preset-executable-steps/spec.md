# Feature Specification: Preview and Apply Preset Steps Into Executable Steps

**Feature Branch**: `284-preview-apply-preset-executable-steps`  
**Created**: 2026-04-29  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-565 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-565` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-565` and local artifact `artifacts/moonspec-inputs/MM-565-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: existing feature `specs/278-preview-apply-preset-steps` covers the earlier related Jira source `MM-558`, but no existing Moon Spec feature directory preserved `MM-565`; `Specify` was the first incomplete MM-565 stage.

## Original Preset Brief

```text
# MM-565 MoonSpec Orchestration Input

## Source

- Jira issue: MM-565
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preview and apply Preset steps into executable steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-565-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-565 from MM project
Summary: Preview and apply Preset steps into executable steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-565 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-565: Preview and apply Preset steps into executable steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.3 `preset`
- 6.5 Preset picker
- 6.6 Preset preview and apply
- 7.1 Authoring payload
- 7.2 Runtime plan mapping
- 8.4 Preset validation
- 12. Preset Management vs Preset Use
- 16. Open Design Decisions / Q1
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-017

As a task author, I can choose a Preset from the step editor, configure its inputs, preview deterministic expansion, and apply it into editable executable Tool and Skill steps.

Acceptance Criteria
- Preset use is available from the step editor, not only from the Presets management area.
- Preset preview lists the generated steps before application.
- Applying a preset replaces the Preset placeholder with editable Tool and Skill steps.
- Generated steps validate under their own Tool or Skill rules before executable submission.
- Submission rejects unresolved Preset steps by default.
- Updating to a newer preset version is explicit and previewed.

Requirements
- Preset steps are authoring-time placeholders by default.
- Preset expansion is deterministic and validated before execution.
- Preset management and preset use remain separate experiences.
- Future linked presets are not part of ordinary preset application unless explicitly introduced with separate semantics.
```

## User Story - Preview and Apply Preset Steps Into Executable Steps

**Summary**: As a task author, I can choose a Preset from the step editor, configure its inputs, preview deterministic expansion, and apply it into editable executable Tool and Skill steps.

**Goal**: Task authors can use reusable Presets during task authoring without allowing unresolved Preset placeholders to reach executable submission.

**Independent Test**: Render the task authoring surface, choose Step Type `Preset`, select and configure an available preset, preview the generated Tool and Skill steps, apply the preset, and verify the draft now contains editable executable steps while unresolved Preset submission remains blocked.

**Acceptance Scenarios**:

1. **Given** a task author is editing a step, **When** they choose Step Type `Preset`, **Then** they can select and configure a preset from the step editor without using the Presets management area.
2. **Given** a configured Preset step, **When** the author previews it, **Then** the system lists the generated steps before application and shows any expansion warnings.
3. **Given** a previewed Preset expansion is valid, **When** the author applies it, **Then** the temporary Preset placeholder is replaced with editable executable Tool and Skill steps.
4. **Given** generated steps came from a preset, **When** the author reviews or edits the draft, **Then** generated steps validate under their own Tool or Skill rules before executable submission.
5. **Given** a Preset step remains unresolved, **When** the author submits the task, **Then** submission is rejected by default.
6. **Given** a newer preset version is available, **When** the author updates preset-derived steps, **Then** the update is explicit and previewed before changing executable draft steps.

### Edge Cases

- The selected preset is missing, inactive, or not previewable; preview and apply are blocked with visible feedback.
- Preset inputs fail schema validation; the author sees validation feedback before expansion changes the draft.
- Deterministic expansion fails or generated Tool/Skill steps are invalid; the draft remains unchanged.
- Expansion succeeds with warnings; warnings are visible before the author applies the preset.
- Future linked-preset execution mode exists; it must be explicit and visibly different from ordinary preset application.
- Preset-derived steps become stale after source instructions change; updating to a newer version requires an explicit preview or reapply action.

## Assumptions

- Runtime mode applies because this story changes task authoring and submission behavior, not only documentation.
- Existing preset catalog, detail, and expansion services are the authoritative source for preview/application.
- Preset management remains separate from preset use; this story does not require new catalog management screens.
- The related MM-558 implementation may already satisfy most behavior, but MM-565 artifacts must preserve the MM-565 source request and coverage IDs.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | docs/Steps/StepTypes.md section 5.3 | A Preset step selects a reusable template, configures inputs, supports preview/application, and is normally a temporary authoring state rather than executable runtime work. | In scope | FR-001, FR-002, FR-003, FR-006 |
| DESIGN-REQ-007 | docs/Steps/StepTypes.md section 6.5 | Presets used for the current task are selected from the same step-authoring surface as Tool and Skill steps, not from a separate Presets management area. | In scope | FR-001, FR-010 |
| DESIGN-REQ-010 | docs/Steps/StepTypes.md section 6.6 | The UI previews generated steps before apply, and applying replaces the temporary Preset step with editable ordinary executable steps. | In scope | FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-011 | docs/Steps/StepTypes.md sections 7.1 and 7.2 | Executable submission contains Tool and Skill steps by default; Preset-derived metadata is provenance only and Preset steps do not map to runtime nodes by default. | In scope | FR-006, FR-007, FR-009 |
| DESIGN-REQ-017 | docs/Steps/StepTypes.md sections 8.4, 12, and 16/Q1 | Preset preview/application requires valid presets, valid inputs, deterministic expansion, generated-step validation, visible warnings, unresolved submission blocking, separate management/use experiences, and explicit linked-preset semantics if introduced later. | In scope | FR-002, FR-003, FR-004, FR-008, FR-009, FR-010, FR-011 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST allow authors to select Step Type `Preset` and choose/configure a preset from the same authoring surface used for Tool and Skill steps.
- **FR-002**: The system MUST validate that the selected preset exists and that its version is active or explicitly previewable before preview or apply succeeds.
- **FR-003**: The system MUST validate Preset inputs before expansion changes the draft.
- **FR-004**: The system MUST expand configured Preset steps deterministically for preview and surface expansion warnings before application.
- **FR-005**: The preview MUST list generated steps before apply, including user-visible titles and Step Types.
- **FR-006**: Applying a valid Preset expansion MUST replace the temporary Preset placeholder with concrete executable Tool and/or Skill steps.
- **FR-007**: Preset-derived generated steps MUST be editable like ordinary executable steps after application.
- **FR-008**: Generated steps MUST validate under their own Tool or Skill rules before executable submission.
- **FR-009**: Executable submission MUST reject unresolved Preset steps by default.
- **FR-010**: Preset management and preset use MUST remain separate experiences; Presets management MUST NOT be required to choose and apply a preset to the current task draft.
- **FR-011**: Updating preset-derived steps to a newer preset version MUST be an explicit action and MUST preview the resulting changes before modifying executable draft steps.

### Key Entities

- **Preset Step Draft**: A temporary authored step with Step Type `Preset`, selected preset identity/version, and preset input values.
- **Preset Expansion Preview**: The deterministic generated step list and warnings produced before application.
- **Preset-Derived Executable Step**: A concrete Tool or Skill step inserted by applying a preset, including available provenance metadata.
- **Preset Provenance**: Metadata that connects generated steps to the preset source for audit, review, reconstruction, and explicit update behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying expansion into the draft.
- **SC-002**: Validation tests cover missing or failing preset expansion, generated-step validation failure, and unresolved Preset submission blocking.
- **SC-003**: Preview shows generated step titles, Step Types, and expansion warnings before apply.
- **SC-004**: Applied preset-derived Tool and Skill steps are editable and submit as executable steps rather than Preset placeholders.
- **SC-005**: The task author can apply a preset from the step editor without using a separate Presets management section.
- **SC-006**: Preset-derived step updates are explicit and visible before draft mutation when newer preset instructions or versions are used.

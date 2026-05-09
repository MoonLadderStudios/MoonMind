# Feature Specification: Preview and Apply Preset Steps

**Feature Branch**: `run-jira-orchestrate-for-mm-572-preview-d49b5fcf`  
**Created**: 2026-05-09  
**Status**: Draft  
**Input**: Jira Orchestrate handoff for `MM-572`.

Preserved source:

- Source story: `STORY-004`
- Source summary: Preview and apply Preset steps
- Source Jira issue: `manual-mm-569-mm-574`
- Target Jira issue: `MM-572`
- Original brief reference: Jira issue `MM-572` recommended preset brief

Current step boundary: create the Jira Orchestrate/MoonSpec handoff artifacts for `MM-572` only. Implementation, verification, Jira transition, pull request creation, and downstream publish work are intentionally not run inline in this task creation step.

## Original Preset Brief

```text
Run Jira Orchestrate for MM-572.

Source story: STORY-004.
Source summary: Preview and apply Preset steps.
Source Jira issue: manual-mm-569-mm-574.
Original brief reference: Jira issue MM-572 recommended preset brief.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside this task creation step.
```

## User Story - Preview and Apply Preset Steps

**Summary**: As a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.

**Goal**: Task authors can use reusable Presets transparently during task authoring while generated executable Tool and Skill steps remain editable and unresolved Preset placeholders do not reach runtime execution.

**Independent Test**: Render the task authoring surface, choose Step Type `Preset`, select and configure an available preset, preview generated steps and warnings, apply the preset, and verify the draft contains editable executable Tool and/or Skill steps while unresolved Preset submission remains blocked.

**Acceptance Scenarios**:

1. **Given** a task author is editing a step, **When** they choose Step Type `Preset`, **Then** the step editor lets them select and configure a preset from the same authoring surface used for Tool and Skill steps.
2. **Given** a configured Preset step, **When** the author requests preview, **Then** the system lists generated steps before application and shows visible expansion warnings or errors.
3. **Given** the previewed expansion is valid, **When** the author applies the preset, **Then** the temporary Preset placeholder is replaced by concrete editable Tool and/or Skill steps.
4. **Given** generated steps came from a preset, **When** the author reviews or edits the draft, **Then** generated steps validate under their own Tool or Skill rules before executable submission.
5. **Given** a Preset step remains unresolved, **When** the author submits the task, **Then** submission is rejected by default with visible feedback.
6. **Given** Preset management exists elsewhere, **When** the author is choosing and applying a preset to the current task, **Then** the separate Presets management section is not required.

### Edge Cases

- The selected preset is missing, inactive, or not previewable; preview and apply are blocked with visible feedback.
- Preset inputs fail validation; the draft remains unchanged and the author sees actionable feedback.
- Deterministic expansion fails or generated Tool/Skill steps are invalid; apply is blocked and the Preset step remains editable.
- Expansion succeeds with warnings; warnings are visible before the author applies the preset.
- The author changes the selected preset or inputs after preview; stale preview data is invalidated before apply.

## Assumptions

- Runtime mode applies because this story changes task authoring and submission behavior, not only documentation.
- `docs/Steps/StepTypes.md` is the canonical design source for Step Type and Preset semantics.
- Existing task-template catalog/detail/expand services are the authoritative source for preset preview and application.
- Prior related artifacts for `MM-558`, `MM-565`, and `MM-578` may provide implementation evidence, but this feature directory preserves `MM-572` traceability separately.
- Preset management remains separate from preset use; this story does not require new preset catalog management screens.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Steps/StepTypes.md` sections 2, 4, 6.3 | `preset` is a canonical Step Type and an authoring-time composition step. | In scope | FR-001, FR-006 |
| DESIGN-REQ-002 | `docs/Steps/StepTypes.md` sections 5, 6.3 | Capability input contracts and schema-driven forms drive preset configuration without preset-specific UI branches. | In scope | FR-001, FR-002 |
| DESIGN-REQ-003 | `docs/Steps/StepTypes.md` sections 6.3, 8.4 | Preset expansion is deterministic and validated before execution. | In scope | FR-002, FR-003, FR-008 |
| DESIGN-REQ-004 | `docs/Steps/StepTypes.md` sections 4, 7.1 | Runtime workflows must not execute unresolved Preset steps by default. | In scope | FR-006 |
| DESIGN-REQ-005 | `docs/Steps/StepTypes.md` sections 3, 4 | Preset provenance is audit/reconstruction metadata, not hidden runtime work. | In scope | FR-004, FR-005 |
| DESIGN-REQ-006 | `docs/Steps/StepTypes.md` sections 6.3, 12 | Preset management and preset use are separate experiences. | In scope | FR-007 |

## Requirements

### Functional Requirements

- **FR-001**: The step editor MUST allow authors to select Step Type `Preset` and choose/configure a preset from the same authoring surface used for Tool and Skill steps.
- **FR-002**: The system MUST validate that the selected preset exists, the selected version is active or explicitly previewable, and input values are valid before preview or apply succeeds.
- **FR-003**: The system MUST preview configured Preset expansion deterministically and list generated step titles, Step Types, and warnings before application.
- **FR-004**: Applying a valid Preset expansion MUST replace the temporary Preset placeholder with concrete executable Tool and/or Skill steps.
- **FR-005**: Preset-derived generated steps MUST remain editable like ordinary executable steps after application.
- **FR-006**: Generated Tool and Skill steps MUST validate under their own rules before executable submission, and unresolved Preset steps MUST be rejected by default.
- **FR-007**: Preset management and preset use MUST remain separate; the Presets management section MUST NOT be required for choosing and applying a preset to the current task draft.
- **FR-008**: Preview/apply failure states MUST leave the current draft unchanged and show visible author feedback.

### Key Entities

- **Preset Step Draft**: Temporary authored step with Step Type `Preset`, selected preset identity/version, and input values.
- **Preset Expansion Preview**: Deterministic generated step list plus warnings or errors shown before application.
- **Preset-Derived Executable Step**: Concrete Tool or Skill step inserted by applying a preset, including available provenance metadata.

## Success Criteria

- **SC-001**: Frontend tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying expansion into the draft.
- **SC-002**: Validation tests cover missing/failing preset expansion, generated-step validation failure, stale preview invalidation, and unresolved Preset submission blocking.
- **SC-003**: Preview shows generated step titles, Step Types, and expansion warnings before apply.
- **SC-004**: Applied preset-derived Tool and Skill steps are editable and submit as executable steps rather than Preset placeholders.
- **SC-005**: The task author can apply a preset from the step editor without using a separate Presets management section.

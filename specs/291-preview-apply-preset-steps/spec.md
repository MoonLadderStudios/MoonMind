# Feature Specification: Preview and Apply Preset Steps

**Feature Branch**: `291-preview-apply-preset-steps`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: "# MM-578 MoonSpec Orchestration Input\n\n## Source\n\n- Jira issue: MM-578\n- Jira project key: MM\n- Issue type: Story\n- Current status at fetch time: In Progress\n- Summary: Preview and apply Preset steps\n- Trusted fetch tool: `jira.get_issue`\n- Trusted response artifact: `artifacts/moonspec-inputs/MM-578-trusted-jira-get-issue-summary.json`\n- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.\n- Labels: moonmind-workflow-mm-911309af-6b4f-48e7-8835-e533aa9af8cf\n\n## Canonical MoonSpec Feature Request\n\nJira issue: MM-578 from MM project\nSummary: Preview and apply Preset steps\nIssue type: Story\nCurrent Jira status: In Progress\nJira project key: MM\n\nUse this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-578 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.\n\nMM-578: Preview and apply Preset steps\n\nSource Reference\nSource Document: docs/Steps/StepTypes.md\nSource Title: Step Types\nSource Sections:\n- 5.3 preset\n- 6.5 Preset picker\n- 6.6 Preset preview and apply\n- 8.4 Preset validation\n- 12. Preset Management vs Preset Use\nCoverage IDs:\n- DESIGN-REQ-004\n- DESIGN-REQ-011\n- DESIGN-REQ-012\n- DESIGN-REQ-013\n- DESIGN-REQ-019\nAs a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.\nAcceptance Criteria\n- Preset use lives in the step editor, not a separate Presets section.\n- Preset preview lists generated steps before application.\n- Applying a preset replaces the temporary Preset step with concrete Tool and/or Skill steps.\n- Preset validation covers existence, version, input schema, deterministic expansion, generated-step validity, step limits, policy limits, and visible warnings.\nRequirements\n- Presets are reusable parameterized authoring templates.\n- Preset steps are normally authoring-time placeholders only.\n\n## Relevant Implementation Notes\n\n- Source design path: `docs/Steps/StepTypes.md`.\n- Section 5.3 `preset`: Preset steps select reusable templates, configure inputs, and are temporary authoring state for known multi-step workflows rather than executable runtime steps.\n- Section 6.5 Preset picker: Preset use belongs in the same step editor as Tool and Skill; the Presets section is management-only.\n- Section 6.6 Preset preview and apply: Preview lists generated steps before application; applying a preset replaces the temporary Preset step with expanded editable steps and supports undo, origin/provenance visibility, detach, comparison, and explicit update to newer versions.\n- Section 7.1 Authoring payload: draft authoring may temporarily contain `type: \"preset\"`, but executable submission should contain only Tool and Skill steps by default while preserving preset-derived source metadata.\n- Section 8.4 Preset validation: preview/application require existing active or previewable preset version, schema-valid inputs, deterministic expansion, generated Tool/Skill step validity, enforced step/policy limits, and visible warnings; unresolved Preset steps must not be submitted unless a future linked-preset execution mode explicitly supports them.\n- Section 12 Preset Management vs Preset Use: management covers catalog lifecycle and audit, while use covers selecting, configuring, previewing, and applying a preset inside task authoring.\n\n## MoonSpec Classification Input\n\nClassify this as a single-story runtime feature request for task authoring: implement the authoring experience for previewing and applying Preset steps while keeping Tool, Skill, and Preset semantics distinct and preserving MM-578 traceability.\n\n## Orchestration Constraints\n\nSelected mode: runtime.\nDefault to runtime mode and only use docs mode when explicitly requested.\nIf the brief points at an implementation document, treat it as runtime source requirements.\nClassify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.\nInspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.\n"

Preserved source Jira preset brief: `MM-578` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-578` and local artifact `artifacts/moonspec-inputs/MM-578-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: existing related feature directories `specs/278-preview-apply-preset-steps` and `specs/284-preview-apply-preset-executable-steps` cover earlier Jira sources, but no existing Moon Spec feature directory preserved `MM-578`; `Specify` was the first incomplete MM-578 stage.

## Original Preset Brief

```text
# MM-578 MoonSpec Orchestration Input

## Source

- Jira issue: MM-578
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preview and apply Preset steps
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `artifacts/moonspec-inputs/MM-578-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Labels: moonmind-workflow-mm-911309af-6b4f-48e7-8835-e533aa9af8cf

## Canonical MoonSpec Feature Request

Jira issue: MM-578 from MM project
Summary: Preview and apply Preset steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-578 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-578: Preview and apply Preset steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.3 preset
- 6.5 Preset picker
- 6.6 Preset preview and apply
- 8.4 Preset validation
- 12. Preset Management vs Preset Use
Coverage IDs:
- DESIGN-REQ-004
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-019
As a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.
Acceptance Criteria
- Preset use lives in the step editor, not a separate Presets section.
- Preset preview lists generated steps before application.
- Applying a preset replaces the temporary Preset step with concrete Tool and/or Skill steps.
- Preset validation covers existence, version, input schema, deterministic expansion, generated-step validity, step limits, policy limits, and visible warnings.
Requirements
- Presets are reusable parameterized authoring templates.
- Preset steps are normally authoring-time placeholders only.

## Relevant Implementation Notes

- Source design path: `docs/Steps/StepTypes.md`.
- Section 5.3 `preset`: Preset steps select reusable templates, configure inputs, and are temporary authoring state for known multi-step workflows rather than executable runtime steps.
- Section 6.5 Preset picker: Preset use belongs in the same step editor as Tool and Skill; the Presets section is management-only.
- Section 6.6 Preset preview and apply: Preview lists generated steps before application; applying a preset replaces the temporary Preset step with expanded editable steps and supports undo, origin/provenance visibility, detach, comparison, and explicit update to newer versions.
- Section 7.1 Authoring payload: draft authoring may temporarily contain `type: "preset"`, but executable submission should contain only Tool and Skill steps by default while preserving preset-derived source metadata.
- Section 8.4 Preset validation: preview/application require existing active or previewable preset version, schema-valid inputs, deterministic expansion, generated Tool/Skill step validity, enforced step/policy limits, and visible warnings; unresolved Preset steps must not be submitted unless a future linked-preset execution mode explicitly supports them.
- Section 12 Preset Management vs Preset Use: management covers catalog lifecycle and audit, while use covers selecting, configuring, previewing, and applying a preset inside task authoring.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for task authoring: implement the authoring experience for previewing and applying Preset steps while keeping Tool, Skill, and Preset semantics distinct and preserving MM-578 traceability.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

```

## User Story - Preview and Apply Preset Steps

**Summary**: As a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.

**Goal**: Task authors can use reusable Presets transparently during task authoring while keeping generated Tool and Skill steps editable and preventing unresolved Preset placeholders from reaching executable submission.

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
- Existing task-template catalog/detail/expand services are the authoritative source for preset preview and application.
- Existing MM-558/MM-565 implementation evidence may satisfy MM-578 behavior, but MM-578 artifacts must preserve the MM-578 source request and coverage IDs.
- Preset management remains separate from preset use; this story does not require new preset catalog management screens.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-004 | docs/Steps/StepTypes.md section 5.3 | Presets are reusable parameterized authoring templates; Preset steps configure inputs and are normally temporary authoring placeholders. | In scope | FR-001, FR-002, FR-005 |
| DESIGN-REQ-011 | docs/Steps/StepTypes.md section 6.5 | Preset use belongs in the step editor, not a separate Presets section for applying presets to the current task. | In scope | FR-001, FR-007 |
| DESIGN-REQ-012 | docs/Steps/StepTypes.md section 6.6 | Preset preview lists generated steps before application, and applying replaces the temporary Preset step with expanded editable steps. | In scope | FR-003, FR-004, FR-005 |
| DESIGN-REQ-013 | docs/Steps/StepTypes.md section 8.4 | Preset preview/application requires valid preset/version/input state, deterministic expansion, generated-step validation, step/policy limits, and visible warnings. | In scope | FR-002, FR-003, FR-006 |
| DESIGN-REQ-019 | docs/Steps/StepTypes.md section 12 | Preset management and preset use remain separate experiences; management is catalog lifecycle, while use is step authoring preview/apply. | In scope | FR-007 |

## Requirements *(mandatory)*

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

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying expansion into the draft.
- **SC-002**: Validation tests cover missing/failing preset expansion, generated-step validation failure, stale preview invalidation, and unresolved Preset submission blocking.
- **SC-003**: Preview shows generated step titles, Step Types, and expansion warnings before apply.
- **SC-004**: Applied preset-derived Tool and Skill steps are editable and submit as executable steps rather than Preset placeholders.
- **SC-005**: The task author can apply a preset from the step editor without using a separate Presets management section.

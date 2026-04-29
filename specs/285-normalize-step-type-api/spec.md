# Feature Specification: Normalize Step Type API and Executable Submission Payloads

**Feature Branch**: `285-normalize-step-type-api`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-566 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-566 MoonSpec Orchestration Input

## Source

- Jira issue: MM-566
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Normalize Step Type API and executable submission payloads
- Trusted fetch tool: `jira.get_issue` through MoonMind MCP `/mcp/tools/call`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-566 from MM project
Summary: Normalize Step Type API and executable submission payloads
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-566 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-566: Normalize Step Type API and executable submission payloads

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7. Runtime and Payload Contract
- 8. Validation Rules
- 11. API Shape
- 14. Migration Guidance
Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-019

As an API consumer, I can submit explicit discriminated Step Type payloads where drafts may contain Preset steps but executable submissions normally contain only Tool and Skill steps.

Acceptance Criteria
- Draft APIs can represent ToolStep, SkillStep, and PresetStep as explicit discriminated shapes.
- Executable submission normally accepts only ToolStep and SkillStep.
- Invalid mixed payloads fail fast with validation errors.
- Legacy shapes remain readable only where migration requires them.
- New API outputs and docs converge on Step Type terminology and shapes.

Requirements
- Step payloads include stable local identity, optional/generated title, type discriminator, and matching type-specific payload.
- Compatibility readers do not reintroduce ambiguous UI or docs terminology.
- Migration can proceed in phases while preserving desired-state API direction."

Preserved source Jira preset brief: `MM-566` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-566` and local artifact `artifacts/moonspec/mm-566-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: related feature `specs/279-submit-discriminated-executable-payloads` preserves Jira source `MM-559`; no existing Moon Spec feature directory preserved `MM-566`, so `Specify` is the first incomplete MM-566 stage.

## User Story - Normalize Step Type Payload Boundaries

**Summary**: As an API consumer, I can work with explicit Tool, Skill, and Preset step shapes in draft-oriented payloads while executable submissions accept only Tool and Skill steps.

**Goal**: Step Type payloads should be unambiguous across authoring, draft reconstruction, executable submission, and documentation so invalid mixed shapes fail fast and legacy readers do not erase the desired Step Type model.

**Independent Test**: Reconstruct or validate payloads containing ToolStep, SkillStep, PresetStep, invalid mixed steps, and legacy step shapes; then confirm draft-oriented surfaces preserve the explicit discriminator, executable submission rejects unresolved Preset or invalid Activity labels, and docs describe the same Step Type terminology.

**Acceptance Scenarios**:

1. **SCN-001 - Draft Tool shape**: **Given** a draft or editable task payload contains `type: "tool"` with a matching Tool payload, **When** the draft is reconstructed for editing, **Then** the step remains a Tool step with stable identity, title, instructions, and Tool inputs preserved.
2. **SCN-002 - Draft Skill shape**: **Given** a draft or editable task payload contains `type: "skill"` with a matching Skill payload, **When** the draft is reconstructed for editing, **Then** the step remains a Skill step with stable identity, title, instructions, and Skill inputs preserved.
3. **SCN-003 - Draft Preset shape**: **Given** a draft or editable task payload contains `type: "preset"` with a matching Preset payload, **When** the draft is reconstructed for editing, **Then** the step remains a Preset step and is not silently coerced to Skill.
4. **SCN-004 - Executable boundary**: **Given** a submitted executable task contains an unresolved Preset step or Activity-labeled step, **When** executable payload validation runs, **Then** validation fails before runtime materialization.
5. **SCN-005 - Mixed payload rejection**: **Given** a submitted executable step combines conflicting Step Type payloads, **When** validation runs, **Then** validation fails with an actionable error.
6. **SCN-006 - Legacy readability**: **Given** an older task shape lacks an explicit Step Type but still carries recognizable legacy Tool or Skill fields, **When** compatibility readers reconstruct it, **Then** the reader preserves existing editability without promoting ambiguous terminology into new API output.

### Edge Cases

- A draft Preset step has preset inputs but no executable instructions yet.
- A Step Type discriminator differs in case, such as `Activity`.
- A legacy Skill step is represented through older `tool.type = "skill"` fields.
- A Tool step includes a Skill payload, or a Skill step includes a non-skill Tool payload.
- A doc or UI-facing output uses `Activity`, `Script`, or `Command` as the primary Step Type label.

## Assumptions

- MM-559 already delivered much of the executable submission boundary; this MM-566 story may reuse that implementation as evidence but must preserve the newer Jira source request in its own artifacts.
- Preset steps remain authoring-time draft placeholders by default and are not executable runtime plan nodes.
- Legacy readers may remain permissive only to preserve editability for already-stored task inputs.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Functional Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-012 | `docs/Steps/StepTypes.md` section 7 | Draft authoring may temporarily contain Preset steps, while executable task submission should normally contain only Tool and Skill steps. | In scope | FR-001, FR-002, FR-004 |
| DESIGN-REQ-014 | `docs/Steps/StepTypes.md` section 8 | Invalid mixed Step Type payloads must fail validation. | In scope | FR-003, FR-005 |
| DESIGN-REQ-015 | `docs/Steps/StepTypes.md` section 11 | Step payloads use stable local identity, optional title, explicit type discriminator, and matching type-specific payloads. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` section 14 | Migration may proceed in phases while preserving legacy readability only where necessary and converging new outputs on Step Type terminology. | In scope | FR-006, FR-007 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Draft-oriented APIs and edit reconstruction MUST represent `ToolStep`, `SkillStep`, and `PresetStep` as explicit discriminated shapes.
- **FR-002**: Executable task submission MUST accept only `type: "tool"` and `type: "skill"` step payloads by default.
- **FR-003**: Executable task submission MUST reject unresolved `type: "preset"` steps and Temporal `Activity` labels before runtime materialization.
- **FR-004**: Draft Preset steps MUST remain Preset steps during reconstruction and MUST NOT be silently coerced to Skill.
- **FR-005**: Step validation MUST reject conflicting or mixed type-specific payloads with actionable validation errors.
- **FR-006**: Compatibility readers MUST keep legacy Tool and Skill shapes readable only where needed for migration and editing.
- **FR-007**: New API outputs, UI-facing labels, and canonical docs MUST converge on Tool, Skill, Preset, and Step Type terminology rather than Activity, Script, or Command as the primary discriminator.

### Key Entities

- **Step Type**: The user-facing discriminator for a task step: Tool, Skill, or Preset.
- **ToolStep**: An executable step with `type: "tool"` and a matching typed Tool payload.
- **SkillStep**: An executable step with `type: "skill"` and a matching Skill payload.
- **PresetStep**: A draft-only authoring placeholder with `type: "preset"` and a matching Preset payload.
- **Executable Submission**: The task payload boundary that can be materialized into runtime execution and normally accepts only ToolStep and SkillStep.
- **Legacy Reader**: Compatibility code that reconstructs older task shapes for editing without changing the desired-state API.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Draft reconstruction tests cover explicit Tool, Skill, and Preset step payloads and preserve their Step Type discriminators.
- **SC-002**: Executable submission tests cover accepted Tool and Skill payloads plus rejected Preset, Activity, and conflicting mixed payloads.
- **SC-003**: Legacy reconstruction tests prove older Tool and Skill-shaped payloads remain editable without introducing Preset execution behavior.
- **SC-004**: Documentation verification confirms the Step Type API shape is not internally contradictory and uses Step Type terminology for the primary discriminator.
- **SC-005**: Final verification preserves Jira issue key `MM-566` and the original Jira preset brief in active MoonSpec artifacts and delivery metadata.

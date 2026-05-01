# Feature Specification: Submit Flattened Executable Steps with Provenance

**Feature Branch**: `292-submit-flattened-executable-steps-with-provenance`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-579 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-579 MoonSpec Orchestration Input

## Source

- Jira issue: MM-579
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Submit flattened executable steps with provenance
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-579 from MM project
Summary: Submit flattened executable steps with provenance
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.3 preset
- 7.1 Authoring payload
- 13. Proposal and Promotion Semantics
Coverage IDs:
- DESIGN-REQ-004
- DESIGN-REQ-006
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-023

As an operator, I can submit tasks that contain only executable Tool and Skill steps by default, while preset-derived steps retain provenance for audit and reconstruction without runtime lookup.

Acceptance Criteria
- Executable submission contains only Tool and Skill steps by default.
- Preset-derived steps carry source.kind, presetId, presetVersion, includePath when applicable, and originalStepId metadata.
- Runtime correctness does not depend on preset provenance metadata or live catalog lookup.
- Promotion validates the stored flat payload and only refreshes from catalog after explicit user action with preview.

Requirements
- Preset expansion is deterministic and validated before execution.
- Preset provenance supports audit, UI grouping, proposal reconstruction, and review.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-579 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

Preserved source Jira preset brief: `MM-579` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-579` and local artifact `artifacts/moonspec/MM-579-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-579`, so `Specify` was the first incomplete MM-579 stage.

## User Story - Flatten Preset-Derived Executable Submissions

**Summary**: As an operator, I can submit tasks that contain only executable Tool and Skill steps by default, while preset-derived steps retain provenance for audit and reconstruction without runtime lookup.

**Goal**: Task submission and proposal promotion use the reviewed flat executable steps as the source of truth, while preserving preset provenance only as metadata for audit, grouping, reconstruction, and explicit refresh workflows.

**Independent Test**: Apply a preset into executable Tool and Skill steps, submit or promote the resulting task, and verify that execution accepts only the flat Tool and Skill steps, provenance remains visible, no live preset lookup is required for correctness, and any catalog refresh requires explicit preview and validation.

**Acceptance Scenarios**:

1. **Given** a task draft contains a configured preset, **When** the operator applies it for submission, **Then** the submitted task contains executable Tool and Skill steps rather than an unresolved Preset step.
2. **Given** executable steps were derived from a preset, **When** the submitted task is reviewed, executed, or audited, **Then** each derived step retains source metadata identifying the preset, version, original step, and include path when present.
3. **Given** preset provenance is missing, partial, stale, or references a catalog entry that is unavailable, **When** the flat executable steps are otherwise valid, **Then** runtime execution still proceeds from the executable Tool and Skill steps without requiring live catalog lookup.
4. **Given** a stored proposal contains preset-derived executable steps, **When** the proposal is promoted, **Then** promotion validates and uses the stored flat payload without silently re-expanding a live preset catalog entry.
5. **Given** an operator wants a draft or proposal refreshed from a newer preset catalog entry, **When** refresh is requested, **Then** the system requires an explicit preview and validation step before replacing the reviewed flat payload.

**Edge Cases**:

- A preset expands into multiple levels of included steps; provenance preserves the include path for derived executable steps when that path exists.
- A derived step has no include path because it came directly from the selected preset; the step remains valid when other required provenance fields are present.
- A stale preset version remains recorded in provenance; runtime execution still uses the flat executable steps and does not refresh automatically.
- A submitted task still contains an unresolved Preset step; validation rejects it before runtime execution.
- A promoted proposal has provenance metadata but no live preset catalog access; promotion still validates the stored executable payload.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST submit executable task payloads that contain only Tool and Skill steps by default after a preset has been applied.
- **FR-002**: The system MUST reject unresolved Preset steps in runtime submission or promotion paths unless an explicit future linked-preset mode is selected outside this story's scope.
- **FR-003**: Preset-derived executable steps MUST preserve provenance metadata containing source kind, preset identifier, preset version, original step identifier, and include path when an include path exists.
- **FR-004**: Preset provenance metadata MUST be usable for audit, review, UI grouping, and proposal reconstruction.
- **FR-005**: Runtime execution MUST NOT depend on preset provenance metadata or live preset catalog lookup when the submitted Tool and Skill steps are otherwise valid.
- **FR-006**: Preset expansion MUST be deterministic and validated before the resulting executable steps can be submitted.
- **FR-007**: Stored promotable proposals derived from presets MUST contain a reviewed flat executable payload before promotion.
- **FR-008**: Proposal promotion MUST validate the stored flat executable payload and MUST NOT silently re-expand a live preset catalog entry.
- **FR-009**: Refreshing a draft or proposal from a preset catalog entry MUST require an explicit operator action with preview and validation before the stored flat payload changes.
- **FR-010**: Validation and verification evidence for this feature MUST preserve Jira issue key `MM-579` and the original Jira preset brief for traceability.

### Key Entities

- **Executable Step**: A task step that is ready for runtime execution as either Tool or Skill.
- **Preset Step**: An authoring-time placeholder that must be applied into executable steps before default runtime submission.
- **Preset Provenance**: Metadata on a derived executable step that records where the step came from, including preset identity, preset version, original step identity, and include path when available.
- **Flat Executable Payload**: The reviewed task or proposal payload containing executable Tool and Skill steps without unresolved Preset steps.
- **Promotable Proposal**: A stored proposal that can become an executable task after validating its flat executable payload.

## Assumptions

- The ordinary runtime path does not include a linked-preset execution mode; any future linked-preset mode will be explicit and visibly distinct from default preset application.
- The feature may rely on existing product terminology for Tool, Skill, Preset, proposal, promotion, and provenance.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
|----|--------|---------------------|-------|---------------------|
| DESIGN-REQ-004 | `docs/Steps/StepTypes.md` section 5.3 `preset` | Applying a preset produces concrete executable Tool and Skill steps, and the default submitted form should not keep the Preset step as executable work. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-006 | `docs/Steps/StepTypes.md` section 5.3 `preset` | Preset-derived steps preserve source metadata identifying the preset origin, version, original step, and include path where applicable. | In scope | FR-003, FR-004 |
| DESIGN-REQ-015 | `docs/Steps/StepTypes.md` section 7.1 `Authoring payload` | Executable task submissions should contain Tool and Skill steps by default, while Preset steps remain authoring-time placeholders. | In scope | FR-001, FR-002 |
| DESIGN-REQ-016 | `docs/Steps/StepTypes.md` section 7.1 `Authoring payload` | Preset provenance is metadata for audit, grouping, reconstruction, and review, and must not be required for runtime correctness. | In scope | FR-003, FR-004, FR-005 |
| DESIGN-REQ-023 | `docs/Steps/StepTypes.md` section 13 `Proposal and Promotion Semantics` | Proposals preserve executable intent by storing flat executable payloads, validating those payloads during promotion, and requiring explicit preview for catalog refresh. | In scope | FR-007, FR-008, FR-009 |

## Success Criteria

- **SC-001**: In all covered submission scenarios, unresolved Preset steps are rejected before runtime execution and executable Tool and Skill steps are accepted.
- **SC-002**: 100% of preset-derived executable steps in covered scenarios retain required provenance metadata, with include path retained whenever the source expansion provides one.
- **SC-003**: Covered runtime execution scenarios succeed from valid flat executable payloads even when preset provenance is stale or live preset catalog access is unavailable.
- **SC-004**: Covered proposal promotion scenarios validate the stored flat payload and perform zero automatic live preset re-expansions.
- **SC-005**: Covered refresh scenarios require explicit operator preview and validation before replacing a stored draft or proposal payload from the preset catalog.
- **SC-006**: Final verification can trace `MM-579`, the original Jira preset brief, and all in-scope source design requirements through the MoonSpec artifacts.

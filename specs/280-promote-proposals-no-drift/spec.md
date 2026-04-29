# Feature Specification: Promote Proposals Without Live Preset Drift

**Feature Branch**: `280-promote-proposals-no-drift`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-560 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-560` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-560` and local artifact `artifacts/moonspec-inputs/MM-560-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-560` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-560 MoonSpec Orchestration Input

## Source

- Jira issue: MM-560
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Promote proposals without live preset drift
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-560-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-560 from MM project
Summary: Promote proposals without live preset drift
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-560 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-560: Promote proposals without live preset drift

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7.3 Backward compatibility
- 13. Proposal and Promotion Semantics
- 14. Migration Guidance
- 15. Non-Goals
- 16. Open Design Decisions
Coverage IDs:
- DESIGN-REQ-014
- DESIGN-REQ-018
- DESIGN-REQ-019

As an operator reviewing task proposals, I want promotable payloads to preserve executable intent so promotion validates reviewed steps and cannot silently re-expand changed preset catalog entries.

Acceptance Criteria
- Stored proposals contain executable Tool and Skill steps by default.
- Preset provenance can be preserved without causing live catalog lookup during promotion.
- Promotion validates the reviewed flat payload.
- Refreshing from the preset catalog is an explicit action with preview and validation.
- Legacy payload readers do not reintroduce ambiguous umbrella terminology into new UI or docs.

Requirements
- Task proposals preserve executable intent.
- Migration may read legacy shapes but new authoring surfaces converge on Step Type.
- Compatibility readers must not undermine terminology or payload convergence.
- Future linked-preset behavior requires separate explicit rules.
```

## User Story - Reviewed Proposal Promotion

**Summary**: As an operator reviewing task proposals, I want promotion to execute the reviewed flat Tool and Skill payload so preset-derived proposals cannot silently drift by re-expanding live catalog entries.

**Goal**: Promotion starts an execution from the stored reviewed proposal payload, preserves preset provenance metadata, validates the flat executable payload, and rejects promotion-time replacement payloads that could bypass the reviewed content.

**Independent Test**: Create or load a proposal containing flattened executable steps and preset provenance, promote it, and verify the promoted execution parameters are derived from the stored proposal payload without any live preset lookup or full payload replacement.

**Acceptance Scenarios**:

1. **Given** an open proposal with stored executable Tool and Skill steps and preset provenance metadata, **When** the operator promotes it, **Then** the system validates and executes the stored flat payload and preserves the provenance metadata.
2. **Given** an open proposal, **When** a promotion request includes only bounded promotion controls such as runtime mode, priority, max attempts, or note, **Then** the system applies those controls without replacing the reviewed steps.
3. **Given** an open proposal, **When** a promotion request includes a full task payload override, **Then** the system rejects the request before creating an execution.
4. **Given** a stored proposal payload contains an unresolved Preset step, **When** promotion validates the payload, **Then** promotion fails because submitted task steps must be executable Tool or Skill steps.

### Edge Cases

- Stored proposal payloads may include legacy-compatible step shapes, but new validation must not allow unresolved `type: "preset"` submission.
- Promotion-time runtime changes must not remove `authoredPresets`, step `source`, or executable steps from the stored payload.
- Existing preset provenance metadata is audit metadata only and must not trigger catalog refresh or lookup during promotion.

## Assumptions

- Runtime selection is a bounded promotion control because it changes where the reviewed task runs, not what reviewed steps execute.
- Explicit preset refresh and preview behavior is out of scope for this story except that promotion must not perform it implicitly.

## Source Design Requirements

| ID | Source | Requirement | Scope | Maps To |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-014 | `docs/Steps/StepTypes.md` section 7.3 | Compatibility readers may read legacy shapes during migration, but new surfaces must normalize toward Step Type and must not reintroduce ambiguous umbrella terminology. | In scope | FR-001, FR-006 |
| DESIGN-REQ-018 | `docs/Steps/StepTypes.md` sections 13 and 14 | Stored proposals should contain executable Tool and Skill steps, may carry preset provenance as metadata, and promotion must validate the reviewed flat payload without requiring live preset lookup. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` sections 15 and 16 | Hidden runtime preset work is a non-goal; any future linked-preset or refresh behavior must be explicit and visibly different from ordinary promotion. | In scope | FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST validate stored proposal task steps as executable Tool or Skill steps before creating a promoted execution.
- **FR-002**: System MUST preserve stored `authoredPresets` and step `source` provenance metadata in the final promoted task payload when present.
- **FR-003**: System MUST derive promoted execution parameters from the stored reviewed proposal payload by default.
- **FR-004**: System MAY apply bounded promotion controls such as runtime mode, priority, max attempts, and note without replacing the reviewed payload.
- **FR-005**: System MUST reject promotion requests that attempt to replace the reviewed proposal with a full task payload override.
- **FR-006**: User-visible proposal preview and API response terminology MUST expose preset provenance without using ambiguous umbrella terminology for Tool, Skill, and Preset.

### Key Entities

- **Task Proposal**: A reviewed candidate task with status, repository, provenance, and a stored `taskCreateRequest` envelope.
- **Promoted Execution Payload**: The canonical task payload used to start the execution after promotion.
- **Preset Provenance Metadata**: Optional audit metadata such as `authoredPresets` and step `source` that explains preset-derived origins without requiring runtime catalog lookup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests verify promotion preserves preset provenance and rejects unresolved Preset steps.
- **SC-002**: API tests verify full task payload overrides are rejected before execution creation.
- **SC-003**: API tests verify runtime-only promotion still creates an execution using stored reviewed task content.
- **SC-004**: Traceability checks preserve `MM-560`, `DESIGN-REQ-014`, `DESIGN-REQ-018`, and `DESIGN-REQ-019` in Moon Spec artifacts.

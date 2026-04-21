# Feature Specification: Surface Hierarchy and liquidGL Fallback Contract

**Feature Branch**: `217-surface-hierarchy-liquidgl`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-425 as the canonical Moon Spec orchestration input."

```text
# MM-425 MoonSpec Orchestration Input

Jira issue: MM-425 from MM project
Summary: Implement surface hierarchy and liquidGL fallback contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-425 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-425: Implement surface hierarchy and liquidGL fallback contract

Source Reference
- Source Document: docs/UI/MissionControlDesignSystem.md
- Source Title: Mission Control Design System
- Source Sections:
  - 3.2 Matte for content, glass for controls
  - 3.3 One hero effect per page
  - 4. Surface hierarchy
  - 8. Glass system
  - 13.5 liquidGL enhancement contract
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-015
  - DESIGN-REQ-018
  - DESIGN-REQ-027

User Story
As an operator, I want content surfaces, control surfaces, and premium liquid-glass surfaces to have distinct roles and reliable fallbacks so dense work stays readable while elevated controls feel premium.

Acceptance Criteria
- Surface classes or modifiers clearly distinguish matte data slabs, satin form surfaces, glass controls, liquidGL hero targets, and accent/live surfaces.
- Default glass surfaces have token-driven translucent fill, 1px luminous border, controlled shadow separation, supported backdrop-filter blur/saturation, and coherent near-opaque fallback.
- liquidGL target components remain fully laid out, bordered, padded, shadowed, and legible with JavaScript/WebGL disabled.
- liquidGL is not applied to dense tables, large cards, long forms, large scrolling containers, large textareas, or default panel/card classes.
- A page has no more than one liquidGL hero surface by default; any second usage must be explicit and non-competing.
- Nested dense cards and panels use quieter, more opaque weights rather than repeating the same glass effect.

Requirements
- Implement strict surface hierarchy.
- Provide CSS glass as the default glass foundation.
- Treat liquidGL as bounded enhancement with graceful fallback.
- Preserve matte readability for dense content and editing surfaces.
```

## User Story - Surface Hierarchy and Fallbacks

**Summary**: As a Mission Control operator, I want content surfaces, control surfaces, and premium liquid-glass surfaces to have distinct roles and reliable fallbacks so dense work stays readable while elevated controls feel premium.

**Goal**: Mission Control exposes a shared surface hierarchy where matte data slabs, satin form surfaces, glass controls, bounded liquidGL hero surfaces, and accent/live surfaces have distinct reusable styles and fallback behavior.

**Independent Test**: Inspect the shared Mission Control stylesheet and render representative task-list and Create page surfaces. The story passes when named surface classes express each hierarchy role, glass uses token-driven translucent fill/border/elevation with near-opaque fallback, liquidGL remains scoped to explicit bounded targets, dense content stays matte, and existing task-list/Create page behavior still passes.

**Acceptance Scenarios**:

1. **Given** shared Mission Control CSS is loaded, **when** surface classes are inspected, **then** matte data, satin form, glass control, liquidGL hero, and accent/live roles are distinguishable by stable selectors and token-driven styling.
2. **Given** a browser supports backdrop filtering, **when** glass control surfaces render, **then** they use token-driven translucent fill, a 1px luminous border, controlled shadow separation, and blur/saturation.
3. **Given** a browser does not support backdrop filtering, **when** glass control surfaces render, **then** they fall back to coherent near-opaque token-based surfaces.
4. **Given** liquidGL is unavailable, disabled, or not initialized, **when** a liquidGL target renders, **then** its CSS shell still provides layout, border, padding, shadow, readability, and standard glass fallback.
5. **Given** dense tables, large cards, long forms, large scrolling containers, large textareas, or default panel/card classes render, **when** their surface styles are inspected, **then** liquidGL is not applied by default.
6. **Given** nested dense cards and panels render, **when** their surface posture is inspected, **then** they use quieter, more opaque weights instead of repeating the same glass effect.

### Edge Cases

- Backdrop filter support is unavailable.
- liquidGL initialization has not completed or JavaScript/WebGL is disabled.
- Dense data slabs contain long identifiers or scrolling table content.
- Form and editing surfaces include long textareas.
- A page already contains one liquidGL hero target.
- Light and dark themes must keep the same surface roles with theme-appropriate token values.

## Assumptions

- Runtime mode is selected, so the shared Mission Control UI behavior and styles must be implemented and verified, not only documented.
- Existing Create page liquidGL work remains the primary explicit liquidGL target; this story defines and verifies the broader shared hierarchy contract around it.
- No backend persistence, API, Temporal workflow, or task submission contract changes are required.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/UI/MissionControlDesignSystem.md` 3.2 | Dense reading and editing regions stay grounded while floating, sticky, or elevated controls may use glass. | In scope | FR-001, FR-006 |
| DESIGN-REQ-004 | 3.3 | Each page should have one primary spectacle or premium-effect surface by default. | In scope | FR-008 |
| DESIGN-REQ-005 | 4 | Mission Control uses a strict surface hierarchy with matte data, satin form, glass control, liquidGL hero, and accent/live roles. | In scope | FR-001 |
| DESIGN-REQ-007 | 8.1 | The default glass system is token-driven CSS and exists even when liquidGL is disabled or unsupported. | In scope | FR-002, FR-003 |
| DESIGN-REQ-008 | 8.2-8.5 | Inner editing surfaces stay grounded; liquidGL does not become the default for `.panel` or `.card`; fallback remains coherent. | In scope | FR-004, FR-005, FR-006 |
| DESIGN-REQ-015 | 13.1-13.3 | Shared Mission Control surfaces use stable semantic class names and token-first theming. | In scope | FR-001, FR-009 |
| DESIGN-REQ-018 | 13.5 | liquidGL-enabled surfaces have CSS shell, bounded target, fallback, and elevated interactive rationale. | In scope | FR-004, FR-007 |
| DESIGN-REQ-027 | 10.5, 10.9 | Dense tables and nested dense panels use matte or near-opaque weights and preserve readability. | In scope | FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control MUST expose stable semantic surface selectors for matte data slabs, satin form surfaces, glass control surfaces, liquidGL hero surfaces, and accent/live surfaces.
- **FR-002**: Glass control surfaces MUST use token-driven translucent fill, a 1px luminous border, controlled shadow separation, and supported blur/saturation.
- **FR-003**: Glass control surfaces MUST provide a coherent near-opaque token-based fallback when backdrop filtering is unavailable.
- **FR-004**: liquidGL target surfaces MUST retain complete CSS shell layout, border, padding, shadow, and legibility when liquidGL is unavailable, disabled, or not initialized.
- **FR-005**: liquidGL MUST NOT be applied by default to dense tables, large cards, long forms, large scrolling containers, large textareas, or default `.panel`/`.card` surfaces.
- **FR-006**: Dense content, editing surfaces, and nested dense cards/panels MUST use matte, satin, or quieter near-opaque weights instead of repeated translucent glass effects.
- **FR-007**: liquidGL MUST be bounded to explicit hero/elevated target selectors that can fall back to standard CSS glass.
- **FR-008**: The shared surface contract MUST support the one-hero-effect posture by making liquidGL opt-in rather than automatic.
- **FR-009**: Automated verification MUST cover the shared surface selectors, fallback rules, bounded liquidGL target, dense-surface exclusions, and unchanged representative UI behavior.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-425 and the original preset brief.

### Key Entities

- **Surface Role**: A reusable Mission Control styling role for matte data, satin form, glass control, liquidGL hero, or accent/live UI.
- **Glass Control Surface**: A token-driven translucent elevated control shell with blur and near-opaque fallback.
- **liquidGL Hero Surface**: An explicit bounded elevated target enhanced by liquidGL while retaining a full CSS shell.
- **Dense Data Slab**: A near-opaque content surface for tables, logs, evidence, cards, and editing-heavy regions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: CSS verification confirms all five surface roles are represented by stable selectors.
- **SC-002**: CSS verification confirms glass control surfaces use shared glass tokens, 1px borders, elevation, blur/saturation, and an `@supports not` fallback.
- **SC-003**: CSS verification confirms liquidGL target styling is opt-in, has a standard CSS fallback, and default `.panel`/`.card` selectors do not initialize liquidGL.
- **SC-004**: UI tests confirm task-list data slabs and Create page liquidGL target controls remain present and behaviorally unchanged.
- **SC-005**: Traceability verification confirms MM-425 and the original Jira preset brief are preserved in MoonSpec artifacts and final evidence.

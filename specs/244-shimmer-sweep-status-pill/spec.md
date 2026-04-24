# Feature Specification: Shared Executing Shimmer for Status Pills

**Feature Branch**: `244-shimmer-sweep-status-pill`  
**Created**: 2026-04-23  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-488 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-488-moonspec-orchestration-input.md`

## Original Jira Preset Brief

```text
Jira issue: MM-488 from MM project
Summary: Attach executing shimmer as a shared status-pill modifier
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-488 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-488: Attach executing shimmer as a shared status-pill modifier

Source Reference
- Source Document: docs/UI/EffectShimmerSweep.md
- Source Title: Shimmer Sweep Effect - Declarative Design
- Source Sections:
  - Intent
  - Scope
  - Host Contract
  - State Matrix
  - Implementation Shape
  - Non-Goals
  - Hand-off Note
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-011
  - DESIGN-REQ-013
  - DESIGN-REQ-016

User Story
As a Mission Control user, I need executing status pills to opt into one shared shimmer modifier so active workflow progress is visible consistently without changing status text, icons, task row layout, or update behavior.

Acceptance Criteria
- Executing status-pill hosts can activate the shimmer through the preferred data-state/data-effect selector.
- Existing `.is-executing` hosts can activate the same shared modifier when needed.
- The modifier does not mutate host text content, casing, icon choice, task row layout, polling, or live-update behavior.
- The shared modifier is available to list, card, and detail status-pill surfaces rather than being page-local.
- Non-executing states never inherit the shimmer accidentally.

Requirements
- Provide one reusable executing-state shimmer treatment for existing status pills.
- Keep the story limited to effect activation and host integration.
- Attach without adding wrappers that change layout or pill dimensions.
- Preserve explicit non-goals for broader workflow-state UI behavior.

Relevant Implementation Notes
- Preserve MM-488 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for the shimmer effect intent, host contract, state matrix, implementation shape, and non-goals.
- Keep the work focused on activating one shared executing-state shimmer modifier for existing status-pill surfaces.
- Support both preferred data-state/data-effect selectors and existing `.is-executing` hosts where needed.
- Do not add wrappers or otherwise change status-pill layout, dimensions, text, casing, icon selection, polling behavior, or live-update behavior.
- Ensure non-executing states do not inherit the shimmer accidentally.
- Keep the shared modifier reusable across list, card, and detail status-pill surfaces rather than page-local.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-488 is blocked by MM-489, whose embedded status is Selected for Development.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable UI behavior story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectShimmerSweep.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-488 were found under `specs/`; specification is the first incomplete stage.

## User Story - Shared Executing Shimmer Modifier

**Summary**: As a Mission Control user, I want executing status pills to share one shimmer treatment so active workflow progress reads consistently anywhere that status pills appear.

**Goal**: Executing workflow state is visually distinct and consistently recognizable across list, card, and detail surfaces without changing status text, icon choices, layout, or broader workflow behavior.

**Independent Test**: Put status-pill surfaces into executing, non-executing, and reduced-motion conditions, then verify the executing state alone receives the shared shimmer treatment, reduced motion receives a non-animated active treatment, and text, icon, and layout behavior remain unchanged while MM-488 traceability is preserved.

**Acceptance Scenarios**:

1. **Given** a status pill represents the executing workflow state on any supported Mission Control surface, **When** the shared shimmer modifier is enabled, **Then** the pill shows one reusable executing treatment that communicates active progress.
2. **Given** a status pill exposes the preferred executing-state hooks or an approved existing executing marker, **When** the pill is rendered, **Then** the same shared shimmer treatment attaches without requiring a page-specific implementation.
3. **Given** a status pill is in any non-executing workflow state, **When** the surface renders, **Then** the shimmer treatment is absent.
4. **Given** the executing shimmer treatment is active, **When** the status pill renders or updates, **Then** its text content, text casing, icon choice, and surrounding layout remain unchanged.
5. **Given** reduced-motion preference is active while a status pill is executing, **When** the surface renders, **Then** the executing state still reads as active through a non-animated replacement treatment.

### Edge Cases

- Executing state appears on list, card, and detail surfaces that do not share identical markup.
- A supported pill exposes only the approved fallback executing marker rather than the preferred data attributes.
- Rapid state transitions between executing and non-executing must not leave the shimmer treatment behind.
- Reduced-motion preference can change while an executing status pill is already visible.
- Finalizing or other future-adjacent active states must not inherit the executing shimmer unless explicitly specified later.

## Assumptions

- MM-488 is limited to one shared executing-state treatment for existing status pills and does not introduce a broader status-system redesign.
- Existing Mission Control surfaces already expose enough state information to identify executing pills through the preferred hook or approved fallback marker.
- The blocker relationship to MM-489 is tracked outside this specification and does not change the scope of the requested story definition.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/UI/EffectShimmerSweep.md` Intent | The executing state effect must communicate active progress without reading as error, urgency, or instability. | In scope | FR-001, FR-007 |
| DESIGN-REQ-002 | `docs/UI/EffectShimmerSweep.md` Scope | The story covers only the executing shimmer effect and excludes unrelated status color mapping, layout changes, icon changes, and polling or live-update behavior changes. | In scope | FR-004, FR-005 |
| DESIGN-REQ-003 | `docs/UI/EffectShimmerSweep.md` Host Contract | The executing shimmer attaches to status-pill hosts as a modifier through preferred executing-state hooks and an approved fallback marker. | In scope | FR-001, FR-002 |
| DESIGN-REQ-004 | `docs/UI/EffectShimmerSweep.md` Host Contract and Design Principles | Host text remains the source of truth, text casing is preserved, and the effect must not change layout or pill dimensions. | In scope | FR-004, FR-005 |
| DESIGN-REQ-011 | `docs/UI/EffectShimmerSweep.md` Implementation Shape | The effect must remain attachable to existing status-pill markup rather than requiring a second page-local treatment. | In scope | FR-002, FR-003 |
| DESIGN-REQ-013 | `docs/UI/EffectShimmerSweep.md` Reduced Motion Behavior | Reduced-motion users must still see the executing state as active through a non-animated replacement treatment. | In scope | FR-006 |
| DESIGN-REQ-016 | `docs/UI/EffectShimmerSweep.md` State Matrix | Only the executing workflow state receives the shimmer effect; non-executing states remain off unless a future story expands the contract. | In scope | FR-003 |
| DESIGN-REQ-OUT-001 | `docs/UI/EffectShimmerSweep.md` State Matrix | Finalizing may receive an optional future variant. | Out of scope: future variant behavior is not requested by MM-488. | None |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST present one shared shimmer treatment for status pills that represent the executing workflow state.
- **FR-002**: The system MUST allow supported status-pill hosts to activate the shared shimmer treatment through the preferred executing-state selector and the approved fallback executing marker.
- **FR-003**: The system MUST ensure non-executing workflow states do not receive the executing shimmer treatment.
- **FR-004**: The system MUST preserve status-pill text content, text casing, and icon selection while the executing shimmer treatment is active.
- **FR-005**: The system MUST preserve existing status-pill layout, dimensions, and surrounding workflow update behavior when the executing shimmer treatment is applied.
- **FR-006**: The system MUST provide a non-animated executing-state treatment for reduced-motion conditions so executing still reads as active.
- **FR-007**: The system MUST ensure the executing shimmer reads as active progress rather than as warning, error, or unstable behavior.
- **FR-008**: The shared executing shimmer treatment MUST be reusable across list, card, and detail status-pill surfaces instead of being limited to one page-local implementation.
- **FR-009**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-488.

### Key Entities

- **Status Pill Surface**: A Mission Control status presentation element that communicates workflow state on list, card, or detail views.
- **Executing Shimmer Treatment**: The shared visual treatment that indicates active progress for the executing workflow state.
- **Executing-State Hook**: A supported state marker that allows a status pill to opt into the shared executing shimmer treatment.
- **Reduced-Motion Active Treatment**: A non-animated replacement presentation that still indicates the executing state when motion reduction is preferred.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Verification confirms executing status pills on each supported surface receive the same shared executing shimmer treatment.
- **SC-002**: Verification confirms both the preferred executing-state hook and the approved fallback executing marker activate the same shared treatment.
- **SC-003**: Verification confirms non-executing states do not render the executing shimmer treatment.
- **SC-004**: Verification confirms executing shimmer activation does not change status text, text casing, icon choice, layout footprint, polling behavior, or live-update behavior.
- **SC-005**: Verification confirms reduced-motion conditions use a non-animated active treatment while preserving executing-state recognition.
- **SC-006**: Verification confirms MM-488 appears in the spec, plan, tasks, verification evidence, and final implementation summary.

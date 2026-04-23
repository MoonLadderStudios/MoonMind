# Feature Specification: Calm Shimmer Motion and Reduced-Motion Fallback

**Feature Branch**: `246-animate-shimmer-motion`  
**Created**: 2026-04-23  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-490 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-490-moonspec-orchestration-input.md`

## Original Jira Preset Brief

```text
Jira issue: MM-490 from MM project
Summary: Animate shimmer motion with reduced-motion fallback
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-490 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-490: Animate shimmer motion with reduced-motion fallback

Source Reference
- Source Document: docs/UI/EffectShimmerSweep.md
- Source Title: Shimmer Sweep Effect - Declarative Design
- Source Sections:
  - Host Contract
  - Motion Profile
  - Reduced Motion Behavior
  - Acceptance Criteria
  - Non-Goals
- Coverage IDs:
  - DESIGN-REQ-007
  - DESIGN-REQ-010
  - DESIGN-REQ-012
  - DESIGN-REQ-014

User Story
As a user watching an executing workflow, I need the shimmer to move with a calm sweep cadence when motion is allowed and become a static active highlight when reduced motion is requested.

Acceptance Criteria
- The sweep travels left-to-right from the configured off-pill start to off-pill end without escaping visible rounded bounds.
- The animation cadence totals roughly 1.6 to 1.8 seconds per sweep including delay.
- The brightest moment occurs near the center of the pill rather than at either edge.
- The sweep uses soft entry and smooth fade exit with no overlap between cycles.
- When reduced motion is requested, the animated sweep is disabled and a static active highlight remains.
- The reduced-motion treatment still communicates executing as active without requiring animation for comprehension.

Requirements
- Implement the declared motion profile and pacing values.
- Respect motion-preference triggers for normal and reduced-motion behavior.
- Keep the treatment calm and activity-oriented rather than urgent or unstable.
- Preserve the same executing-only activation boundary from the host story.

Relevant Implementation Notes
- Preserve MM-490 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for the host contract, motion profile, reduced-motion behavior, acceptance criteria, and non-goals.
- Keep the shimmer cadence calm and activity-oriented rather than urgent or unstable.
- Animate the sweep left-to-right from the configured off-pill start to off-pill end while keeping it inside visible rounded bounds.
- Target a total cadence of roughly 1.6 to 1.8 seconds per sweep, including delay, with the brightest moment near the center of the pill.
- Use soft entry and smooth fade exit with no overlap between cycles.
- When reduced motion is requested, disable the animated sweep and retain a static active highlight that still communicates executing as active.
- Preserve the existing executing-only activation boundary from the host story.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-490 is blocked by MM-491, whose embedded status is Selected for Development.
- Trusted Jira link metadata at fetch time shows MM-490 blocks MM-489, whose embedded status is Code Review.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable UI behavior story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectShimmerSweep.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-490 were found under `specs/`; specification is the first incomplete stage.

## User Story - Calm Executing Shimmer Motion

**Summary**: As a user watching an executing workflow, I want the status-pill shimmer to move with a calm sweep cadence when motion is allowed and become a static highlight when motion is reduced so executing still reads as active without feeling urgent or unstable.

**Goal**: The executing status shimmer communicates active progress through a measured left-to-right sweep in normal conditions and a clear static active treatment in reduced-motion conditions, while staying bounded to the executing state only.

**Independent Test**: Render an executing status pill with normal motion and reduced-motion conditions, then verify the sweep path, timing, center-brightness, no-overlap pacing, reduced-motion fallback, and executing-only activation boundary all match the MM-490 preset brief and preserved source design requirements.

**Acceptance Scenarios**:

1. **Given** an executing status pill renders with motion allowed, **When** the shimmer sweep runs, **Then** it travels left-to-right from the configured off-pill start to off-pill end without escaping visible rounded bounds.
2. **Given** the shimmer sweep is active under normal motion, **When** one full cycle is observed, **Then** the total cadence remains roughly 1.6 to 1.8 seconds including delay, the entry is quick but soft, the exit fades smoothly, and cycles do not overlap.
3. **Given** the shimmer sweep passes across the status text, **When** the midpoint of the cycle is reached, **Then** the brightest moment occurs near the center of the pill rather than at either edge.
4. **Given** reduced motion is requested for an executing status pill, **When** the pill renders, **Then** the animated sweep is disabled and replaced with a static active highlight.
5. **Given** a user relies on reduced motion, **When** the executing pill is rendered without animation, **Then** the reduced-motion treatment still communicates the executing state as active without requiring motion for comprehension.
6. **Given** a status pill is not in the executing workflow state, **When** the surface renders, **Then** the MM-490 motion treatment remains off.

### Edge Cases

- The shimmer may enter and exit outside the visible pill bounds mathematically, but the visible effect must remain clipped to the pill's rounded shape.
- Rapid re-renders or polling updates must not cause overlapping sweep cycles or doubled animations.
- Reduced-motion preference can change while an executing pill is already visible.
- Static fallback treatment must still read as active when the pill is viewed briefly or peripherally.
- Finalizing or other future-adjacent active states do not inherit the MM-490 motion profile unless a later story expands the state contract.

## Assumptions

- MM-488 and MM-489 already define the shared executing shimmer host attachment and layered visual treatment; MM-490 is limited to the motion profile and reduced-motion fallback for that existing executing-state effect.
- The static reduced-motion highlight may be expressed through an inner highlight or subtle border emphasis as long as executing still reads as active without animation.
- The blocker relationship to MM-491 is tracked outside this specification and does not change the scope of the requested story definition.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/UI/EffectShimmerSweep.md` Host Contract | The shimmer effect attaches to executing status-pill hosts, uses the executing state as its semantic trigger, and provides a reduced-motion fallback trigger rather than expanding to unrelated states. | In scope | FR-001, FR-004, FR-005, FR-006 |
| DESIGN-REQ-010 | `docs/UI/EffectShimmerSweep.md` Motion Profile | The shimmer uses a left-to-right sweep path, soft pacing, brightest midpoint emphasis, a total cycle near 1.67 seconds including delay, and no overlap between cycles. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-012 | `docs/UI/EffectShimmerSweep.md` Reduced Motion Behavior | Reduced-motion behavior disables animation, keeps a static active highlight, and preserves comprehension of the executing state without requiring motion. | In scope | FR-004, FR-005 |
| DESIGN-REQ-014 | `docs/UI/EffectShimmerSweep.md` State Matrix and Non-Goals | The shimmer remains an executing-only treatment and does not expand into unrelated state variants or alternate effect families for this story. | In scope | FR-005 |
| DESIGN-REQ-OUT-001 | `docs/UI/EffectShimmerSweep.md` Visual Model and Theme Binding | Layer composition, theme-token binding, and text-isolation details belong to adjacent shimmer stories and are not required to complete MM-490. | Out of scope: this story focuses on motion behavior and reduced-motion fallback only. | None |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST animate the executing shimmer as a left-to-right sweep whose visible motion remains within the pill's rounded bounds.
- **FR-002**: The system MUST keep the executing shimmer cadence within roughly 1.6 to 1.8 seconds per cycle including delay, with quick-but-soft entry, smooth fade exit, and no overlap between cycles.
- **FR-003**: The system MUST place the brightest moment of the executing shimmer near the pill center during the sweep.
- **FR-004**: The system MUST disable the animated shimmer when reduced motion is requested and replace it with a static active highlight.
- **FR-005**: The system MUST ensure the reduced-motion replacement still communicates executing as an active state without requiring animation for comprehension.
- **FR-006**: The system MUST limit the MM-490 shimmer motion behavior to the executing workflow state and keep it off for other workflow states covered by the source state matrix.
- **FR-007**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-490.

### Key Entities

- **Executing Shimmer Motion Cycle**: The bounded left-to-right sweep timing and pacing contract for the executing state.
- **Reduced-Motion Active Highlight**: The static replacement treatment that communicates executing without animation.
- **Executing-State Trigger**: The semantic workflow-state condition that allows the MM-490 motion profile to activate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Verification confirms the executing shimmer visibly travels left-to-right while remaining clipped to the rounded pill bounds.
- **SC-002**: Verification confirms a complete shimmer cycle lasts roughly 1.6 to 1.8 seconds including delay, with no overlap between cycles.
- **SC-003**: Verification confirms the brightest visual emphasis occurs near the pill center rather than at either edge.
- **SC-004**: Verification confirms reduced-motion conditions disable animation and replace it with a static active highlight.
- **SC-005**: Verification confirms the reduced-motion presentation still communicates executing as active without motion.
- **SC-006**: Verification confirms non-executing states do not activate the MM-490 motion treatment.
- **SC-007**: Verification confirms MM-490 appears in the spec, plan, tasks, verification evidence, and final implementation summary.

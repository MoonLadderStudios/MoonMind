# Feature Specification: Mission Control Shared Interaction Language

**Feature Branch**: `run-jira-orchestrate-for-mm-427-align-mi-00e0a46d`  
**Created**: 2026-04-21  
**Status**: Implemented  
**Input**: Trusted Jira preset brief for MM-427 from `spec.md` (Input). Summary: "Align Mission Control components with shared interaction language." Source design: `docs/UI/MissionControlDesignSystem.md`, sections 9 and 10.

## Original Jira Preset Brief

Jira issue: MM-427 from MM project
Summary: Align Mission Control components with shared interaction language
Issue type: Story
Current Jira status at trusted fetch time: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-427 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-427: Align Mission Control components with shared interaction language

Source Reference
- Source Document: docs/UI/MissionControlDesignSystem.md
- Source Title: Mission Control Design System
- Source Sections:
  - 9. Interaction and motion
  - 10. Component system
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As an operator, I want navigation, buttons, inputs, filters, chips, overlays, and transient UI to respond consistently so controls feel precise, accessible, and operationally legible.

Acceptance Criteria
- Hover states generally increase brightness, border light, or glow rather than darkening.
- Buttons use subtle hover scale-up, active scale-down, crisp high-contrast focus-visible, disabled de-emphasis, and semantic variant colors.
- Inputs/selects/comboboxes have designed shells, grounded wells, readable labels, visible focus, intentional icons, and clear click targets.
- Active filter chips are visible, removable, and paired with an intentional reset/clear affordance.
- Status chips are translucent, bordered, compact, semantically mapped, and keep finished states stable.
- Executing/live effects are low-amplitude and removed or significantly softened under reduced motion.
- Overlays and rails use glass only where elevation improves clarity, with readable grounded inner content.

Requirements
- Implement shared interaction timing and motion model.
- Align component variants and semantic states.
- Provide accessible focus and reduced-motion behavior.
- Keep elevated/transient surfaces readable and layered.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-427 is blocked by MM-428, whose embedded status is Backlog.
- Trusted Jira link metadata also shows MM-427 blocks MM-426, which is not a blocker for MM-427 and is ignored for dependency gating.

## Classification

Single-story runtime feature request. The brief contains one independently testable UI design-system outcome: shared Mission Control controls must use the same interaction language for hover, press, focus, disabled, and compact utility states without changing page behavior.

## User Story - Shared Interaction Language

**Summary**: As a Mission Control operator, I want buttons, compact controls, filter chips, and toggles to respond with one consistent visual language so the interface feels precise and predictable across pages.

**Goal**: Mission Control shared controls use tokenized glow/grow interaction values, avoid legacy lift motion, and expose consistent focus and disabled states across primary buttons, secondary buttons, commit actions, icon buttons, filter chips, and inline toggles.

**Independent Test**: Inspect the shared Mission Control stylesheet and render existing Mission Control pages. The story passes when shared interaction tokens exist, button-like controls use scale-based hover and press states without vertical translate lift, filter chips and toggles consume the same control-shell language, focus-visible states remain high contrast, disabled controls suppress motion/glow, and existing page behavior tests still pass.

**Acceptance Scenarios**:

1. **Given** Mission Control shared CSS loads, **when** the token contract is inspected, **then** it defines reusable interaction tokens for hover scale, press scale, transition timing, focus ring, and disabled opacity.
2. **Given** primary, secondary, commit, and icon buttons are hovered or pressed, **when** their CSS interaction rules are inspected, **then** they brighten and scale through shared tokens without translate-based lift.
3. **Given** filter chips and inline toggles render in control decks or utility bars, **when** their CSS rules are inspected, **then** they use the shared control shell, border light, and focus/hover language rather than unrelated one-off styling.
4. **Given** a keyboard user focuses an interactive control, **when** focus-visible styles apply, **then** the ring is crisp, high-contrast, and based on the shared focus token.
5. **Given** a button or compact control is disabled, **when** its disabled rule applies, **then** motion and glow are suppressed while opacity is controlled by the shared disabled token.
6. **Given** existing Mission Control routes render, **when** interaction styling changes are present, **then** routing, task-list filtering, pagination, mobile cards, and shared app-shell behavior remain unchanged.

### Edge Cases

- Reduced-motion users must not receive scale or pulse interaction motion.
- Icon-only buttons must preserve clear focus states even when their visual shell differs from text buttons.
- Filter chip text must still wrap safely for long repository or status values.
- Disabled controls must not brighten on hover.

## Assumptions

- "Shared interaction language" refers to the interaction and component-system rules in `docs/UI/MissionControlDesignSystem.md`, especially sections 9 and 10.
- This story aligns shared CSS behavior and focused tests; it does not introduce a new React component library.
- Existing Create page liquid glass and task-list composition work are foundations this story consumes rather than redesigns.
- The trusted Jira preset brief for MM-427 is available in `spec.md` (Input) and is the canonical source for this runtime story.
- MM-427 has a trusted Jira dependency note showing blocker MM-428 in Backlog at fetch time; this spec records that dependency context but does not alter the already completed runtime implementation evidence.

## Requirements *(mandatory)*

- **FR-001**: The shared Mission Control stylesheet MUST define reusable interaction tokens for hover scale, press scale, transition timing, focus ring, and disabled opacity.
- **FR-002**: Primary/default buttons, secondary buttons, commit/action buttons, and icon buttons MUST use shared scale-based hover/press behavior and MUST NOT use translate-based lift for routine interaction.
- **FR-003**: Button-like anchors MUST align with the same shared interaction behavior as buttons for hover, press, and focus-visible states.
- **FR-004**: Inline toggles and compact filter/page-size controls MUST use a shared control-shell treatment with consistent border light, fill, hover, focus, and disabled posture.
- **FR-005**: Active filter chips MUST use the shared compact-control language while preserving safe wrapping for long values.
- **FR-006**: Focus-visible states for buttons, links styled as buttons, inputs, selects, textareas, icon buttons, and compact controls MUST use the shared focus ring token.
- **FR-007**: Disabled buttons and compact controls MUST suppress hover/press motion and glow while using the shared disabled opacity token.
- **FR-008**: Reduced-motion preferences MUST remove interaction scale and long-running motion effects for shared controls.
- **FR-009**: Existing Mission Control behavior for routing, task-list filters, sorting, pagination, mobile cards, and shared app-shell rendering MUST remain unchanged.
- **FR-010**: Automated verification MUST cover the interaction token contract, no-lift routine control behavior, compact-control alignment, focus/disabled rules, and unchanged existing UI behavior.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-427 and the trusted Jira preset brief.

## Key Entities

- **Interaction Token Contract**: CSS custom properties that define shared hover scale, press scale, transition timing, focus ring, and disabled opacity.
- **Routine Control**: A button, button-like anchor, icon button, inline toggle, filter chip, or compact selector that should use shared interaction language.
- **Compact Control Shell**: The shared surface treatment for chips, toggles, and small utility controls.

## Success Criteria *(mandatory)*

- **SC-001**: CSS verification confirms interaction tokens exist and are consumed by shared control rules.
- **SC-002**: CSS verification confirms routine button/control hover and press rules do not use `translateY`.
- **SC-003**: CSS verification confirms compact toggles, filters, and chips consume the shared control-shell language.
- **SC-004**: Existing shared Mission Control app-shell and task-list UI tests continue to pass.
- **SC-005**: Traceability verification confirms MM-427 and the trusted Jira preset brief are preserved in MoonSpec artifacts and final evidence.

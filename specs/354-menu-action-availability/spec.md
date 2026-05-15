# Feature Specification: Menu Action Availability and Unavailable Presentation

**Feature Branch**: `[354-menu-action-availability]`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "THOR-403: Menu Action Availability and Unavailable Presentation

## User Story
As a player, I want unavailable menu actions to be visible with clear disabled messaging so I understand what exists and why it cannot currently be used.
## Acceptance Criteria
- FTacticsMenuActionEntry or its eligibility result exposes player-facing unavailable reason text consistently.
- Generated menu buttons show disabled state and unavailable copy for ineligible actions that should remain visible.
- Online Co-op remains visible while blocked and routes to explicit unavailable feedback without travel/session side effects.
- The same generated-button behavior works for Play, Home navigation, Options, and future panels.
- Automation verifies enabled, disabled-visible, hidden-by-window, and blocked-selection behavior.
## Notes
- THOR Tactics menu work."

## User Story - Menu Action Availability and Unavailable Presentation

**Summary**: As a player, I want unavailable menu actions to remain visible with clear disabled messaging so I understand what exists and why it cannot currently be used.

**Goal**: Present menu actions consistently across generated menu panels so players can distinguish available actions, temporarily unavailable visible actions, and actions that should be hidden by the current menu window.

**Independent Test**: Can be fully tested by rendering generated menu buttons for available, disabled-visible, hidden-by-window, and blocked Online Co-op actions, then confirming the visible unavailable actions show player-facing reasons and blocked selection does not trigger travel or session side effects.

**Acceptance Scenarios**:

1. **Given** a generated menu action is eligible, **When** the menu panel renders it, **Then** the button appears enabled and selection runs the action.
2. **Given** a generated menu action is ineligible but should remain visible, **When** the menu panel renders it, **Then** the button appears disabled and shows the player-facing unavailable reason.
3. **Given** a generated menu action is outside the current visibility window, **When** the menu panel renders available actions, **Then** the action is hidden instead of shown disabled.
4. **Given** Online Co-op is currently blocked but should remain discoverable, **When** the Play menu renders generated actions, **Then** Online Co-op remains visible with explicit unavailable feedback.
5. **Given** Online Co-op is visible but blocked, **When** the player attempts to select it, **Then** the system presents unavailable feedback and does not start travel, matchmaking, session creation, or session joining.
6. **Given** Play, Home navigation, Options, or a future generated-button panel contains an ineligible visible action, **When** the panel renders generated buttons, **Then** the same disabled-visible behavior and unavailable copy are used.

### Edge Cases

- An ineligible visible action has no authored unavailable reason.
- An action changes between enabled and disabled-visible while a panel is open.
- An action is both ineligible and outside the current visibility window.
- Multiple disabled-visible actions appear on the same panel.
- A player activates a blocked action through keyboard/controller input rather than pointer input.
- A future generated-button panel uses the shared action rendering path without custom per-panel code.

## Assumptions

- "Should remain visible" is determined by the action entry or its eligibility result, while actions excluded by the current menu window remain hidden.
- Unavailable copy may come from the action entry, the eligibility result, or a defined fallback when no authored reason exists.
- Online Co-op is the required blocked-selection example for this story.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST represent menu action eligibility as enabled, disabled-visible, or hidden-by-window outcomes.
- **FR-002**: The system MUST expose player-facing unavailable reason text for disabled-visible menu actions consistently through the action entry or eligibility result.
- **FR-003**: Generated menu buttons MUST render disabled-visible actions as visible disabled controls with unavailable copy.
- **FR-004**: Generated menu buttons MUST omit actions classified as hidden by the current menu window.
- **FR-005**: Generated menu buttons MUST keep enabled actions selectable without adding unavailable messaging.
- **FR-006**: Online Co-op MUST remain visible while blocked and display explicit unavailable feedback.
- **FR-007**: Selecting blocked Online Co-op MUST NOT trigger travel, matchmaking, session creation, session joining, or other session side effects.
- **FR-008**: Play, Home navigation, Options, and future generated-button panels MUST use the same disabled-visible rendering and unavailable-copy behavior.
- **FR-009**: The system MUST provide a deterministic fallback unavailable reason when a disabled-visible action has no authored player-facing reason.
- **FR-010**: Automation MUST verify enabled, disabled-visible, hidden-by-window, and blocked-selection behavior.

### Key Entities

- **Menu Action Entry**: A configured menu action that defines identity, label, visibility intent, selection behavior, and optional unavailable reason text.
- **Eligibility Result**: The evaluated state for a menu action, including whether it is enabled, disabled-visible, or hidden by the current menu window.
- **Generated Menu Button**: The player-facing control created from a menu action entry and eligibility result.
- **Unavailable Reason**: Player-facing copy explaining why a disabled-visible action cannot currently be used.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage proves an eligible generated action appears enabled and can be selected.
- **SC-002**: Automated coverage proves an ineligible action that should remain visible appears disabled with player-facing unavailable copy.
- **SC-003**: Automated coverage proves an action hidden by the current menu window is not rendered.
- **SC-004**: Automated coverage proves blocked Online Co-op remains visible with unavailable feedback and produces zero travel or session side effects when selected.
- **SC-005**: The same generated-button test fixture proves disabled-visible behavior for Play, Home navigation, Options, and one future-panel-compatible path.

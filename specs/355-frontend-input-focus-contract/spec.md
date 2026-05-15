# Feature Specification: Frontend Input and Focus Contract

**Feature Branch**: `[355-frontend-input-focus-contract]`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "THOR-404: Frontend Input and Focus Contract

## User Story
As a controller/keyboard user, I want frontend menus to define consistent confirm, cancel/back, mouse, keyboard, and gamepad behavior so native fallback menus are fully navigable.
## Acceptance Criteria
- Shared menu base classes define default CommonUI input config for menu screens and panels.
- Generated action buttons are focusable and receive an initial focus target on activation.
- Back/cancel dismisses panels or returns to the previous frontend state where applicable.
- Mouse click and keyboard/controller confirm activate the same coordinator path.
- Focus restores when returning from Play or Options to Home.
- Automation covers initial focus, confirm activation, cancel/back, and focus restoration in native-only widgets.
## Notes
- THOR Tactics menu work."

## User Story - Frontend Input and Focus Contract

**Summary**: As a controller or keyboard user, I want frontend menus to handle confirm, cancel/back, pointer, keyboard, and gamepad input consistently so fallback menu surfaces remain fully navigable.

**Goal**: Provide a consistent input and focus contract for frontend menu screens and panels so players can navigate generated menu actions with mouse, keyboard, or controller even when final authored presentation assets are absent.

**Independent Test**: Can be fully tested by opening Home, Play, and Options menu surfaces with native fallback widgets, confirming each generated action receives usable focus, pointer and confirm inputs activate the same behavior, Back or Cancel exits appropriately, and focus returns to the originating Home action.

**Acceptance Scenarios**:

1. **Given** a frontend menu screen or panel opens, **When** it becomes active, **Then** it applies a default input behavior contract for confirm and cancel/back.
2. **Given** a generated menu action button is shown, **When** the panel becomes active, **Then** at least one actionable button receives initial focus.
3. **Given** a generated action button is focused, **When** the player activates it using keyboard confirm, controller confirm, or mouse click, **Then** each input path invokes the same menu coordinator action.
4. **Given** a dismissible panel is open, **When** the player uses Back or Cancel, **Then** the panel closes or the frontend returns to the previous state.
5. **Given** the player navigates from Home to Play and returns, **When** Home is shown again, **Then** focus returns to the Play navigation action.
6. **Given** the player navigates from Home to Options and returns, **When** Home is shown again, **Then** focus returns to the Options navigation action.
7. **Given** authored presentation assets are unavailable, **When** native fallback widgets render the menu flow, **Then** the same initial focus, confirm activation, cancel/back, and focus restoration behavior remains automated and usable.

### Edge Cases

- A panel opens with generated actions but no authored default focus target.
- A panel has no enabled generated action but still contains visible disabled actions.
- The previously focused Home action is removed or unavailable when returning from a child surface.
- The player alternates rapidly between pointer, keyboard, and controller input.
- Back or Cancel is used on a root surface where there is no previous frontend state.
- Native fallback widgets are used without final authored presentation assets.

## Assumptions

- Play and Options are the required Home child surfaces for this story.
- When the exact prior focus target no longer exists, the menu may choose the nearest valid fallback focus target rather than leaving focus unset.
- Root-surface Back or Cancel behavior may be a no-op or use the product's existing root-menu exit behavior, but it must not leave focus in an invalid state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Frontend menu screens and panels MUST define a shared default input behavior contract for confirm and cancel/back while active.
- **FR-002**: Generated action buttons MUST be focusable when they are visible and actionable by the current menu state.
- **FR-003**: Each activated menu panel MUST assign an initial focus target when at least one valid focusable generated action exists.
- **FR-004**: Keyboard confirm, controller confirm, and mouse click activation on the same generated action MUST invoke the same coordinator behavior.
- **FR-005**: Back or Cancel MUST dismiss an active child panel or return to the previous frontend state where a previous state exists.
- **FR-006**: Returning from Play to Home MUST restore focus to the Home Play navigation action when that action remains valid.
- **FR-007**: Returning from Options to Home MUST restore focus to the Home Options navigation action when that action remains valid.
- **FR-008**: If a previously focused return target is unavailable, the menu MUST choose a valid fallback focus target instead of leaving focus unset.
- **FR-009**: Native fallback widgets MUST preserve initial focus, confirm activation, cancel/back, and focus restoration behavior without requiring authored presentation assets.
- **FR-010**: Automation MUST cover initial focus, confirm activation parity, cancel/back behavior, Play-to-Home focus restoration, Options-to-Home focus restoration, and native fallback widget behavior.

### Key Entities

- **Menu Surface**: A screen or panel that participates in frontend navigation and handles confirm and cancel/back while active.
- **Generated Action Button**: A focusable control created from a menu action entry and routed through the menu coordinator when activated.
- **Focus Return Target**: The Home navigation action that should regain focus when returning from a child surface.
- **Menu Coordinator Action**: The shared behavior path invoked by pointer, keyboard, and controller activation for a generated action.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automation proves each tested native fallback menu surface assigns a valid initial focus target when a valid focusable action exists.
- **SC-002**: Automation proves mouse click, keyboard confirm, and controller confirm for the same generated action reach one shared coordinator behavior.
- **SC-003**: Automation proves Back or Cancel dismisses child panels or returns to the prior frontend state for Play and Options flows.
- **SC-004**: Automation proves returning from Play to Home restores focus to the Play navigation action in 100% of tested attempts where the action remains valid.
- **SC-005**: Automation proves returning from Options to Home restores focus to the Options navigation action in 100% of tested attempts where the action remains valid.
- **SC-006**: Automation proves native fallback widgets satisfy initial focus, confirm activation, cancel/back, and focus restoration without authored presentation assets.

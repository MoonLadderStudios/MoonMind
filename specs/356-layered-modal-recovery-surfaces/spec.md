# Feature Specification: Layered Modal Recovery Surfaces

**Feature Branch**: `[356-layered-modal-recovery-surfaces]`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "THOR-405: Layered Modal Confirmation and Recovery Surfaces

## User Story
As a player, I want progress, error, retry, dismiss, and confirmation modals to behave consistently through the frontend layer stack so failure recovery is predictable.
## Acceptance Criteria
- Modal presentation uses UI.Layer.Modal through the UI manager/layout stack in production paths.
- Progress, error, retry, dismiss, and confirmation states share a native modal base or equivalent reusable implementation.
- Retry executes captured recovery action when available.
- Dismiss returns to Home unless an explicit prior state is configured.
- Native fallback modals work without Blueprint subclasses.
- Automation covers progress modal, blocking error modal, retry, dismiss, and layer push/dismiss behavior.
## Notes
- THOR Tactics menu work."

## User Story - Layered Modal Recovery Surfaces

**Summary**: As a player, I want progress, error, retry, dismiss, and confirmation modals to behave consistently through the frontend layer stack so failure recovery is predictable.

**Goal**: Provide consistent modal presentation and recovery behavior for frontend progress, blocking error, retry, dismiss, and confirmation flows so players can recover from interrupted or failed actions without unpredictable navigation outcomes.

**Independent Test**: Can be fully tested by triggering progress, blocking error, retry, dismiss, and confirmation modal flows through the frontend runtime, confirming each modal appears through the modal layer, shares consistent base behavior, executes recovery or navigation outcomes correctly, and can be pushed and dismissed through the layer stack.

**Acceptance Scenarios**:

1. **Given** a production frontend flow needs to show a progress state, **When** the progress modal is presented, **Then** it appears through the modal layer and blocks conflicting interaction until dismissed or replaced by the next outcome.
2. **Given** a production frontend flow encounters a blocking error, **When** the error modal is presented, **Then** it appears through the modal layer with the same base interaction behavior used by other modal states.
3. **Given** a retryable failure captures a recovery action, **When** the player chooses Retry, **Then** the captured recovery action is executed exactly once for that retry attempt.
4. **Given** a modal is dismissed without an explicit prior state, **When** the player chooses Dismiss, **Then** the frontend returns to Home.
5. **Given** a modal is dismissed with an explicit prior state configured, **When** the player chooses Dismiss, **Then** the frontend returns to that prior state instead of Home.
6. **Given** a confirmation modal asks the player to confirm or cancel an action, **When** the player chooses either option, **Then** the selected outcome is routed consistently and the modal leaves the layer stack.
7. **Given** authored presentation assets are unavailable, **When** native fallback modals are used, **Then** progress, error, retry, dismiss, and confirmation flows remain usable without authored subclasses.
8. **Given** modal flows are exercised by automation, **When** progress, blocking error, retry, dismiss, and layer push/dismiss behaviors run, **Then** each required behavior is verified independently.

### Edge Cases

- A retryable modal is shown without a captured recovery action.
- A player dismisses a modal while no explicit prior state is configured.
- A player dismisses a modal while an explicit prior state is configured.
- A modal is presented while another modal is already active in the layer stack.
- Authored modal presentation assets are unavailable and native fallback surfaces must be used.
- A recovery action completes, fails again, or is no longer valid when Retry is selected.

## Assumptions

- Home is the safe default return state when no explicit prior state is configured.
- A retry option may be unavailable or disabled when no captured recovery action exists, but choosing Retry when available must never execute an undefined action.
- Confirmation modals have two or more explicit outcomes, and every selected outcome must close the modal consistently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Production frontend modal presentation MUST route progress, blocking error, retry, dismiss, and confirmation modal states through the modal layer in the frontend layer stack.
- **FR-002**: Progress, blocking error, retry, dismiss, and confirmation modal states MUST share a consistent native modal base or equivalent reusable behavior contract.
- **FR-003**: Progress modals MUST prevent conflicting player interaction while the progress state is active.
- **FR-004**: Blocking error modals MUST communicate that the current flow cannot continue without player acknowledgement or recovery.
- **FR-005**: Retry-capable modals MUST execute the captured recovery action when one is available and the player chooses Retry.
- **FR-006**: Retry-capable modals MUST avoid executing an undefined recovery action when no captured recovery action is available.
- **FR-007**: Dismiss behavior MUST return the player to Home when no explicit prior state is configured.
- **FR-008**: Dismiss behavior MUST return the player to the configured prior state when an explicit prior state exists.
- **FR-009**: Confirmation modals MUST route each player-selected outcome consistently and leave the modal layer stack after the outcome is handled.
- **FR-010**: Native fallback modals MUST support progress, blocking error, retry, dismiss, and confirmation behavior without requiring authored presentation subclasses.
- **FR-011**: Modal push and dismiss behavior MUST preserve layer stack consistency when a modal is added, replaced, or removed.
- **FR-012**: Automation MUST cover progress modal behavior, blocking error modal behavior, retry with a captured recovery action, dismiss to Home, dismiss to an explicit prior state, confirmation outcome handling, native fallback behavior, and layer push/dismiss behavior.

### Key Entities

- **Modal State**: A player-facing frontend interruption state for progress, blocking error, retry, dismiss, or confirmation.
- **Recovery Action**: A captured action that can be executed when the player chooses Retry after a recoverable failure.
- **Prior State**: The frontend destination that should be restored when a modal is dismissed, when explicitly configured.
- **Home State**: The default frontend destination used when a modal is dismissed without an explicit prior state.
- **Modal Layer Stack**: The active frontend layer stack area that presents modal surfaces and tracks their push and dismiss behavior.
- **Native Fallback Modal**: A runtime-provided modal surface that remains usable when authored presentation assets are unavailable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automation proves progress modals present through the modal layer and prevent conflicting interaction in 100% of tested progress flows.
- **SC-002**: Automation proves blocking error modals present through the modal layer and use the shared modal behavior contract in 100% of tested blocking error flows.
- **SC-003**: Automation proves Retry executes the captured recovery action exactly once per selected retry attempt in 100% of tested retryable failure flows.
- **SC-004**: Automation proves Dismiss returns to Home in 100% of tested flows where no explicit prior state is configured.
- **SC-005**: Automation proves Dismiss returns to the configured prior state in 100% of tested flows where an explicit prior state is configured.
- **SC-006**: Automation proves native fallback modals support progress, blocking error, retry, dismiss, and confirmation behavior without authored subclasses.
- **SC-007**: Automation proves modal push and dismiss operations leave the layer stack in the expected state after each tested modal interaction.

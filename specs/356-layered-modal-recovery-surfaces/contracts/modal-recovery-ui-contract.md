# UI Interaction Contract: Layered Modal Recovery Surfaces

## Scope

This contract defines the player-visible behavior for THOR-405 frontend modal recovery surfaces. It applies to progress, blocking error, retry, dismiss, and confirmation modal states presented through the frontend modal layer, including native fallback modals used without authored presentation subclasses.

## Modal Layer Presentation

Production modal presentation must route through the frontend modal layer stack. A modal shown for progress, blocking error, retry, dismiss, or confirmation must:
- enter the modal layer rather than bypassing the UI manager/layout stack;
- become the top input target while active;
- leave the modal layer when dismissed, replaced, or completed.

## Shared Modal Behavior

All required modal states must share a native modal base or equivalent reusable behavior contract for:
- player-facing message/status content;
- modal action registration;
- dismiss handling;
- layer push and dismiss semantics;
- native fallback rendering.

State-specific content and available actions may differ, but stack and input behavior must remain consistent.

## Progress and Blocking Error

Progress modals must block conflicting interaction while active.

Blocking error modals must indicate that the current flow cannot continue without acknowledgement or recovery. If the error is retryable, Retry behavior follows the recovery action contract.

## Retry

When Retry is visible and selected:
- a captured recovery action must exist;
- the captured action executes exactly once for that selected retry attempt;
- the modal state must not execute an undefined or stale action.

When no captured recovery action exists, Retry must be unavailable or safely non-executable.

## Dismiss

Dismiss behavior must navigate deterministically:
- if an explicit prior state is configured, return to that prior state;
- otherwise, return to Home.

Dismiss must also remove the active modal from the modal layer stack.

## Confirmation Outcomes

Confirmation modals must expose explicit player outcomes such as confirm and cancel. Selecting an outcome must:
- route the selected outcome through one consistent result path;
- remove the modal from the modal layer stack;
- avoid leaving stale modal input capture behind.

## Native Fallback

Native fallback modals must satisfy the same behavior contract without authored presentation subclasses. Authored assets may replace presentation, but they must not be required for progress, blocking error, retry, dismiss, confirmation, or layer push/dismiss behavior.

## Acceptance Evidence

The contract is satisfied when automated coverage demonstrates:
- progress modal layer presentation and interaction blocking;
- blocking error modal layer presentation and shared behavior;
- Retry executes a captured recovery action exactly once;
- Dismiss returns to Home without a prior state;
- Dismiss returns to an explicit prior state when configured;
- confirmation outcomes route and close consistently;
- native fallback modals satisfy the full behavior contract;
- modal push and dismiss operations leave the expected layer stack state.

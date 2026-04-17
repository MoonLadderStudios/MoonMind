# Contract: Create Button Right Arrow

## Primary Submit Action

The Create Page primary submit action must:

- remain the single explicit action that submits a create-mode task draft
- display a right-pointing arrow as part of its visible presentation
- retain an accessible action name that communicates Create or task creation
- preserve existing enabled, disabled, validation, and loading behavior
- remain colocated with the existing create/edit/rerun submit action area at the bottom of the shared Steps card

## Submission Behavior

Activating the primary submit action with a valid draft must continue to submit through the existing task creation path.

The arrow presentation must not:

- create tasks implicitly
- alter task payload shape
- bypass validation
- change Jira import behavior
- change preset behavior
- change dependency selection behavior
- change runtime, attachment, or publish controls

## Responsive And State Behavior

The primary submit action must fit without text or arrow overlap in representative desktop and mobile-width layouts.

Disabled and loading states must remain recognizable as the same Create action and must not resize the surrounding layout in a way that obscures adjacent controls.

## Verification Contract

Focused UI coverage should verify:

- the visible right-pointing arrow exists on the primary Create action
- the action remains reachable by its Create-oriented accessible name
- existing submit behavior still posts a valid create request
- disabled or validation behavior remains consistent with the pre-existing Create Page flow

Full implementation verification should also run the repo's required unit test runner and the hermetic integration test runner when available.

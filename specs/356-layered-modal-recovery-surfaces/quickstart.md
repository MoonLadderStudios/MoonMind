# Quickstart: Layered Modal Recovery Surfaces

## Preconditions

- Use the THOR Tactics repository for implementation. The current MoonMind managed workspace does not contain the target Unreal/game source files.
- Confirm that the proposed target paths in `plan.md` match the THOR repository, or adapt them to established equivalent frontend/modal modules before writing tests.
- Preserve the Jira issue key `THOR-405` and the spec path `specs/356-layered-modal-recovery-surfaces/spec.md` in implementation notes and final verification.
- Run the story with authored modal presentation assets disabled or absent for native fallback modal coverage.

## TDD Sequence

1. Write failing unit tests for shared modal state policy and modal layer routing.
2. Write failing unit tests for progress interaction blocking and blocking error behavior.
3. Write failing unit tests for retry recovery action execution and absent-action guardrails.
4. Write failing unit tests for dismiss destination selection and confirmation outcome routing.
5. Write failing integration/automation tests for progress, blocking error, retry, dismiss, confirmation, native fallback, and layer push/dismiss flows.
6. Implement the smallest code changes needed to pass the tests.
7. Re-run unit and integration automation and capture evidence for `/moonspec-verify`.

## Unit Test Strategy

Target command: use the THOR repository's standard unit/automation test runner for non-rendered modal state policy, recovery action, destination selection, confirmation routing, and modal stack tests.

Required unit coverage:
- Modal presentation routes through the modal layer abstraction.
- Required modal states share common modal behavior.
- Progress modal blocks conflicting interaction.
- Blocking error modal exposes acknowledgement or recovery behavior.
- Retry executes a captured recovery action exactly once per selected retry attempt.
- Retry does not execute when no captured recovery action exists.
- Dismiss resolves to Home or explicit prior state.
- Confirmation outcomes route consistently and close the modal.
- Modal push/dismiss stack invariants hold after add, replace, and remove operations.

## Integration Test Strategy

Target command: use the THOR repository's standard game automation test runner for player-visible native fallback modal flows.

Required integration coverage:
- Progress modal presents through the modal layer and blocks conflicting interaction.
- Blocking error modal presents through the modal layer.
- Retry executes the captured recovery action in a retryable failure flow.
- Dismiss returns to Home when no explicit prior state exists.
- Dismiss returns to an explicit prior state when configured.
- Confirmation confirm and cancel outcomes both close the modal and route expected results.
- Authored presentation subclasses absent does not break native fallback modal behavior.
- Modal push and dismiss operations leave the expected layer stack state.

## End-to-End Verification

The story is ready for final verification when:
- all required unit tests pass;
- modal automation passes for progress, blocking error, retry, dismiss, confirmation, native fallback, and stack behavior;
- Retry execution count evidence shows exactly one recovery action execution per selected retry attempt;
- dismiss destination evidence covers both Home default and explicit prior state;
- native fallback modals remain usable without authored presentation subclasses.

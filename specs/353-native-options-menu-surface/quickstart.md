# Quickstart: Native Options Menu Surface

## Preconditions

- Use the THOR Tactics repository for implementation. The current MoonMind managed workspace does not contain the target Unreal/game source files.
- Preserve the Jira issue key `THOR-402` and the spec path `specs/353-native-options-menu-surface/spec.md` in implementation notes and final verification.
- Do not add settings persistence in this story.

## TDD Sequence

1. Write failing unit tests for Options category resolution:
   - authored data provides Video, Audio, and Input categories;
   - empty authored data falls back to Video, Audio, and Input;
   - partial authored data fills only missing required categories from fallback entries.
2. Write failing unit tests for navigation state:
   - Home Options action opens Options;
   - Back returns to Home;
   - Cancel returns to Home;
   - focus target after return is the Home Options action.
3. Write a failing integration/automation test for Home -> Options -> Back using only the baseline surface.
4. Implement the smallest code changes needed to pass the tests.
5. Re-run the unit and integration tests and capture evidence for `/speckit.verify`.

## Unit Test Strategy

Target command: use the THOR repository's standard unit/automation test runner for non-rendered menu model and navigation-state tests.

Required unit coverage:
- Stable identifiers: `frontend.nav.options`, `frontend.options.video`, `frontend.options.audio`, `frontend.options.input`.
- Category list construction from authored data.
- Fallback category list construction with missing authored data.
- No settings persistence dependency.
- Back/Cancel state transitions and return focus target.

## Integration Test Strategy

Target command: use the THOR repository's standard game automation test runner for player-visible menu flows.

Required integration coverage:
- Start at Home.
- Activate Options.
- Confirm the Options surface opens without final authored Options presentation assets.
- Confirm Video, Audio, and Input category actions are visible.
- Trigger Back or Cancel.
- Confirm the view returns to Home.
- Confirm focus is restored to the Options navigation action.

## End-to-End Verification

The story is ready for final verification when:
- all required unit tests pass;
- the Home -> Options -> Back automation passes using the baseline surface;
- no settings persistence behavior is introduced;
- missing authored Options assets/data still produce a usable menu.

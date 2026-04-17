# Quickstart: Create Button Right Arrow

## Focused Unit Checks

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:

- The Create Page primary submit action displays a right-pointing arrow.
- The primary submit action remains available by a Create-oriented accessible name.
- Disabled or validation behavior remains unchanged when the primary step is incomplete.
- A valid create-mode draft still submits through the configured task creation endpoint.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected coverage:

- Existing Python and frontend unit tests continue to pass.
- The Create Page change does not regress Jira, preset, dependency, runtime, attachment, or publish controls covered by the unit suite.

## Hermetic Integration Verification

```bash
./tools/test_integration.sh
```

Expected coverage:

- Required `integration_ci` checks continue to pass in an environment with Docker access.
- No provider credentials are required.

If Docker is unavailable in the managed-agent container, record the exact blocker and rely on focused unit evidence plus final unit-suite evidence for local verification.

## End-To-End Story Check

1. Open the Create Page in create mode.
2. Confirm the primary Create action visibly includes a right-pointing arrow.
3. Confirm the action is still announced as Create or task creation by its accessible name.
4. Submit a valid draft and confirm the existing task creation flow is used.
5. Resize to a representative mobile width and confirm the action text and arrow do not overlap adjacent content.

## Moon Spec Helper

```bash
SPECIFY_FEATURE=203-create-button-right-arrow .specify/scripts/bash/check-prerequisites.sh --json
```

Use the `SPECIFY_FEATURE` prefix when running helper scripts from managed branches that do not use the numbered feature-branch form.

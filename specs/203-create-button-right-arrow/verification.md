# Verification: Create Button Right Arrow

**Feature**: Create Button Right Arrow  
**Jira issue**: MM-390  
**Date**: 2026-04-17  
**Status**: Implementation evidence recorded; final MoonSpec verification pending

## TDD Evidence

Red-first focused run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Result: FAIL before production implementation, with four expected failures for missing `data-submit-arrow="right"` assertions in `frontend/src/entrypoints/task-create.test.tsx`.

Focused frontend verification after implementation:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Result: PASS, 154 tests passed.

Full unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Result: PASS for Python unit suite reported by the wrapper: 3531 passed, 1 xpassed, 16 subtests passed.

## Integration Verification

Hermetic integration command:

```bash
./tools/test_integration.sh
```

Result: NOT RUN in this managed container because Docker is unavailable.

Docker blocker:

```text
failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is correct and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory
```

## Requirement Evidence

- FR-001, FR-002, SC-001: `frontend/src/entrypoints/task-create.tsx` renders a create-mode right arrow with `data-submit-arrow="right"` and focused tests assert the visible arrow marker.
- FR-003, FR-004, DESIGN-REQ-002, DESIGN-REQ-003, SC-005: existing create submit request-shape coverage still passes after the presentation change.
- FR-005: focused tests verify authoring controls remain available and the full unit suite passed.
- FR-006, SC-004: implementation adds a no-wrap inline-flex Create action presentation; final real viewport evidence remains part of `/moonspec-verify` or manual story validation.
- FR-007, SC-003: the arrow is `aria-hidden`, preserving the accessible button name `Create`.
- FR-008: focused Create Page tests cover the MM-390 behavior.
- FR-009, SC-006: MM-390 is preserved in `spec.md`, `tasks.md`, this verification file, and should be preserved in commit and PR metadata.

## Remaining Verification Work

- Run `/moonspec-verify` after final implementation review.
- Run `./tools/test_integration.sh` in an environment with Docker access if required before closure.

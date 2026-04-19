# Quickstart: Liquid Glass Publish Panel

## Prerequisites

- Local Node/npm dependencies installed by the standard unit test runner when needed.
- Managed-agent local test mode enabled with `MOONMIND_FORCE_LOCAL_TESTS=1`.

## Test-First Validation

1. Add or update focused Create page tests in `frontend/src/entrypoints/task-create.test.tsx` before production styling changes.
2. Cover the bottom task submission controls group and assert it contains GitHub Repo, Branch, Publish Mode, and the Create action.
3. Cover the liquid glass panel treatment through stable class or computed-style assertions that prove the target panel receives the intended treatment.
4. Cover unchanged create behavior by preserving or extending request-shape assertions for a valid draft submission.
5. Run the focused UI test command:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

6. Before finalizing the story, run the full unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Visual Verification

1. Open the Create Page at `/tasks/new`.
2. Confirm the bottom panel containing GitHub Repo, Branch, Publish Mode, and Create task controls has visible liquid glass blur and refractive depth.
3. Confirm all labels, values, icons, branch status messages, and the create action remain readable.
4. Confirm the panel fits without overlap or clipped text at a desktop-width viewport.
5. Confirm the panel fits without overlap or clipped text at a mobile-width viewport.
6. Repeat readability checks in light and dark appearance settings.
7. Enter or load a long repository name and a long branch name, then confirm the panel remains stable and text does not overlap adjacent controls.
8. Check branch loading, empty, failed, disabled, and stale states when available, then confirm status text remains readable inside or near the panel.
9. Check a publish-mode-constrained task or skill when available, then confirm the panel remains stable when publish options are limited or reset.

## Integration Notes

- No compose-backed integration suite is required for this story because the planned change is a browser UI styling and behavior-preservation change.
- Existing task submission payload semantics remain the integration boundary to verify in focused Create page tests.

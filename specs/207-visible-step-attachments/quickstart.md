# Quickstart: Visible Step Attachments

## Focused Red-First Test

Run the focused Create page tests while developing:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected before implementation: newly added MM-410 tests fail because the step attachment control is a generic visible file input and file selection replaces instead of appends.

Expected after implementation: focused Create page tests pass.

## Story Validation

1. Enable attachment policy in the test payload.
2. Render the Create page.
3. Verify each step has a compact + button named `Add images to Step 1` for image-only policy.
4. Select a file for Step 1 through the hidden input associated with the + button.
5. Select another file for Step 1 and verify both files remain visible.
6. Select an exact duplicate and verify the duplicate is not rendered twice.
7. Add another step, select a same-named file for Step 2, reorder steps, and verify target ownership remains stable.
8. Submit and verify files upload before `/api/executions`, with refs under each owning step only.

## Final Unit Verification

Run the repository unit suite:

```bash
./tools/test_unit.sh
```

If frontend dependencies are missing or stale, the script is expected to prepare them according to repo instructions.

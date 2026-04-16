# Quickstart: Edit Task Shows All Steps

## Focused Validation

1. Prepare frontend dependencies if needed:

```bash
npm ci --no-fund --no-audit
```

2. Run the focused task-create tests:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

3. Run the project unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Manual Scenario

1. Open an editable `MoonMind.Run` that has at least three task steps.
2. Click Edit from the task detail page.
3. Confirm the Edit Task page shows Step 1, Step 2, and Step 3 separately.
4. Save without modifying Step 2 or Step 3.
5. Confirm the update request still includes later step payloads.

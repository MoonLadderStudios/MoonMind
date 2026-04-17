# Quickstart: Preset Application and Reapply State

## Focused Validation

Run the focused Create page test file:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## Story Scenarios

1. Open the Create page with the task template catalog enabled.
2. Select a preset and verify steps do not change until Apply is clicked.
3. With only the empty default step present, click Apply and verify expanded preset steps replace the placeholder.
4. Add authored step content, click Apply, and verify expanded preset steps append.
5. Enter Feature Request / Initial Instructions and verify task objective and derived title use that text.
6. After Apply, change objective text or objective-scoped attachments and verify the UI shows Reapply preset without changing expanded step content.
7. Edit or import into a template-bound step instruction or attachment target and verify submitted step payload no longer preserves stale template identity.

## Final Validation

Run the full unit suite before completion when feasible:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

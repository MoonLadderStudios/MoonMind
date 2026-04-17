# Quickstart: Step-First Draft and Attachment Targets

## Focused UI Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected:
- Create page tests pass.
- Objective-scoped images submit through `task.inputAttachments`.
- Step-scoped images submit through owning `task.steps[n].inputAttachments`.
- Reordered steps keep their selected attachments.
- Attachment refs are not appended to instruction text.

## Full Unit Validation

```bash
./tools/test_unit.sh
```

Expected:
- Python unit tests and frontend unit tests pass through the repository runner.

## Manual Story Check

1. Open `/tasks/new` with attachment policy and task presets enabled.
2. Add primary instructions or select an explicit skill; confirm Step 1 is labeled Primary.
3. Add an objective-scoped image under `Feature Request / Initial Instructions`.
4. Add at least two steps and attach different images to each step.
5. Reorder the steps.
6. Submit the draft.
7. Confirm the execution create payload contains objective refs in `task.inputAttachments`, step refs in the owning `task.steps[n].inputAttachments`, and no generated attachment block in instruction text.

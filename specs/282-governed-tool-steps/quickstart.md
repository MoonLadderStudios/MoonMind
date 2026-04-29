# Quickstart: Governed Tool Step Authoring

1. Open Mission Control Create page.
2. Set Step Type to `Tool`.
3. Enter Tool `jira.get_issue`.
4. Enter Tool Version `1.0`.
5. Enter Tool Inputs:

```json
{"issueKey":"MM-563"}
```

6. Submit the task.
7. Confirm the submitted payload has `task.steps[0].type == "tool"`, includes `tool.id`, optional `tool.version`, and object `tool.inputs`, and omits `skill`.
8. Repeat with invalid Tool Inputs such as `[` and confirm submission is blocked before the execution request.

Validation commands:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
pytest tests/unit/workflows/tasks/test_task_contract.py -q
./tools/test_unit.sh
```

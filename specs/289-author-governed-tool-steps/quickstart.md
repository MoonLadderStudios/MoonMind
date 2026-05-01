# Quickstart: Author Governed Tool Steps

1. Run the frontend focused test:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

2. Run the existing task contract unit test:

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py -q
```

3. Manual verification:
- Open the Create page.
- Set Step Type to Tool.
- Confirm discovered tools are grouped and searchable when `/mcp/tools` is available.
- Select `jira.transition_issue`.
- Enter Tool Inputs with `{"issueKey":"MM-576"}`.
- Choose a returned target status.
- Submit and confirm the payload is a `type: tool` step with Tool inputs and no Skill payload.

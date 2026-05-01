# Quickstart: Author Agentic Skill Steps

## Focused Backend Validation

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q
```

Expected result: task contract and task-template validation accept valid Skill payload shapes and reject mixed Skill/Tool payloads.

## Focused Create-Page Validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected result: the Create-page target passes, including the MM-577 Skill-step regression, invalid Skill Args coverage, and adjacent Tool/Preset authoring coverage.

## End-To-End Story Check

1. Open the task Create page.
2. Configure the primary step as Skill work with `moonspec-orchestrate`.
3. Enter Skill Args JSON containing `{"issueKey":"MM-577","mode":"runtime"}`.
4. Enter required capabilities `git, jira`.
5. Submit and confirm the payload preserves `type: skill`, `task.skill.id`, Skill args, and required capabilities.
6. Enter invalid Skill Args JSON and confirm submission is blocked before execution.

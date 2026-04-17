# Quickstart: Targeted Image Attachment Submission

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py
```

Expected:
- Valid objective and step `inputAttachments` refs are accepted.
- Missing compact metadata, raw byte/content fields, and image data URLs are rejected.
- `/api/executions` forwards valid refs into `MoonMind.Run` initial parameters.

## Contract Validation

```bash
./tools/test_unit.sh tests/contract/test_temporal_execution_api.py
```

Expected:
- Task-shaped execution create succeeds with objective and step refs.
- The original task input snapshot artifact preserves `task.inputAttachments` and `task.steps[n].inputAttachments`.

## Full Unit Suite

```bash
./tools/test_unit.sh
```

Expected:
- Required unit suite passes before `/moonspec-verify`.

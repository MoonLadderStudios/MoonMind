# Quickstart: Submit Discriminated Executable Payloads

## Focused Validation

Run backend validation tests:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

Run focused Create page tests:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Run final unit verification:

```bash
./tools/test_unit.sh
```

## Manual Payload Checks

1. Submit or validate a task with a step shaped as `type: "tool"` and a `tool` sub-payload.
2. Confirm the runtime plan node uses the typed tool id/name and does not expose Activity as the Step Type.
3. Submit or validate a task with a step shaped as `type: "skill"` and a `skill` sub-payload.
4. Confirm the runtime plan node uses the selected agent-facing skill.
5. Submit or validate a task with `type: "preset"` and confirm it is rejected before runtime materialization.
6. Confirm preset-applied Create-page submissions include flattened steps with `type: "tool"` or `type: "skill"` and retain optional provenance metadata when present.

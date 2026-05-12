# Quickstart: Compile-Time Preset Composition With Provenance Preservation

## Focused Validation

Run backend catalog, contract, worker, and API coverage:

```bash
python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/api/routers/test_executions.py -q
```

Run focused Create page coverage:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Run focused hermetic integration coverage:

```bash
python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci
```

Run required final suites when available:

```bash
./tools/test_unit.sh
./tools/test_integration.sh
```

## Manual Traceability Check

1. Confirm `spec.md` preserves `MM-642` and the canonical Jira preset brief.
2. Confirm `plan.md`, `research.md`, `data-model.md`, this quickstart, contract, tasks, and verification evidence reference `MM-642`.
3. Confirm source coverage IDs `DESIGN-REQ-010` and `DESIGN-REQ-011` map to compile-time composition and provenance durability.
4. Confirm manual-only submission tests still show no fabricated preset metadata.

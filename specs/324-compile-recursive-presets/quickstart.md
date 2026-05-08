# Quickstart: Compile Recursive Task Presets

## Focused Unit Validation

Run focused backend tests while implementing catalog, task contract, and API boundary changes:

```bash
python -m pytest tests/unit/api/test_task_step_templates_service.py -q
python -m pytest tests/unit/workflows/tasks/test_task_contract.py -q
python -m pytest tests/unit/api/routers/test_executions.py -q
```

If Create page submission payload behavior changes, run focused frontend tests through the repo test runner:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## Required Unit Suite

```bash
./tools/test_unit.sh
```

## Hermetic Integration Validation

Run the required integration suite after adding the task submission/execution boundary coverage:

```bash
./tools/test_integration.sh
```

## End-to-End Story Check

1. Create a task draft with a root preset that includes a child preset and at least one manual step.
2. Submit the task and confirm recursive includes are validated before execution finalization.
3. Confirm the submitted task has one deterministic flattened step order.
4. Confirm `authoredPresets`, `appliedStepTemplates` composition metadata, and `steps[].source` preserve reliable preset provenance.
5. Simulate live preset catalog change or unavailability after submission.
6. Confirm the worker-facing task still contains resolved executable steps and reconstruction uses submitted snapshot metadata, not live catalog lookup.
7. Confirm a manual-only task still submits without fabricated preset metadata.
8. Confirm `MM-630` remains preserved in MoonSpec artifacts and final verification output.

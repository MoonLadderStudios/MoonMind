# Verification: Compile Recursive Task Presets

## Scope

Implementation scope remains the single MM-630 story from `spec.md`: compile recursive task presets before execution, preserve compact provenance, and keep manual-only task behavior unchanged.

## TDD Evidence

- Red-first backend unit check was reconstructed in `/tmp/mm630-redfirst` by applying only the test diff to `HEAD`.
- Command: `python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py -q`
- Expected failures observed before production changes:
  - `KeyError: 'authoredPresets'` for recursive catalog expansion metadata.
  - `DID NOT RAISE ValidationError` for unresolved preset include work in task steps.
  - `KeyError: 'authoredPresets'` for worker runtime expanded seeded template metadata.
- The hermetic integration test diff passed against `HEAD` before production changes, so that coverage is regression evidence rather than red-first evidence.

## Passing Checks

- `python -m pytest tests/unit/api/routers/test_executions.py -q` -> 170 passed.
- `python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/api/routers/test_executions.py -q` -> 314 passed.
- `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci` -> 7 passed.
- `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` -> Python suite 4573 passed, 1 xpassed, 16 subtests passed; Create page target 30 passed, 223 skipped.
- `./tools/test_unit.sh` -> Python suite 4575 passed, 1 xpassed, 16 subtests passed; frontend suite 20 files passed, 324 tests passed, 223 skipped.

## Blocked Checks

- `./tools/test_integration.sh` did not run to completion in this environment. Docker returned `403 Forbidden` after reporting the buildx plugin warning while building the `repo-pytest` image.
- Provider verification is not required for MM-630. The story covers local task preset compilation, task contract normalization, frontend submission shape, and Temporal task-shaped submission boundaries without credentialed external providers.

## Requirement Coverage

- FR-001, FR-003, FR-004, FR-006: catalog expansion now emits deterministic flattened steps, compact composition metadata, authored preset bindings, and per-step source provenance.
- FR-002: existing catalog validation continues to reject invalid include trees, including missing targets and duplicate aliases, with explicit errors.
- FR-005: task contract and worker runtime coverage prevent unresolved preset include work from reaching worker-facing task steps.
- FR-007: route, integration, and frontend coverage preserve manual-only behavior without fabricated preset metadata.
- FR-008: MM-630 and the original Jira preset brief remain preserved in the MoonSpec artifacts.

## Remaining Verification

- Final `/moonspec-verify` was not run in this implementation step.
- Full required integration remains blocked by the local Docker administrative policy described above.

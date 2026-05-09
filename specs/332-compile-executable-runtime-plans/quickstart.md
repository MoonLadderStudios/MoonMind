# Quickstart: Compile Executable Steps into Runtime Plans

## Preconditions

- Work from `specs/332-compile-executable-runtime-plans/spec.md`.
- Preserve `MM-573`, source issue `manual-mm-569-mm-574`, and the original preset brief in downstream artifacts and delivery metadata.
- Use test-first implementation. Add failing unit or integration tests before changing production code when a requirement is not already verified.

## Focused Unit Verification

Run focused tests while iterating:

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/task_proposals/test_service.py -q
```

Expected coverage:

- `TaskExecutionSpec` rejects unresolved `preset` and include work.
- Runtime planner maps explicit Tool steps to typed plan nodes.
- Runtime planner maps explicit Skill steps to acceptable runtime materialization inputs.
- Proposal promotion rejects unresolved preset steps.
- Proposal promotion preserves reviewed flattened payload and preset provenance without live re-expansion.

## Full Unit Verification

Before completing implementation, run:

```bash
./tools/test_unit.sh
```

## Hermetic Integration Verification

Run when API/execution-boundary behavior changes:

```bash
./tools/test_integration.sh
```

Focused integration target when iterating locally:

```bash
pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q
```

Expected coverage:

- Submitted task-shaped payloads preserve flattened executable steps.
- Preset-derived source/authored metadata remains compact and durable.
- Manual-only submissions do not gain fabricated preset metadata.

## End-to-End Story Check

1. Build or select a reviewed task payload derived from a preset.
2. Confirm durable `task.steps` contains only `tool` and `skill` step types.
3. Confirm each Tool step maps to a typed tool plan node.
4. Confirm each Skill step maps to an accepted runtime materialization path without changing the Step Type contract.
5. Confirm preset provenance is present for audit but no live preset catalog lookup is required to execute.
6. Confirm promotion validates the reviewed payload and rejects unresolved Preset steps.
7. Confirm final verification compares behavior against the preserved MM-573 Jira preset brief.

## Expected Next Step

After this plan is accepted, run `/speckit.tasks` or the active `moonspec-tasks` workflow to generate TDD-first tasks from `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-step-plan-contract.md`, and this quickstart.

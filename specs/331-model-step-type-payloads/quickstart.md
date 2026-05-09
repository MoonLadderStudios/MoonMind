# Quickstart: Model Explicit Step Type Payloads and Validation

## Preconditions

- Work from the repository root.
- Keep `MM-569`, `manual-mm-569-mm-574`, and the original Jira preset brief traceable in all downstream artifacts.
- Do not create `tasks.md` or implementation changes during the planning step.

## Focused Unit Validation

Run task contract tests while iterating on executable submission validation:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
```

Run task template catalog tests while iterating on Tool, Skill, and Preset validation:

```bash
./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py
```

Run focused Create-page Step Type tests after frontend dependencies are prepared:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx
```

## Focused Integration Validation

Run task contract normalization integration tests:

```bash
./tools/test_integration.sh
```

For focused local iteration before the full integration wrapper, use the existing hermetic targets:

```bash
pytest tests/integration/api/test_task_contract_normalization.py -q --tb=short
pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q --tb=short
```

## Story Validation Flow

1. Add red-first unit tests for valid Tool, Skill, and Preset examples.
2. Add red-first unit tests for mixed-type, missing payload, command-like Tool, Skill metadata, and Preset schema failures.
3. Add integration coverage that executable submission rejects unresolved Preset steps and accepts only flat Tool/Skill executable steps with provenance.
4. Implement the smallest validation changes required to pass those tests.
5. Confirm failed Preset expansion preserves entered inputs and visible field-addressable errors.
6. Run the full unit suite:

```bash
./tools/test_unit.sh
```

7. Run hermetic integration verification:

```bash
./tools/test_integration.sh
```

8. Confirm traceability remains present in MoonSpec artifacts:

```bash
rg -n "MM-569|manual-mm-569-mm-574|DESIGN-REQ-012|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-018|DESIGN-REQ-021" specs/331-model-step-type-payloads
```

## Expected Result

- Tool, Skill, and Preset draft payloads validate according to the Step Type discriminator.
- Mixed-type and missing type-specific payloads fail before execution.
- Validation failures include field paths and actionable reasons.
- Executable runtime payloads contain only Tool and Skill steps unless linked-preset execution is explicitly supported.
- `MM-569` and `manual-mm-569-mm-574` remain preserved for final verification.

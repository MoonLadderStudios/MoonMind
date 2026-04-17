# Quickstart: Composable Preset Expansion

## Focused Unit Validation

```bash
pytest tests/unit/api/test_task_step_templates_service.py -q
```

Expected coverage:
- Concrete-step-only expansion remains compatible.
- Parent presets flatten active child includes.
- Flattened steps include deterministic IDs and provenance.
- Composition metadata describes the include tree.
- Scope, cycle, inactive target, incompatible input, and limit failures include useful paths.

## Full Unit Suite

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Suite

```bash
./tools/test_integration.sh
```

Run when Docker is available. This story does not add new external credentials.

## Manual Contract Check

1. Create a global child preset with one or more concrete steps.
2. Create a global parent preset with a `kind: include` entry referencing the child slug and pinned version.
3. Expand the parent preset.
4. Confirm `steps[]` contains only concrete executable steps.
5. Confirm `composition` and each step's `presetProvenance` preserve MM-383 contract semantics.
6. Confirm no executor or `PlanDefinition` path needs to resolve includes at runtime.

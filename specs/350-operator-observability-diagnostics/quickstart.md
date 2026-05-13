# Quickstart: Operator Observability Diagnostics

## Scope

Verify the single MM-651 story: operators can diagnose attachment and Resume outcomes from task detail target diagnostics without parsing raw workflow history.

## Test-First Workflow

1. Add focused backend tests before implementation changes:

```bash
pytest tests/unit/api/routers/test_executions.py -q --tb=short
```

Target new or strengthened cases:
- objective and step attachments are grouped by target
- an empty target is visibly distinguishable from a populated target
- manifest and generated context refs both render through execution detail
- attachment failures expose failing target and bounded phase
- compatibility alias input does not retarget objective attachments to steps or step attachments to objective
- failed Resume diagnostics include `checkpoint_validation`, `workspace_restoration`, `preserved_output_injection`, and `failed_step_execution`

2. Add or strengthen frontend tests before UI changes:

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
```

Target UI cases:
- target cards render objective and step metadata separately
- targets without attachments show an explicit empty state
- generated context refs and manifest refs are shown under Evidence
- failed attachment phases and evidence refs are visible
- Resume recovery shows source run, checkpoint, preserved steps, and failed Resume phase
- raw diagnostics remain independently accessible

3. Add or strengthen integration boundary tests:

```bash
pytest tests/integration/schemas/test_execution_target_diagnostics_boundary.py -q --tb=short
```

Add route or schema-boundary coverage when implementation changes affect:
- serialized payload shape
- alias compatibility
- failed Resume phase values
- preserved-step provenance

4. Run required final unit verification:

```bash
./tools/test_unit.sh
```

5. Run required hermetic integration verification:

```bash
./tools/test_integration.sh
```

## Implementation Contingency

If the new verification tests fail, update the smallest relevant surface:
- Backend projection: `api_service/api/routers/executions.py`
- Temporal Resume source/provenance service: `moonmind/workflows/temporal/service.py`
- Runtime Resume validation: `moonmind/workflows/temporal/workflows/run.py`
- Frontend task detail rendering: `frontend/src/entrypoints/task-detail.tsx`
- Generated API types only through the existing OpenAPI generation workflow if schema models change

## End-to-End Acceptance

An execution detail response containing objective attachments, step attachments, manifest refs, generated context refs, attachment failures, Resume provenance, preserved steps, and failed Resume phase renders in Mission Control such that an operator can identify:
- the owning target for each attachment item
- the target and phase for each attachment failure
- the target for each manifest or generated context ref
- the source run and preserved steps for Resume
- the failed Resume phase without opening raw workflow history

## Traceability Check

Before proceeding to tasks or implementation, confirm:

```bash
rg -n "MM-651|DESIGN-REQ-012|DESIGN-REQ-030|DESIGN-REQ-031" specs/350-operator-observability-diagnostics
```

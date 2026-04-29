# Quickstart: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

## Focused Verification

Run the focused backend suites that prove the MM-567 runtime and proposal boundaries:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py
```

Expected result:
- Explicit Tool and Skill steps materialize into runtime plan nodes.
- Preset and Activity Step Types are rejected at executable boundaries.
- Proposal promotion preserves preset provenance and rejects unresolved Preset steps.
- Proposal preview exposes preset provenance metadata.

## Documentation Check

Verify canonical Step Types documentation still states the desired runtime/proposal semantics:

```bash
rg -n "Promotion validates|does not require live preset lookup|Activity means Temporal Activity|Preset.*No runtime node" docs/Steps/StepTypes.md
```

## Full Unit Verification

Before closing the feature, run:

```bash
./tools/test_unit.sh
```

If full unit verification is blocked by environment limitations, record the exact blocker in `verification.md` and keep focused test evidence.

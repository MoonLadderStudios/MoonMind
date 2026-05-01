# Quickstart: Submit Flattened Executable Steps with Provenance

## Focused Unit Tests

Run the backend contract, runtime, and proposal tests first:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

Expected coverage:
- Tool and Skill executable steps are accepted.
- Preset, Activity, and shell-shaped executable steps are rejected.
- Preset-derived source metadata preserves `presetId`, `presetVersion`, `includePath`, and `originalStepId`.
- Runtime materialization does not require preset provenance or live catalog lookup.
- Proposal promotion validates a flat executable payload, preserves provenance, rejects unresolved Preset steps, and does not silently re-expand a live preset.

## Focused Frontend Integration-Boundary Tests

Run the Create page target through the managed dashboard runner:

```bash
./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:
- Applying a preset replaces the Preset placeholder with editable executable Tool and Skill steps.
- Submitting the applied preset sends only Tool and Skill steps.
- Preset-derived steps retain complete provenance metadata in the submitted payload.
- Preview failure leaves the draft unchanged.
- Refresh/reapply requires explicit preview and validation before replacing reviewed steps.

## Full Unit Verification

Before final verification, run:

```bash
./tools/test_unit.sh
```

If the full suite is blocked by an unrelated environment issue, record the exact blocker and preserve focused test evidence.

## End-to-End Story Check

1. Start with a task draft containing a configured Preset step.
2. Preview expansion and confirm generated Tool and Skill steps plus warnings are visible before mutation.
3. Apply the preview and confirm the Preset placeholder is replaced.
4. Submit or promote the reviewed task payload.
5. Confirm the executable payload contains only Tool and Skill steps.
6. Confirm preset provenance remains visible for audit/review and does not control runtime execution.
7. Confirm catalog refresh requires explicit preview and validation.

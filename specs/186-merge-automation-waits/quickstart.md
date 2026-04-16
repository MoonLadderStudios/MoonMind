# Quickstart: Merge Automation Waits

## Focused Unit Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_merge_gate_models.py \
  tests/unit/workflows/temporal/test_merge_gate_workflow.py \
  tests/unit/workflows/temporal/test_run_merge_gate_start.py \
  tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py \
  tests/unit/workflows/temporal/test_temporal_workers.py
```

Expected coverage:
- `MoonMind.MergeAutomation` is the canonical workflow type.
- Start payloads carry `publishContextRef`, parent ids, compact PR identity, merge config, and resolver template.
- Blocked/stale/expired readiness does not launch a resolver.
- External events wake the workflow before fallback polling.
- Fallback polling uses configured `fallbackPollSeconds`.
- Continue-As-New payloads preserve compact wait state.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Verification

```bash
./tools/test_integration.sh
```

Run when Docker Compose is available. If Docker is unavailable in the managed agent container, record the exact blocker in `specs/186-merge-automation-waits/verification.md`.

## End-To-End Scenario

1. Submit or simulate a PR-publishing `MoonMind.Run` with merge automation enabled.
2. Confirm the parent starts `MoonMind.MergeAutomation` with compact publish context ref and current PR head SHA.
3. Stub readiness as blocked; confirm summary shows `awaiting_external`, blockers, cycle count, and no resolver run.
4. Signal `merge_automation.external_event`; confirm readiness re-evaluates before fallback polling.
5. Stub readiness as ready for the current head SHA; confirm one child resolver run request is built with publish mode `none`.
6. Stub expired wait; confirm output status is `expired` and no resolver run is launched.

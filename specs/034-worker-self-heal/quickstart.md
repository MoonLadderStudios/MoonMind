# Quickstart: Worker Self-Heal System (Phase 1)

## Prerequisites

- MoonMind stack running with worker + API + broker.
- Required auth/secrets configured for normal task execution.
- Unit test runner available via `./tools/test_unit.sh`.

## Self-Heal Budget Configuration

Set worker env vars (or rely on worker defaults):

```bash
export STEP_MAX_ATTEMPTS=3
export STEP_TIMEOUT_SECONDS=900
export STEP_IDLE_TIMEOUT_SECONDS=300
export STEP_NO_PROGRESS_LIMIT=2
export JOB_SELF_HEAL_MAX_RESETS=1
```

For local smoke tests, use lower timeout values (for example `30`/`10`) to speed validation.

## Smoke Test 1: Retryable failure soft-reset recovery

1. Run a task step that fails once with a transient runtime error and then succeeds.
2. Verify event stream includes:
   - `task.step.attempt.started` (attempt 1)
   - `task.step.attempt.failed`
   - `task.self_heal.triggered`
   - `task.step.attempt.started` (attempt 2)
   - `task.step.attempt.finished`
3. Verify uploaded artifacts include:
   - `state/self_heal/attempt-0000-0001.json`
   - `state/steps/step-0000.json`

## Smoke Test 2: Retryable exhaustion escalates to queue retry

1. Configure a step to fail repeatedly with a retryable transient failure.
2. Verify final event includes `task.self_heal.exhausted`.
3. Verify terminal failure is marked retryable when queue attempts remain.
4. Confirm attempt artifacts are preserved under `state/self_heal/`.

## Smoke Test 3: Deterministic failure fails fast

1. Trigger a deterministic validation/policy style failure.
2. Verify no `task.self_heal.triggered` event is emitted.
3. Verify job fails after first attempt with non-retryable semantics.

## Control Compatibility Check

1. Trigger self-heal retry conditions.
2. Apply pause/takeover control while job is active.
3. Verify worker does not launch the next attempt until pause is cleared.

## Required Verification

Run the full unit suite:

```bash
./tools/test_unit.sh
```

## Verification Notes (2026-03-02)

- Runtime scope gate passed:
  - `SPECIFY_FEATURE=034-worker-self-heal .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- Unit validation passed:
  - `./tools/test_unit.sh` (`905 passed`, dashboard runtime node tests completed)

Phase 2/3 scenarios (hard reset replay and operator recovery actions) are intentionally out of current quickstart scope.

# Quickstart: Validate Codex Session Phase 2 Runtime Behaviors

## Prerequisites

- Run from the repository root.
- Use the active feature branch: `155-codex-session-phase2-runtime`.
- Managed-agent local test mode should be enabled for final unit verification:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

## Focused Validation

Run the focused tests that exercise the changed runtime/workflow boundaries:

```bash
python -m pytest -q \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/services/temporal/runtime/test_codex_session_runtime.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/workflows/temporal/test_activity_runtime.py
```

Expected result:

- `CancelSession` tests show cancellation stops active work without terminating the session.
- `TerminateSession` tests show cleanup is invoked and failures are not reported as successful termination.
- Runtime steering tests show the hardcoded unsupported path is gone.
- Controller tests show duplicate launch, clear, interrupt, and terminate behavior is retry-safe.
- Activity runtime/catalog tests show heartbeat coverage for blocking controls.

## Parent Workflow Regression

Run the parent/session teardown regression tests:

```bash
python -m pytest -q tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py
```

Expected result:

- Parent workflow teardown still targets `TerminateSession`.
- Session cleanup does not regress existing task finalization behavior.

## Full Unit Verification

Run the repository unit wrapper before finalizing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result:

- Python unit suite passes.
- Frontend unit suite passes when invoked by the wrapper.
- No Docker-in-Docker fallback is required inside managed-agent worker environments.

## Manual Runtime Smoke Check

When a local Docker-backed MoonMind environment is available:

1. Start the system with `docker compose up -d`.
2. Submit a Codex managed-session task.
3. Confirm the first turn starts and produces live/session observability.
4. Send a steering request while a turn is active.
5. Cancel the active turn and confirm the session remains recoverable.
6. Terminate the session and confirm the session container is removed and the supervision record reaches terminated state.

This smoke check is optional for unit completion but useful before broader rollout.

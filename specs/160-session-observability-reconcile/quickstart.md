# Quickstart: Managed Session Observability and Reconcile

## Runtime Mode

This feature is runtime implementation work. Required deliverables include production runtime code changes plus validation tests. Docs/spec-only completion is invalid.

## Implementation Checklist

1. Add bounded managed-session visibility updates to the session workflow.
2. Add static summary/details and initial bounded Search Attributes when the task-scoped session workflow is started.
3. Add readable, secret-safe activity summaries for managed-session launch and control activities.
4. Add the managed-session reconcile activity to the agent-runtime activity catalog and runtime binding.
5. Add the recurring reconcile workflow target and register it on workflow workers.
6. Add the Temporal client helper that creates or updates the recurring reconcile schedule.
7. Add focused tests for workflow metadata, activity summaries, routing, worker registration, schedule behavior, and bounded reconcile output.

## Focused Verification

Run the focused runtime tests while iterating:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/temporal/test_temporal_workers.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_main_async_workflow_fleet \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py::test_agent_runtime_reconcile_managed_sessions_returns_bounded_summary \
  tests/unit/workflows/temporal/test_client_schedules.py::TestManagedSessionReconcileSchedule
```

## Final Verification

Before handoff, run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Operator Safety Checks

Confirm the implemented metadata never includes:

- prompts
- transcripts
- terminal scrollback
- raw logs
- credentials
- secret values
- raw error bodies

Confirm Docker/runtime work remains on the agent-runtime activity fleet and not the workflow fleet.

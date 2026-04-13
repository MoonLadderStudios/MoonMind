# Quickstart: Codex Managed Session Phase 4/5 Hardening

## Runtime Scope

This feature is runtime implementation work. Required deliverables include production runtime code changes, not docs-only updates, plus validation tests.

## Implementation Checklist

1. Audit existing Phase 4 and Phase 5 implementation surfaces and identify only missing behavior.
2. Add failing or regression tests for each missing behavior before or alongside code changes.
3. Verify bounded workflow visibility and Search Attribute behavior.
4. Verify activity summaries for launch, send, interrupt, clear, cancel, steer, and terminate.
5. Verify metrics, tracing, and structured log correlation uses bounded identifiers only.
6. Verify runtime/container activity routing stays on the runtime worker boundary.
7. Verify scheduled reconcile and bounded reconcile outcomes.
8. Verify lifecycle integration behavior for send, clear, interrupt, cancel, and terminate.
9. Verify restart/reconcile, race/idempotency, Continue-As-New carry-forward, and replay safety.

## Focused Verification

Use focused tests while iterating. Adjust the exact target list to the behavior changed in the task:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/temporal/test_temporal_workers.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/workflows/temporal/test_client_schedules.py \
  tests/unit/workflows/temporal/test_agent_session_replayer.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py \
  tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py
```

## Final Verification

Before handoff, run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration tests when changes touch required integration-ci seams:

```bash
./tools/test_integration.sh
```

Provider verification remains optional and must not be required for this feature unless explicitly requested by an operator.
No provider-verification coverage is intentionally deferred for this feature because Phase 4/5 hardening is confined to Temporal workflow visibility, runtime activity routing, reconciliation, lifecycle controls, and replay validation.

## Safety Review

Before completion, scan metadata, summaries, schedules, telemetry correlation fields, reconcile outcomes, and replay fixtures for forbidden values:

- prompts
- transcripts
- terminal scrollback
- raw logs
- raw provider output
- raw error bodies
- credentials
- secret values

Confirm runtime/container side effects remain on the runtime activity boundary.

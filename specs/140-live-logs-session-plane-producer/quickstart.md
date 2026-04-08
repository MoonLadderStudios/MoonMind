# Quickstart: Live Logs Session Plane Producer

## Focused verification

Run the Phase 2 unit-test slice:

```bash
./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/workflows/adapters/test_codex_session_adapter.py
```

Run the Spec Kit scope checks:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## Expected behavior

1. Reusing existing runtime handles emits a `session_resumed` row and signals `resume_session`.
2. Steering and termination produce normalized `session`-stream rows.
3. Publishing session artifacts adds `summary_published` and `checkpoint_published` rows to durable observability history.
4. Session-event publication failures do not break successful control actions or reset-boundary artifact persistence.

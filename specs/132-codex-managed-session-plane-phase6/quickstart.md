# Quickstart: Codex Managed Session Plane Phase 6

## Focused Verification

1. Run the focused session suites:

```bash
./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_store.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

2. Run the Spec Kit scope gates for this feature:

```bash
SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

3. Run the repo unit suite:

```bash
./tools/test_unit.sh
```

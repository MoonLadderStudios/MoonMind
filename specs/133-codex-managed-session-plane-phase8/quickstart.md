# Quickstart: Codex Managed Session Plane Phase 8

## Focused Verification

1. Run the focused Phase 8 suites:

```bash
./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/test_agent_runtime_activities.py
```

2. Run the Spec Kit scope gates for this feature:

```bash
SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=133-codex-managed-session-plane-phase8 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

3. Run the repo unit suite:

```bash
./tools/test_unit.sh
```

# Quickstart: Codex Managed Session Plane Phase 10

1. Run the focused Phase 10 verification:

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py
```

2. Validate Spec Kit scope:

```bash
SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

3. Run the full unit suite before finalizing:

```bash
./tools/test_unit.sh
```

4. Manual smoke check:

```text
Execute one Codex managed-session step, note its taskRunId, and load /api/task-runs/{taskRunId}/observability-summary.
Confirm stdout/stderr/diagnostics refs are present and live streaming is not advertised.
```

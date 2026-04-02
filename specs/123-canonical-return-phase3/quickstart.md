# Quickstart: Validate Phase 3 Canonical Returns

**Branch**: `123-canonical-return-phase3`

## Run Unit Tests

```bash
./tools/test_unit.sh
```

All tests must pass. Key test files to verify:

- `tests/unit/workflows/temporal/test_agent_runtime_fetch_result.py` — existing tests, updated assertions
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py` — new TDD tests

## Verify Return Type Annotations

```bash
grep -n "def agent_runtime_status\|def agent_runtime_fetch_result\|def agent_runtime_cancel\|def agent_runtime_publish_artifacts" \
  moonmind/workflows/temporal/activity_runtime.py
```

Expected: each method signature ends with `-> AgentRunStatus:` or `-> AgentRunResult:` (no `dict[str, Any]` or `None` for the target 4 methods).

## Verify No Provider-Specific Fields Leak Out

```bash
grep -n "external_id\|tracking_ref\|provider_status" \
  moonmind/workflows/temporal/activity_runtime.py | grep "agent_runtime"
```

Expected: no matches in the managed runtime activity methods.

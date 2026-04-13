# Quickstart: DooD Bounded Helper Containers

This feature is runtime implementation work. Production code changes and validation tests are required; docs/spec-only completion is invalid.

## Focused Validation

Run the workload contract and launcher tests while iterating:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_contract.py \
  tests/unit/workloads/test_docker_workload_launcher.py \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py
```

For a narrower helper tool/activity pass, run:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py
```

## Full Verification

Before finalizing, run:

```bash
./tools/test_unit.sh
```

## Manual Smoke Shape

1. Define a helper-capable runner profile with `kind: bounded_service`, TTL limits, readiness probe, safe mount policy, and cleanup policy.
2. Submit a helper request with owner task/step/attempt, artifacts directory, explicit `ttlSeconds`, and allowed command/env values.
3. Confirm the helper result reports `ready` only after readiness passes.
4. Run at least two dependent sub-step actions that refer to the same helper ownership metadata, not a session identity.
5. Tear down the helper and confirm stop/kill/remove diagnostics and runtime artifacts exist.
6. Run expired-helper cleanup and confirm only expired helpers are removed.

## Expected Evidence

- Helper request validation rejects missing TTL, missing readiness, unknown profile, excessive TTL, disallowed env, unsafe mounts, and unsupported helper profile use.
- Helper metadata uses a helper/workload kind and deterministic helper ownership, not `session_id` as identity.
- Readiness success and failure are visible from bounded artifacts and metadata.
- Helper start/stop tool results use normal executable-tool output shape through `container.start_helper` and `container.stop_helper`.
- Temporal `workload.run` dispatches helper start/stop by tool name while preserving one-shot workload behavior.
- Teardown records cleanup attempts and final status.
- Expired-helper sweeper preserves fresh helpers, one-shot workloads, session containers, and unrelated containers.

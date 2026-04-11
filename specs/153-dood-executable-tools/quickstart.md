# Quickstart: DooD Executable Tool Exposure

## Purpose

Validate that Docker-backed workloads are exposed through MoonMind's executable tool path and remain separate from managed session identity.

## Prerequisites

- Current branch: `153-dood-executable-tools`
- Python and frontend dependencies available through the standard unit test runner
- Existing Phase 1 workload contracts and Phase 2 Docker workload launcher code present

## Focused Verification

Run the Phase 3 focused verification:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_activity_catalog.py \
  tests/unit/workflows/temporal/test_activity_runtime.py::test_default_skill_registry_payload_uses_dood_tool_definitions \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_runtime_activities_reconciles_managed_sessions_only_on_agent_runtime_fleet \
  tests/unit/workflows/temporal/workflows/test_run_integration.py::test_run_execution_stage_routes_dood_skill_tool_to_agent_runtime_activity
```

Expected result:

- `container.run_workload` and `unreal.run_tests` definitions require `docker_workload`.
- `docker_workload` skill capability resolves to the `agent_runtime` task queue.
- Workload tool inputs convert to validated workload requests.
- `MoonMind.Run` invokes the workload tool through `mm.tool.execute`, not `MoonMind.AgentRun`.

## Existing Launcher Regression Coverage

Run Phase 2 launcher regression tests with Phase 3 routing tests:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_contract.py \
  tests/unit/workloads/test_docker_workload_launcher.py \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_activity_catalog.py \
  tests/unit/workflows/temporal/test_temporal_workers.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py
```

Expected result:

- Existing workload launcher behavior remains green.
- The new executable tool bridge does not regress validated workload request handling.
- Worker topology continues to advertise Docker workload capability only on the Docker-capable fleet.

## Full Verification

Run the full unit suite before finalizing implementation:

```bash
./tools/test_unit.sh
```

Expected result:

- Python unit suite passes.
- Frontend unit suite passes.
- Any warnings are reviewed and confirmed unrelated to this feature.

## Runtime Scope Validation

After implementation tasks exist, validate runtime scope:

```bash
SPECIFY_FEATURE=153-dood-executable-tools \
  .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime

SPECIFY_FEATURE=153-dood-executable-tools \
  .specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

Expected result:

- Tasks include production runtime code changes.
- Tasks include validation tests.
- Diff includes runtime files and tests.

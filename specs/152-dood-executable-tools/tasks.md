# Tasks: DooD Executable Tool Exposure

**Input**: Design documents from `/specs/152-dood-executable-tools/`  
**Prerequisites**: spec.md, plan.md

**Tests**: Required. This feature exposes Docker-backed workloads through the existing executable tool path and must include routing, conversion, and workflow-boundary coverage.

## Phase 1: Tests First

- [X] T001 [P] Add workload tool bridge tests in `tests/unit/workloads/test_workload_tool_bridge.py` for curated tool definition generation, `container.run_workload` request conversion, launcher invocation, and `unreal.run_tests` command construction.
- [X] T002 [P] Add activity catalog routing coverage in `tests/unit/workflows/temporal/test_activity_catalog.py` for `docker_workload` skill capability routing to the `agent_runtime` task queue.
- [X] T003 [P] Add default registry payload coverage in `tests/unit/workflows/temporal/test_activity_runtime.py` for generated DooD `ToolDefinition` payloads.
- [X] T004 [P] Add workflow-boundary coverage in `tests/unit/workflows/temporal/workflows/test_run_integration.py` proving a `tool.type = "skill"` DooD step routes through `mm.tool.execute` on the agent-runtime queue.

## Phase 2: Runtime Implementation

- [X] T005 Implement DooD executable tool definitions and workload-tool handlers in `moonmind/workloads/tool_bridge.py`.
- [X] T006 Export DooD tool bridge helpers from `moonmind/workloads/__init__.py`.
- [X] T007 Route `docker_workload` skill capabilities to the `agent_runtime` fleet in `moonmind/workflows/temporal/activity_catalog.py`.
- [X] T008 Generate curated DooD tool definitions from the default executable tool registry path in `moonmind/workflows/temporal/activity_runtime.py`.
- [X] T009 Register DooD skill handlers on the agent-runtime worker path in `moonmind/workflows/temporal/worker_runtime.py`.

## Phase 3: Tracking and Verification

- [X] T010 Update Phase 3 completion notes in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`.
- [X] T011 Run focused verification with `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_temporal_workers.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_activity_runtime.py::test_default_skill_registry_payload_uses_dood_tool_definitions tests/unit/workflows/temporal/workflows/test_run_integration.py::test_run_execution_stage_routes_dood_skill_tool_to_agent_runtime_activity`.
- [X] T012 Run full unit verification with `./tools/test_unit.sh`.
- [X] T013 Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T014 Run runtime diff validation with `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

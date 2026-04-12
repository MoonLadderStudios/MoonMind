# Tasks: DooD Unreal Pilot

**Input**: Design documents from `/specs/159-dood-unreal-pilot/`  
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The feature request explicitly requires test-driven development and validation tests for runtime behavior.

## Phase 1: Tests First

- [X] T001 [P] Add default Unreal profile registry tests in `tests/unit/workloads/test_workload_contract.py`
- [X] T002 [P] Add worker bootstrap tests proving the built-in registry loads when no env override is set in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T003 [P] Add `unreal.run_tests` report contract tests in `tests/unit/workloads/test_workload_tool_bridge.py`
- [X] T004 [P] Add launcher arg/artifact tests for Unreal cache mounts and report refs in `tests/unit/workloads/test_docker_workload_launcher.py`

## Phase 2: Implementation

- [X] T005 Add `config/workloads/default-runner-profiles.yaml` with `unreal-5_3-linux`
- [X] T006 Load the built-in profile registry from `moonmind/workflows/temporal/worker_runtime.py` when no operator registry is configured
- [X] T007 Extend `unreal.run_tests` input schema and request conversion in `moonmind/workloads/tool_bridge.py`
- [X] T008 Ensure report paths are relative declared outputs and invalid paths fail before launch

## Phase 3: Verification and Tracking

- [X] T009 Run targeted workload and worker bootstrap tests
- [X] T010 Update `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md` with Phase 6 completion notes and operator enablement path
- [X] T011 Run final unit verification or document any local blocker

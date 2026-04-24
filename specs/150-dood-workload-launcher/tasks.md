# Tasks: Docker-Out-of-Docker Workload Launcher

**Input**: Design documents from `/specs/150-dood-workload-launcher/`
**Prerequisites**: Phase 1 workload contract in `/specs/148-dood-workload-contract/`

**Tests**: TDD is required. Add failing launcher and routing tests first, then implement the launcher and worker wiring.

## Phase 1: Setup

- [X] T001 Review Phase 1 workload models and registry validation.
- [X] T002 Review `docs/ManagedAgents/DockerOutOfDocker.md` and the remaining-work tracker.

## Phase 2: Tests First

- [X] T003 Add failing unit tests for Docker run argument construction, deterministic labels, workspace/cache mounts, env/resource flags, stdout/stderr capture, and completion cleanup.
- [X] T004 Add failing timeout cleanup tests proving stop/kill/rm calls.
- [X] T005 Add failing orphan lookup tests for label-filtered Docker cleanup.
- [X] T006 Add failing activity catalog and worker topology tests for `workload.run` on the `agent_runtime` fleet with `docker_workload` capability.

## Phase 3: Launcher Implementation

- [X] T007 Implement `DockerWorkloadLauncher` and `DockerContainerJanitor` in `moonmind/workloads/docker_launcher.py`.
- [X] T008 Build deterministic `docker run` arguments from `ValidatedWorkloadRequest` in `moonmind/workloads/docker_launcher.py`.
- [X] T009 Implement stdout/stderr capture, exit metadata, timeout cleanup, cancellation cleanup, and remove-on-exit behavior in `moonmind/workloads/docker_launcher.py`.

## Phase 4: Worker Fleet Wiring

- [X] T010 Add `workload.run` to the Temporal activity catalog on `mm.activity.agent_runtime` in `moonmind/workflows/temporal/activity_catalog.py`.
- [X] T011 Expose `docker_workload` as an `agent_runtime` fleet capability and forbid it on non-Docker fleets in `moonmind/workflows/temporal/workers.py`.
- [X] T012 Bind `workload.run` to `TemporalAgentRuntimeActivities` without overloading session verbs in `moonmind/workflows/temporal/activity_runtime.py`.
- [X] T013 Initialize the workload registry and launcher in the `agent_runtime` worker runtime from deployment env in `moonmind/workflows/temporal/worker_runtime.py`.

## Phase 5: Verification

- [X] T014 Run focused unit tests for workload launcher and routing with `./tools/test_unit.sh --python-only tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_temporal_workers.py tests/unit/workflows/temporal/test_workload_run_activity.py`.
- [X] T015 Run full unit test suite with `./tools/test_unit.sh`.
- [X] T016 Update `docs/ManagedAgents/DockerOutOfDocker.md` to mark Phase 2 complete.

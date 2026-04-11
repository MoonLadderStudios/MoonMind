# Quickstart: Docker-Out-of-Docker Workload Launcher

## Prerequisites

- Local MoonMind development environment with Python dependencies installed.
- Existing Phase 1 workload request and runner-profile contract available.
- Docker-capable worker configuration uses the existing `DOCKER_HOST` / docker-proxy wiring.

## Focused Verification

Run the backend tests that cover the Phase 2 launcher and routing boundary:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_contract.py \
  tests/unit/workloads/test_docker_workload_launcher.py \
  tests/unit/workflows/temporal/test_activity_catalog.py \
  tests/unit/workflows/temporal/test_temporal_workers.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py
```

Expected result:

- workload contract tests remain green;
- launcher tests prove Docker argument construction, stream capture, cleanup, timeout handling, and orphan lookup;
- activity catalog and worker topology tests prove `workload.run` routes to the `agent_runtime` fleet with `docker_workload` capability;
- activity runtime tests prove the activity validates the request and calls the launcher.

## Full Verification

Run the full unit suite before completing implementation:

```bash
./tools/test_unit.sh
```

## Scope Validation

Runtime mode requires production runtime code changes plus tests:

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## Operator Configuration Notes

- `MOONMIND_WORKLOAD_PROFILE_REGISTRY` points to the deployment-owned runner profile registry file.
- `MOONMIND_AGENT_RUNTIME_STORE` defaults to the shared agent workspace root.
- `MOONMIND_DOCKER_BINARY` defaults to `docker`.
- `DOCKER_HOST` or `SYSTEM_DOCKER_HOST` should point at the existing docker-proxy endpoint for the Docker-capable worker fleet.

## Expected Phase Boundary

This phase should end with a working one-shot workload launcher and worker capability. It should not expose generic plan tools, publish durable workload log artifacts, add security hardening beyond existing profile validation, or implement bounded helper containers.

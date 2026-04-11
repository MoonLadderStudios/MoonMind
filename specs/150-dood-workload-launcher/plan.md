# Implementation Plan: Docker-Out-of-Docker Workload Launcher

**Branch**: `150-dood-workload-launcher` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)
**Input**: Phase 2 of the MoonMind Docker-out-of-Docker phased implementation plan.

## Summary

Implement the Phase 2 launcher as a standalone workload module that consumes the Phase 1 validated request/profile contract and executes one bounded Docker container through the existing Docker-capable `agent_runtime` worker fleet. Add `workload.run` as a distinct activity with `docker_workload` capability routing rather than reusing managed-session verbs.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: stdlib `asyncio`, Docker CLI through `DOCKER_HOST`
**Storage**: Existing `agent_workspaces` Docker volume; no database changes
**Testing**: `./tools/test_unit.sh`
**Target Platform**: Existing `temporal-worker-agent-runtime` deployment with `docker-proxy`
**Project Type**: Python service module and Temporal activity routing

## Constitution Check

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Workload containers remain MoonMind-launched control-plane workloads. |
| II. One-Click Agent Deployment | PASS | Uses existing `agent_runtime` worker and `docker-proxy` wiring. |
| III. Avoid Vendor Lock-In | PASS | Launcher is profile-driven and not Unreal-specific. |
| IV. Own Your Data | PASS | Results are bounded metadata against local workspace/artifact paths. |
| V. Skills Are First-Class | PASS | Keeps executable tool path future-ready through a distinct workload activity. |
| VI. Bittersweet Lesson | PASS | Docker execution is isolated behind a replaceable launcher service. |
| VII. Powerful Runtime Configurability | PASS | Docker binary, Docker host, registry path, and workspace volume remain env-configurable. |
| VIII. Modular and Extensible | PASS | Launcher and janitor live under `moonmind/workloads/`. |
| IX. Resilient by Default | PASS | Timeout/cancel cleanup paths are explicit and tested. |
| X. Continuous Improvement | PASS | Result metadata carries diagnostics needed by later artifact phases. |
| XI. Spec-Driven Development | PASS | Spec, plan, tasks, tests, and implementation are included. |
| XII. Canonical Docs vs tmp | PASS | Phase status is tracked in `docs/tmp/remaining-work/`. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or old workload execution paths are added. |

## Project Structure

```text
moonmind/workloads/docker_launcher.py
moonmind/workloads/__init__.py
moonmind/workflows/temporal/activity_catalog.py
moonmind/workflows/temporal/activity_runtime.py
moonmind/workflows/temporal/worker_runtime.py
moonmind/workflows/temporal/workers.py
tests/unit/workloads/test_docker_workload_launcher.py
tests/unit/workflows/temporal/test_activity_catalog.py
tests/unit/workflows/temporal/test_temporal_workers.py
docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md
```

## Complexity Tracking

No constitution violations require complexity waivers.

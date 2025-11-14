# Implementation Plan: MoonMind Orchestrator Implementation

**Branch**: `005-orchestrator-architecture` | **Date**: 2025-11-13 | **Spec**: [`specs/005-orchestrator-architecture/spec.md`](specs/005-orchestrator-architecture/spec.md)
**Input**: Feature specification from `specs/005-orchestrator-architecture/spec.md`

## Summary

Implement the mm-orchestrator service inside the MoonMind docker-compose stack so high-level repair instructions can be translated into audited patch/build/restart cycles with automated verification, approval enforcement, artifact retention, and StatsD metrics. Delivery centers on a Python 3.11 worker that mounts the repo and Docker socket, executes Compose commands for a single target service, and persists run metadata plus artifacts for MoonMind operators.

## Technical Context

**Language/Version**: Python 3.11 runtime inside the orchestrator container plus POSIX shell for invoking Docker Compose.
**Primary Dependencies**: Docker CLI + Compose plugin, Celery 5.4 task runner, RabbitMQ 3.x broker, PostgreSQL result backend, StatsD-compatible metrics sink.
**Storage**: Local spec workflow artifacts under `var/artifacts/spec_workflows/<run_id>` and PostgreSQL tables (`spec_workflow_runs`, `spec_workflow_task_states`) for run/step status.
**Testing**: Compose-based integration tests that spin up RabbitMQ, Celery worker, API, and orchestrator containers plus focused unit tests for ActionPlan logic (per Phase 0 research).
**Target Platform**: Single Linux host (or small pool) running Docker with docker-compose, using Docker-outside-of-Docker via mounted `/var/run/docker.sock`.
**Project Type**: Backend automation service added to the existing MoonMind services stack plus supporting Celery workers.
**Performance Goals**: Per spec, 90% of successful runs must complete patch -> verify within 20 minutes and 95% must emit a complete artifact set retrievable within 1 minute of completion.
**Constraints**: Must obey file-level allow list, respect approval gates for protected services, restrict service restarts to target container only, and operate without Kubernetes.
**Scale/Scope**: Start with one orchestrator worker processing runs sequentially per host, with the option to add workers later once service-level locking is in place.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still a placeholder with no enforceable principles, so there are no concrete gates to evaluate. Record: **PASS – Constitution pending definition**. Once the constitution is populated, revisit this feature for retroactive compliance.
**Post-Phase-1 Recheck (2025-11-13)**: Design outputs (research, data model, contracts, quickstart) introduce no extra projects or tooling beyond the placeholder constitution, so status remains **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/005-orchestrator-architecture/
├── plan.md              # /speckit.plan output (this file)
├── spec.md              # Approved feature specification
├── research.md          # Phase 0 research log
├── data-model.md        # Phase 1 entity + validation design
├── quickstart.md        # Phase 1 operational onboarding guide
├── contracts/           # Phase 1 API contracts (OpenAPI)
└── checklists/          # Specification quality artifacts from /speckit.specify
```

### Source Code (repository root)

```text
docker-compose.yaml               # Defines api, celery worker, supporting services
docker-compose.job.yaml           # Job-oriented overrides (future orchestrator service goes here)
moonmind/                         # Core Python package (planning, workflows, utils)
api_service/                      # HTTP entrypoint service
celery_worker/                    # Existing worker container build context
spec_tools/ (future)              # <-- new orchestrator package under moonmind/workflows/orchestrator
services/orchestrator/            # <-- new container context with Dockerfile + entrypoint
tests/
├── integration/
│   └── orchestrator/             # <-- new compose-driven verification suites
└── unit/
var/artifacts/spec_workflows/     # Run-scoped artifact storage mount
```

**Structure Decision**: Extend the compose stack with a dedicated `services/orchestrator/` build context that mounts the repo into `/workspace`, add a Python package (e.g., `moonmind.workflows.orchestrator`) for ActionPlan logic plus Celery tasks, and introduce an integration test directory under `tests/integration/orchestrator` to exercise patch/build/relaunch flows using the standard compose stack.

## Complexity Tracking

No constitution-defined violations identified; table intentionally left empty pending a finalized governance document.

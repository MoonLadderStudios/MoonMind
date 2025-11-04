# Implementation Plan: Spec Kit Automation Pipeline

**Branch**: `002-document-speckit-automation` | **Date**: 2025-11-03 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/002-document-speckit-automation/spec.md`

**Note**: This plan follows the `/speckit.plan` workflow and stays aligned with research and design artifacts produced in Phases 0–1.

## Summary

Automate end-to-end execution of Spec Kit phases by having Celery workers spin up ephemeral job containers, run the `speckit.specify`, `speckit.plan`, and `speckit.tasks` prompts against a repository workspace, and publish results (branch + draft PR + artifacts) while keeping the agent backend swappable and the environment isolated per run. Research confirmed Docker-outside-of-Docker orchestration, env-var-only secret injection, optional StatsD hooks, and seven-day artifact retention as the baseline approach.

## Technical Context

**Language/Version**: Python 3.11 for workers and job environment shell tooling  
**Primary Dependencies**: Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL result backend, Codex CLI, Git CLI/GitHub CLI, Docker client/SDK  
**Storage**: PostgreSQL tables `spec_workflow_runs` and `spec_workflow_task_states`; artifacts persisted under named Docker volume `speckit_workspaces` (optionally mirrored to object storage)  
**Testing**: pytest suites (unit/integration), Celery chain integration tests, docker-compose smoke tests  
**Target Platform**: Containerized Linux services deployed via Docker Compose / Kubernetes nodes with Docker socket access  
**Project Type**: Backend workflow automation (Celery worker plus orchestration library)  
**Performance Goals**: ≥95% of runs complete Spec Kit phases and publish artifacts within 20 minutes; structured status emitted for 100% of tasks  
**Constraints**: Secrets must remain ephemeral (env injection only), job containers cleaned after run, deterministic branch naming, retries with backoff, network egress limited to GitHub/Codex endpoints  
**Scale/Scope**: Initial capacity sized for low concurrency (1–2 simultaneous runs) with roadmap to scale via worker count; supports per-run workspace isolation and artifact retention ≥7 days

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Constitution file contains placeholders only; no enforceable principles defined. Recorded governance gap in research and proceeding with default compliance stance.
- Post-design review confirmed feature outputs (data model, contracts, quickstart) stay within the placeholder governance allowance.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
celery_worker/
├── speckit_worker.py         # Celery worker entrypoint/orchestration
└── __init__.py

moonmind/workflows/speckit_celery/
├── orchestrator.py           # Workflow coordination + context handling
├── tasks.py                  # Celery task definitions for phases
├── repositories.py           # Persistence adapters
├── models.py                 # Pydantic schemas for run/task state
└── __init__.py

docs/
└── SpecKitAutomation.md      # Technical design source material

docker-compose.yaml           # Service orchestration (worker, rabbitmq, api)
tools/
└── get_action_status.py      # Operational utilities (unchanged but reference for monitoring)
```

**Structure Decision**: Extend existing Celery worker and workflow orchestration modules under `celery_worker/` and `moonmind/workflows/speckit_celery/`, augmenting documentation and compose definitions without introducing new top-level packages.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |

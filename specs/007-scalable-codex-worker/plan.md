# Implementation Plan: Scalable Codex Worker

**Branch**: `007-scalable-codex-worker` | **Date**: 2025-11-27 | **Spec**: [specs/007-scalable-codex-worker/spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-scalable-codex-worker/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

This feature implements a dedicated, scalable Celery worker service (`celery_codex_worker`) responsible for executing Codex-specific tasks (submission, polling, patch application). It uses a shared named Docker volume (`codex_auth_volume`) to persist OAuth credentials and configuration across container restarts and replicas, ensuring non-interactive operation via a pre-configured `approval_policy = "never"`.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11
**Primary Dependencies**: Celery (Work Queue), Docker (Containerization), Codex CLI (Tooling)
**Storage**: Docker Volume (`codex_auth_volume`), Redis (Message Broker)
**Testing**: Manual verification via `quickstart.md` flows; Integration tests for worker startup.
**Target Platform**: Linux / Docker Compose
**Project Type**: Backend Infrastructure
**Performance Goals**: Horizontal scalability for Codex tasks; Zero interference with default queue.
**Constraints**: Worker must fail fast if unauthenticated.
**Scale/Scope**: Single shared volume, N worker replicas.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Core Principles**:
    - **Modularity**: Dedicated worker isolates Codex dependencies and load.
    - **Simplicity**: Uses standard Docker volumes and Celery queues; no complex custom orchestration.
    - **Testability**: Distinct startup behaviors (crash vs run) are verifiable.
- **Note**: The constitution file provided is a template (`.specify/memory/constitution.md`), so strict specific gate checking against project-specific rules is bypassed, but general best practices are followed.

## Project Structure

### Documentation (this feature)

```text
specs/007-scalable-codex-worker/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # N/A (No new API endpoints)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
# Infrastructure & Configuration
docker-compose.yaml           # Add celery_codex_worker service & volume
celery_worker/
└── speckit_worker.py         # Ensure queue routing config support

# Scripts
tools/
└── codex_worker_entrypoint.sh # (Optional) Pre-flight check script if complex
```

**Structure Decision**: Add a new service definition to `docker-compose.yaml` and potentially `docker-compose.override.yaml`. No new Python packages required, just configuration of the existing `celery_worker` module.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | | |
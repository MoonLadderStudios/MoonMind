# Implementation Plan: Gemini CLI Worker

**Branch**: `008-gemini-cli-worker` | **Date**: 2025-11-30 | **Spec**: [specs/008-gemini-cli-worker/spec.md](../spec.md)
**Input**: Feature specification from `/specs/008-gemini-cli-worker/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement a dedicated Celery worker service (`celery_gemini_worker`) listening on a `gemini` queue, utilizing a shared Docker volume (`gemini_auth_volume`) for persistent authentication and configuration. The worker will run in a container with the `@google/gemini-cli` installed from the public npm registry.

## Technical Context

**Language/Version**: Python 3.11+ (matches existing `api_service` and workers)
**Primary Dependencies**: 
- `celery[librabbitmq]`: Task queue integration
- `@google/gemini-cli` (npm): CLI tool for Gemini interactions
- `docker`/`docker-compose`: Container orchestration
**Storage**: Docker named volume (`gemini_auth_volume`) for auth persistence
**Testing**: `pytest` for worker logic, integration tests for queue routing
**Target Platform**: Linux (Docker container)
**Project Type**: Backend Service / Worker
**Performance Goals**: Non-blocking execution of Gemini tasks; independent scaling
**Constraints**: Must run non-interactively; must use public npm registry
**Scale/Scope**: Single worker instance initially, scalable via Docker Compose

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Library-First**: N/A (Infrastructure/Worker setup)
- **CLI Interface**: The worker wraps a CLI tool (`gemini`), adhering to the principle of using CLI interfaces.
- **Test-First**: Unit tests for the worker logic and integration tests for the container setup will be required.
- **Integration Testing**: Verification of volume mounting and queue routing is essential.

## Project Structure

### Documentation (this feature)

```text
specs/008-gemini-cli-worker/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
# Infrastructure & Config
docker-compose.yaml                  # Add celery_gemini_worker service & gemini_auth_volume

# Worker Implementation
celery_worker/
├── __init__.py
└── speckit_worker.py                # Existing worker entrypoint (reuse or extend)

# Docker Build
api_service/
├── Dockerfile                       # Update to install @google/gemini-cli
└── config.template.toml             # Check if Gemini needs similar config
```

**Structure Decision**: Reuse `celery_worker` package and `api_service/Dockerfile` by adding build args and service definitions, consistent with the `codex` worker pattern.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (None)    |            |                                     |
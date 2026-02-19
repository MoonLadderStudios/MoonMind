# Implementation Plan: Agent Queue Artifact Upload (Milestone 2)

**Branch**: `010-agent-queue-artifacts` | **Date**: 2026-02-13 | **Spec**: `specs/010-agent-queue-artifacts/spec.md`
**Input**: Feature specification from `/specs/010-agent-queue-artifacts/spec.md`

## Summary

Implement Milestone 2 from `docs/TaskQueueSystem.md` by adding artifact ingestion and retrieval for queue jobs: multipart upload endpoint, job-scoped artifact storage root, metadata persistence (`agent_job_artifacts`), artifact list/download endpoints, and validation tests for traversal and upload-size enforcement.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, SQLAlchemy ORM, Alembic, Pydantic, Starlette UploadFile handling, pytest  
**Storage**: PostgreSQL (`agent_job_artifacts` metadata table) + local filesystem artifact root (`var/artifacts/agent_jobs`)  
**Testing**: Unit tests via `./tools/test_unit.sh`  
**Target Platform**: Linux containers and local dev shells running MoonMind API service  
**Project Type**: Backend API + persistence + filesystem storage  
**Performance Goals**: Artifact upload/list/download remains deterministic and enforces bounded upload size to avoid resource exhaustion  
**Constraints**: Runtime code changes required; preserve queue auth patterns; enforce per-job path isolation and traversal defense; milestone scope excludes worker daemon and MCP tool surface  
**Scale/Scope**: Extend existing queue router/service/repository with artifact operations, add one migration for artifact metadata table, and add focused tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` contains placeholders without enforceable MUST/SHOULD rules.
- No additional constitutional gates can be objectively evaluated.

**Gate Status**: PASS WITH NOTE. Proceeding under project instructions and AGENTS constraints.

## Project Structure

### Documentation (this feature)

```text
specs/010-agent-queue-artifacts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── agent-queue-artifacts.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/agent_queue.py                         # Add upload/list/download routes
├── migrations/versions/202602130002_agent_job_artifacts.py
└── main.py                                            # Router already registered in milestone 1

moonmind/
├── config/settings.py                                 # Add AGENT_JOB_ARTIFACT_ROOT + size limit settings
├── schemas/agent_queue_models.py                      # Add artifact request/response schemas
└── workflows/agent_queue/
    ├── models.py                                      # Add AgentJobArtifact ORM model
    ├── repositories.py                                # Add artifact metadata CRUD and job-scoped queries
    ├── service.py                                     # Add artifact validation and orchestration
    └── storage.py                                     # Job-scoped file storage helper with traversal checks

tests/
└── unit/
    ├── api/routers/test_agent_queue_artifacts.py      # Upload/list/download and error-path tests
    ├── workflows/agent_queue/test_artifact_repositories.py
    ├── workflows/agent_queue/test_artifact_storage.py
    └── config/test_settings.py                        # Artifact settings defaults and env overrides
```

**Structure Decision**: Extend the existing queue stack added in Milestone 1 using the same layered pattern (schemas -> repository/service -> router), while introducing a dedicated storage helper for artifact filesystem safety.

## Phase 0: Research Plan

1. Review existing `ArtifactStorage` traversal protection pattern in `moonmind/workflows/speckit_celery/storage.py` and adapt for job-scoped queue artifacts.
2. Define artifact metadata schema and relationship to `agent_jobs`.
3. Define upload size enforcement strategy and where to validate before persistence.
4. Define API behavior for artifact ownership (`jobId` + `artifactId` pairing) and download response headers.

## Phase 1: Design Outputs

- `research.md`: decisions for storage helper, metadata table design, and size validation approach.
- `data-model.md`: `AgentJobArtifact` entity, relationships, and validation invariants.
- `contracts/agent-queue-artifacts.openapi.yaml`: upload/list/download endpoint contract.
- `contracts/requirements-traceability.md`: one row per `DOC-REQ-*` with implementation and validation strategy.
- `quickstart.md`: local validation flow for artifact upload/list/download and security checks.

## Post-Design Constitution Re-check

- Design includes runtime code and validation tests as required.
- No new constitution directives surfaced.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

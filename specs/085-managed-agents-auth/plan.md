# Implementation Plan: Managed Agents Authentication

**Branch**: `001-managed-agents-auth` | **Date**: 2026-03-14 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-managed-agents-auth/spec.md`

**Note**: This template is filled in by the `/agentkit.plan` command.

## Summary

Implement a Temporal-based AuthProfileManager singleton workflow to lease, release, and throttle OAuth and API-key authentication profiles for managed agent runtimes, backed by a persistent PostgreSQL registry, enabling strict rate limiting and concurrent account management.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: SQLAlchemy (Async), Temporalio, FastAPI, Pydantic  
**Storage**: PostgreSQL (`api_service/db/models.py`)  
**Testing**: pytest  
**Target Platform**: Linux server (Docker)  
**Project Type**: backend  
**Performance Goals**: Support high-throughput slot requests (<50ms workflow resolution)  
**Constraints**: Zero leaked credentials in execution logs or subprocess environments  
**Scale/Scope**: Manage ~1-100 overlapping agents and dozens of profiles  

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Design complies with Temporal Workflow constraints (no I/O in workflow).
- Uses dependency injection.
- Database operations are strictly in backend API/services layer, outside Temporal workflow execution.

## Project Structure

### Documentation (this feature)

```text
specs/001-managed-agents-auth/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── requirements-traceability.md
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
api_service/
├── db/
│   ├── models.py                   # ManagedAgentAuthProfile model
│   └── schemas/
│       └── auth_profiles.py        # Pydantic models for REST UI
├── api/
│   └── routers/
│       └── auth_profiles.py        # CRUD endpoints
├── migrations/
│   └── versions/

moonmind/
├── workflows/
│   └── auth_profile/               # New module for the singleton manager
│       ├── manager.py
│       └── contracts.py
├── agents/
│   └── base/
│       └── adapter.py              # Environment shaping logic integration
```

**Structure Decision**: Single repository structure extending the existing `api_service` and `moonmind` packages. Changes localized to Temporal workflows under `moonmind/workflows/auth_profile` and DB models/API in `api_service`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations.

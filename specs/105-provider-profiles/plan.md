# Implementation Plan: Provider Profiles Migration

**Branch**: `105-provider-profiles` | **Date**: 2026-03-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/105-provider-profiles/spec.md`

## Summary

This feature migrates the Auth Profile system to the Provider Profile system. The primary goal is to support multi-provider, multi-mode credentials for managed agent runtimes (like Claude Code with Anthropic/MiniMax/Z.AI) by renaming the backend tables, separating provider and runtime identity, and implementing a strict environment layering model.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Temporal, SQLAlchemy, Alembic  
**Storage**: PostgreSQL (`managed_agent_provider_profiles` table)  
**Testing**: `pytest` (unit/integration boundaries)  
**Target Platform**: Linux server / Docker  
**Project Type**: backend  
**Performance Goals**: N/A (standard DB CRUD and Temporal dispatch)  
**Constraints**: Zero downtime/loss of credential access; strict secret redaction  
**Scale/Scope**: System-wide backend component affecting all managed agent dispatches.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. Orchestrate, Don't Recreate**: No logic handles agent rebuilding; purely an orchestration change for env vars/secrets.
- [x] **II. One-Click Agent Deployment**: No new cloud dependencies. Works with existing local DB.
- [x] **III. Avoid Vendor Lock-In**: Enables explicit abstraction for multiple vendors (Anthropic, OpenAI, MiniMax).
- [x] **VII. Powerful Runtime Configurability**: Provider config is entirely DB-driven and configurable per-slot, rather than hardcoded.
- [x] **IX. Resilient by Default**: Maintains existing failure semantics but adds `clear_env_keys` to prevent silent provider fallback.
- [x] **XI. Spec-Driven Development Is the Source of Truth**: This plan derives entirely from the canonical docs and spec.
- [x] **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: The migration plan explicitly replaces `AuthProfileManager` and `managed_agent_auth_profiles` atomically. No compatibility aliases or dual-read paths.

## Project Structure

### Documentation (this feature)

```text
specs/105-provider-profiles/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/requirements-traceability.md
└── tasks.md
```

### Source Code

```text
api_service/
├── api/routers/            # API endpoints for profile CRUD
├── core/models/            # SQLAlchemy schemas
└── data/                   # Initial seeds
moonmind/
├── workflows/temporal/     # Temporal workflow definitions for Manager and AgentRun
├── runtime/                # ManagedRuntimeLauncher and environment layering logic
└── schema/                 # Base execution contract types (AgentExecutionRequest)
tests/                      # Unit models, CLI tests, temporal workflow tests
```

**Structure Decision**: Single Python monorepo structure spanning `api_service/` and `moonmind/`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None observed | N/A | N/A |

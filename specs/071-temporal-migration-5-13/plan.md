# Implementation Plan: Temporal Migration Task 5.13 (Local Dev Bring-up & E2E Test)

**Branch**: `071-temporal-migration-5-13` | **Date**: 2026-03-09 | **Spec**: `/specs/071-temporal-migration-5-13/spec.md`
**Input**: Feature specification from `/specs/071-temporal-migration-5-13/spec.md`

**Note**: This template is filled in by the `/agentkit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

This feature implements the local development bring-up path and an end-to-end (E2E) test for the Temporal migration in MoonMind. It provides a seamless `docker compose up` experience to start all necessary services (Temporal Server, database, API, and workers), an automated E2E test script to validate task orchestration from creation to artifact generation, and a clean-state procedure to reset the environment between runs.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Docker Compose, Temporal Python SDK, pytest
**Storage**: PostgreSQL (Temporal DB), local disk / minio (artifacts)
**Testing**: pytest
**Target Platform**: Linux / Docker
**Project Type**: single
**Performance Goals**: N/A
**Constraints**: local-first Docker Compose deployment
**Scale/Scope**: 1 local development environment

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. One-Click Agent Deployment**: This feature specifically implements the one-click `docker compose up` local-first deployment.
- [x] **II. Avoid Vendor Lock-In**: Temporal is open source; E2E tests use standard Python/pytest.
- [x] **III. Own Your Data**: Relies on local Temporal instances and PostgreSQL.
- [x] **IV. Skills Are First-Class**: N/A for this infrastructure test.
- [x] **V. The Bittersweet Lesson**: E2E tests are the verification anchor.
- [x] **VI. Powerful Runtime Configurability**: `docker-compose.yaml` uses environment variables.
- [x] **VII. Modular and Extensible Architecture**: The E2E test interacts with public APIs, not internal worker details.
- [x] **VIII. Self-Healing by Default**: The E2E test validates the system's ability to complete tasks end-to-end.
- [x] **IX. Facilitate Continuous Improvement**: The E2E test asserts on final run outcomes and artifacts.
- [x] **X. Spec-Driven Development**: Spec, plan, and tasks files track this implementation.

## Project Structure

### Documentation (this feature)

```text
specs/071-temporal-migration-5-13/
├── plan.md              # This file (/agentkit.plan command output)
├── research.md          # Phase 0 output (/agentkit.plan command)
├── data-model.md        # Phase 1 output (/agentkit.plan command)
├── quickstart.md        # Phase 1 output (/agentkit.plan command)
├── contracts/           # Phase 1 output (/agentkit.plan command)
└── tasks.md             # Phase 2 output (/agentkit.tasks command - NOT created by /agentkit.plan)
```

### Source Code (repository root)

```text
src/
├── docker-compose.yaml
├── scripts/
│   ├── temporal_e2e_test.py
│   └── temporal_clean_state.sh
```

**Structure Decision**: A single project layout using existing files in `scripts/` and `docker-compose.yaml`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | N/A | N/A |

# Implementation Plan: Refactor Execution Service to Temporal Authority

**Branch**: `001-refactor-execution-service` | **Date**: 2026-03-08 | **Spec**: [link](./spec.md)
**Input**: Feature specification from `/specs/001-refactor-execution-service/spec.md`

## Summary

The objective is to refactor `ExecutionService` so that Temporal becomes the authoritative source of truth (as outlined in Temporal Migration Plan step 5). Operations like listing, details, signals, and cancellations must use Temporal APIs instead of local DB state checks. Local DB will transition into a synchronized projection cache.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Temporalio (Python SDK), SQLAlchemy  
**Storage**: PostgreSQL (for projection cache), Temporal backend  
**Testing**: pytest  
**Target Platform**: Linux server / Docker (MoonMind API service)  
**Project Type**: Single project (Backend API Service)  
**Performance Goals**: Avoid high latency on list/detail endpoints by effectively caching Temporal state.  
**Constraints**: Must fail gracefully when Temporal is unreachable. Changes must be verified by automated tests.  
**Scale/Scope**: API layer handling mission control operations.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. One-Click Agent Deployment**: PASS - Relying on existing Temporal server defined in docker-compose.
- **II. Avoid Vendor Lock-In**: PASS - Temporal SDK is used via `TemporalClientAdapter`.
- **III. Own Your Data**: PASS - State still persisted in DB as a read projection cache.
- **IV. Skills Are First-Class**: N/A - Execution service refactoring.
- **V. The Bittersweet Lesson**: PASS - `TemporalClientAdapter` acts as an isolator for Temporal code.
- **VI. Powerful Runtime Configurability**: PASS - Feature is controlled/driven by current connection settings.
- **VII. Modular and Extensible Architecture**: PASS - Core architecture remains consistent, just switching the source of truth.
- **VIII. Self-Healing by Default**: PASS - Temporal inherently provides better self-healing for workflows than local state tracking.
- **IX. Facilitate Continuous Improvement**: PASS - No impact on telemetry.
- **X. Spec-Driven Development**: PASS - Specifications are generated and followed.

## Project Structure

### Documentation (this feature)

```text
specs/001-refactor-execution-service/
├── plan.md              
├── research.md          
├── data-model.md        
├── quickstart.md        
├── contracts/
│   └── requirements-traceability.md
└── tasks.md             
```

### Source Code (repository root)

```text
api_service/
├── api/
│   └── routers/
│       └── executions.py
├── services/
│   └── temporal/
│       └── service.py   # Primary target for refactoring
└── db/
    └── models.py

tests/
└── unit/
    └── api/
        └── test_execution_service.py
```

**Structure Decision**: Modifying the existing API service files. `TemporalExecutionService` in `moonmind/workflows/temporal/service.py` is the primary module needing an update to use Temporal as the authority.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

## Remediation Gates (Prompt B)

## Prompt B Remediation Application (Step 12/16)

- Prompt B scope controls now explicitly require deterministic cross-artifact updates so scope gates stay aligned during downstream task execution.
- Added explicit Prompt B scope-control wording in `tasks.md` and aligned quality gate language so runtime/validation expectations are auditable.
- Added Prompt B remediation status language in `spec.md` to keep runtime-mode, traceability, and risk statements synchronized with planning/task artifacts.

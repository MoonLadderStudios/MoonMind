# Implementation Plan: Temporal API Consistency

**Branch**: `071-temporal-api-consistency` | **Date**: 2026-03-08 | **Spec**: `/specs/071-temporal-api-consistency/spec.md`
**Input**: Feature specification from `/specs/071-temporal-api-consistency/spec.md`

## Summary

Implement routing and data transformation for `/api/executions` (mapped from `/tasks/list?source=temporal` in the UI) and `/api/executions/{id}` to act as authoritative proxies to the Temporal server. This includes fetching visibility data and history directly from Temporal, handling `mm:` prefixed workflow IDs, and applying filters (`workflowType`, `entry`, `state`) using Temporal's Search Attributes.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, temporalio (Python SDK)
**Storage**: Temporal Server DB, Local PostgreSQL (for projection/syncing if applicable)
**Testing**: pytest
**Target Platform**: Linux Server (Docker Compose)
**Project Type**: Backend API Service
**Performance Goals**: <500ms response times for API listing relying on Temporal visibility queries
**Constraints**: Must leverage the Temporal Client Adapter; no local DB-only faking for `source=temporal` requests.
**Scale/Scope**: Small API layer refactoring, affecting execution reads and list endpoints.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. One-Click Agent Deployment**: PASS - Changes are contained in the existing FastAPI worker and Temporal service provided by `docker-compose.yaml`.
- **II. Avoid Vendor Lock-In**: PASS - The underlying system remains adaptable; using Temporal aligns with the current primary durable runtime orchestrator choice.
- **III. Own Your Data**: PASS - State remains accessible in Temporal's DB and the artifact system.
- **IV. Skills Are First-Class**: PASS - N/A (Does not impact skill registration).
- **V. The Bittersweet Lesson**: PASS - We are using `temporalio` which provides a stable client contract.
- **VI. Powerful Runtime Configurability**: PASS - Feature flags/sources (`source=temporal`) dictate runtime paths.
- **VII. Modular and Extensible Architecture**: PASS - The Temporal execution service acts as a clean boundary adapter.
- **VIII. Self-Healing by Default**: PASS - Accurate Temporal state ensures UI can correctly resume/retry or surface actionable failures.
- **IX. Facilitate Continuous Improvement**: PASS - Exposes accurate failures and states.
- **X. Spec-Driven Development**: PASS - We are generating the spec, plan, data models, and requirements mapping.

## Project Structure

### Documentation (this feature)

```text
specs/071-temporal-api-consistency/
├── plan.md              
├── research.md          
├── data-model.md        
├── quickstart.md        
├── contracts/           
└── tasks.md             
```

### Source Code (repository root)

```text
api_service/
├── api/
│   └── routers/
│       └── executions.py
├── core/
│   └── service.py (or similar Temporal execution service adapter)
tests/
└── unit/
    └── api/
        └── test_executions_temporal.py
```

**Structure Decision**: Extending the existing `api_service` components responsible for task/execution management to natively use Temporal capabilities.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations.

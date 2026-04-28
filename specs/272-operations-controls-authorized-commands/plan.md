# Implementation Plan: Operations Controls Exposed as Authorized Commands

**Branch**: `272-operations-controls-authorized-commands` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/272-operations-controls-authorized-commands/spec.md`

## Summary

Implement MM-542 by completing the Settings -> Operations worker-pause command path as an authorized, auditable operation. The existing UI already rendered worker pause/resume controls and deployment operation controls, and deployment operations already provided a typed command pattern. The runtime gap was the configured `/api/system/worker-pause` backend route and its operation service semantics. The plan adds a typed system operation router/service for worker pause and resume, persists compact non-secret operation state and audit events using existing settings tables, enforces backend authorization, invokes Temporal quiesce/resume signals where appropriate, and verifies the path with explicit unit and integration tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/system_operations.py`, `api_service/services/system_operations.py`, `frontend/src/components/settings/OperationsSettingsSection.tsx`, `tests/integration/temporal/test_system_operations_api.py` | complete; preserve route/service and UI command surface | unit + integration |
| FR-002 | implemented_verified | service snapshot returns system, metrics, audit, and signal status; UI test covers worker operation metadata | complete; maintain response/UI assertions | unit UI + API |
| FR-003 | implemented_verified | service validation requires confirmation for disruptive pause/resume commands; UI submits confirmation metadata | complete; maintain validation and UI tests | unit API + UI |
| FR-004 | implemented_verified | `WorkerOperationCommand` and audit persistence in `api_service/services/system_operations.py`; API/service tests cover command metadata | complete; preserve idempotency, status, audit, and result metadata | unit API |
| FR-005 | implemented_verified | POST route enforces admin authorization when auth is enabled; API test covers non-admin rejection | complete; keep backend authorization authoritative | unit API |
| FR-006 | implemented_verified | API test proves non-admin POST is rejected without subsystem invocation | complete; maintain no-side-effect rejection test | unit API |
| FR-007 | implemented_verified | system operations service delegates quiesce/resume to Temporal service methods and keeps Settings presentation-only | complete; maintain service boundary assertions | unit API + integration_ci |
| FR-008 | implemented_verified | route/service return normalized signal and error statuses with sanitized validation/unavailable responses | complete; maintain result/error mapping tests | unit API |
| FR-009 | implemented_verified | `SettingsAuditEvent` stores non-secret worker operation metadata and latest audit projection is sanitized | complete; maintain audit sanitization tests | unit API |
| FR-010 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, verification evidence, and traceability checks preserve `MM-542` | complete; keep traceability checks in final verification | traceability check |
| SC-001 | implemented_verified | integration and UI/API tests cover Settings worker operation state and metadata from the real route contract | complete; maintain unit UI + API evidence | unit UI + API |
| SC-002 | implemented_verified | backend confirmation enforcement and UI confirmation submission are covered by tests | complete; maintain confirmation tests | unit API + UI |
| SC-003 | implemented_verified | `tests/unit/api/routers/test_system_operations.py` covers unauthorized direct submission rejection | complete; maintain authorization regression test | unit API |
| SC-004 | implemented_verified | service/API tests cover audit persistence and latest action response | complete; maintain audit persistence tests | unit API |
| SC-005 | implemented_verified | service/API tests cover Temporal signal delegation; integration test covers configured route shape | complete; maintain subsystem boundary tests | unit API + integration_ci |
| SC-006 | implemented_verified | traceability check covers `MM-542`, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-014 across artifacts | complete; keep final traceability check | traceability check |
| DESIGN-REQ-002 | implemented_verified | worker operation route is a command facade over Temporal operation service and Settings remains a presentation surface | complete; preserve subsystem ownership boundary | unit + integration |
| DESIGN-REQ-013 | implemented_verified | UI/API/service tests cover state, confirmation, command metadata, result status, and sanitized audit feedback | complete; maintain metadata/audit tests | unit API + UI |
| DESIGN-REQ-014 | implemented_verified | route implements permission, confirmation, subsystem invocation, status recording, audit event, and resume action | complete; maintain authorization and integration tests | unit API + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK service boundary, React, TanStack Query, Zod, Vitest  
**Storage**: Existing `settings_overrides` and `settings_audit_events` tables; no new persistent tables  
**Unit Testing**: pytest via `./tools/test_unit.sh`; Vitest via `./tools/test_unit.sh --ui-args <path>` or `npm run ui:test -- <path>`  
**Integration Testing**: pytest integration suite via `./tools/test_integration.sh`; targeted `integration_ci` route/service test for API boundary  
**Target Platform**: Linux server / browser-based Mission Control  
**Project Type**: Web application with FastAPI backend, Temporal orchestration services, and React frontend  
**Performance Goals**: Operations snapshot and command responses remain lightweight and avoid long-running blocking calls; UI polling interval remains bounded by existing config  
**Constraints**: No raw credentials in audit/history; backend authorization is authoritative; operational subsystems retain command semantics; no new database tables unless existing audit/override tables prove insufficient  
**Scale/Scope**: Single Settings -> Operations story focused on worker pause/resume command flow, with deployment controls preserved as existing adjacent operations evidence

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The backend route delegates quiesce/resume behavior to Temporal service boundaries rather than reimplementing workflow behavior in Settings.
- II. One-Click Agent Deployment: PASS. No external SaaS or new infrastructure dependency is introduced.
- III. Avoid Vendor Lock-In: PASS. The feature is MoonMind internal operations behavior and does not add a provider-specific dependency.
- IV. Own Your Data: PASS. Operation state and audit metadata remain in existing local MoonMind storage.
- V. Skills Are First-Class and Easy to Add: PASS. No executable tool contract changes are required.
- VI. Replaceable Scaffolding: PASS. The route is a thin command facade over service contracts and is covered by tests.
- VII. Runtime Configurability: PASS. Existing worker-pause runtime config continues to provide endpoint locations to Mission Control.
- VIII. Modular Architecture: PASS. New logic is isolated in a system operations service/router.
- IX. Resilient by Default: PASS. Command submissions use idempotency keys, explicit statuses, and sanitized failure results.
- X. Facilitate Continuous Improvement: PASS. The feature produces verifiable tests and traceable artifacts.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md` and will drive TDD tasks.
- XII. Canonical Documentation Separation: PASS. No canonical doc migration checklist is added.
- XIII. Pre-Release Compatibility: PASS. No compatibility aliases or hidden fallback semantics are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/272-operations-controls-authorized-commands/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── system-worker-pause-api.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   ├── routers/
│   │   ├── system_operations.py
│   │   └── deployment_operations.py
│   └── schemas.py
├── services/
│   └── system_operations.py
└── main.py

frontend/src/components/settings/
├── OperationsSettingsSection.tsx
└── OperationsSettingsSection.test.tsx

tests/
├── unit/
│   ├── api/routers/test_system_operations.py
│   └── services/test_system_operations.py
└── integration/
    └── temporal/test_system_operations_api.py
```

**Structure Decision**: Use the existing FastAPI router/service pattern from deployment operations and the existing Settings Operations React component. Keep worker-pause behavior in a backend system operations boundary so Settings remains a command surface and not the owner of Temporal or worker semantics.

## Complexity Tracking

No constitution violations.

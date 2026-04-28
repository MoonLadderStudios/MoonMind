# Implementation Plan: Operations Controls Exposed as Authorized Commands

**Branch**: `272-operations-controls-authorized-commands` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/272-operations-controls-authorized-commands/spec.md`

## Summary

Implement MM-542 by completing the Settings -> Operations worker-pause command path as an authorized, auditable operation. The existing UI already renders worker pause/resume controls and deployment operation controls, and deployment operations already provide a typed command pattern. The missing runtime gap is the configured `/api/system/worker-pause` backend route and its operation service semantics. The plan adds a typed system operation router/service for worker pause and resume, persists compact non-secret operation state and audit events using existing settings tables, enforces backend authorization, invokes Temporal quiesce/resume signals where appropriate, and strengthens UI/API tests before implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `OperationsSettingsSection.tsx` renders Operations, worker controls, and deployment controls; `/api/system/worker-pause` is referenced but no route exists | add backend worker-pause operation route/service and preserve UI | unit + integration |
| FR-002 | partial | UI shows worker state, metrics, mode, reason, updated time, recent actions; lacks actor, pending/failure detail from backend route | extend response contract and UI assertions for authorization/last actor/failure/pending where available | unit UI + API |
| FR-003 | partial | UI validates pause/resume reason and confirms resume when not drained; deployment update/rollback confirmations exist | add explicit confirmation requirement to backend command payload for pause/quiesce and resume force cases | unit API + UI |
| FR-004 | missing | no worker-pause backend command object or persisted audit event for configured route | add command submission model, idempotency key, status, audit fields, and result metadata | unit API |
| FR-005 | partial | deployment operation route enforces admin authorization; worker-pause route is absent | add backend authorization to worker-pause route | unit API |
| FR-006 | missing | no route exists to reject unauthorized worker-pause invocation | add rejection tests and implementation with no side effect | unit API |
| FR-007 | partial | `TemporalExecutionService` has quiesce/resume signal methods; UI is presentation-only; route missing | route command through system operation service and Temporal signal methods | unit API + integration_ci |
| FR-008 | missing | no worker operation route result statuses exist | add `pending/succeeded/failed/unauthorized/conflicted/unavailable`-compatible status field and error mapping | unit API |
| FR-009 | partial | `SettingsAuditEvent` exists and deployment recent actions sanitize raw command logs | persist non-secret worker operation audit in settings audit events and return sanitized latest actions | unit API |
| FR-010 | implemented_unverified | `spec.md` preserves MM-542 brief | preserve key in plan/tasks/verification and final output | traceability check |
| SC-001 | partial | UI can render some worker state from mocked API | add real API contract and UI assertions for available metadata | unit UI + API |
| SC-002 | partial | reason is required client-side; confirmation only for unsafe resume | add backend confirmation enforcement for disruptive command classes | unit API + UI |
| SC-003 | missing | no worker-pause backend authorization tests | add unauthorized direct submission test | unit API |
| SC-004 | missing | no worker-pause operation audit route exists | add audit persistence/response tests | unit API |
| SC-005 | partial | deployment uses operation service; worker-pause should use Temporal service signals | add service boundary assertions | unit API + integration_ci |
| SC-006 | implemented_unverified | traceability exists in spec | preserve across generated artifacts | traceability check |
| DESIGN-REQ-002 | partial | source ownership is documented; UI is only presentation, but route absent | implement route as command facade over Temporal operation service | unit + integration |
| DESIGN-REQ-013 | partial | deployment operation command metadata exists; worker controls lack backend command metadata | add worker operation metadata and audit response | unit API + UI |
| DESIGN-REQ-014 | partial | source Pause Workers flow has schemas but no route | implement permission, confirmation, subsystem invocation, status, audit, resume action | unit API + integration |

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

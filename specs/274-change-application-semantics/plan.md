# Implementation Plan: Change Application, Reload, Restart, and Recovery Semantics

**Branch**: `run-jira-orchestrate-for-mm-544-change-a-66e0b6f4` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:7d65d02a-6c47-4328-b4b3-1486da6438a4/repo/specs/274-change-application-semantics/spec.md`

## Summary

Implement MM-544 by making settings change application semantics explicit across backend descriptors, persisted change evidence, diagnostics, and Mission Control settings UI. The current repo already has settings descriptors, override persistence, audit rows, diagnostics, and generated settings UI, but apply mode is not first-class on descriptors, consumer-facing change event semantics are incomplete, and restart/pending activation plus restored-reference recovery behavior need stronger contracts and tests. The plan is TDD-first: add focused backend unit tests, API contract tests, and frontend Vitest coverage before implementing the missing metadata, event, diagnostics, and display behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/services/settings_catalog.py` validates known keys/scopes and builds descriptors; no descriptor-generation validation gate for apply metadata | add descriptor validation for apply semantics and affected subsystems | unit |
| FR-002 | partial | `apply_overrides()` validates key, scope, value shape, version, SecretRefs | extend validation evidence to include apply semantics and dependency visibility | unit + API |
| FR-003 | partial | descriptors expose `requires_reload`, `requires_worker_restart`, `requires_process_restart`, `applies_to`; no `apply_mode` field | add explicit descriptor `apply_mode` and registry metadata | unit + API + UI |
| FR-004 | partial | `SettingsAuditEvent` includes `event_type`, key, scope, actor, `apply_mode`, affected systems fields in read model, but write path does not populate apply mode as a first-class event contract | populate structured settings change event metadata from descriptor semantics | unit + API |
| FR-005 | partial | settings UI invalidates catalog after save; task defaults use settings values in existing view-model paths; no general consumer event contract | expose change event semantics and verify consumer-visible refresh/reload indicators | unit + integration |
| FR-006 | partial | diagnostics expose restart booleans and UI renders badges, but not current/pending value, active state, affected process/worker, and activation guidance | add restart activation diagnostics/read model and UI presentation | unit + API + UI |
| FR-007 | missing | no unified status model distinguishing immediate, next request/task/launch, reload, restart, manual operation | add apply mode state model and diagnostics | unit + API |
| FR-008 | partial | descriptors, diagnostics, and audit exist; runtime application behavior is only partly observable | make apply behavior observable through descriptor/event/diagnostic surfaces | unit + API + UI |
| FR-009 | implemented_unverified | service rejects raw secret-like values and audit redaction exists; no explicit backup contract path found | document/test backup-safe settings export shape or recovery diagnostic boundary if existing export exists | unit/integration |
| FR-010 | implemented_unverified | secret value APIs and settings tests avoid plaintext in overrides/audit | add MM-544-specific backup/recovery assertion against settings surfaces | unit + API |
| FR-011 | partial | missing SecretRefs and provider profiles produce diagnostics; OAuth volume restored-reference coverage not evident | add restored-reference diagnostics for missing provider profile/secret/OAuth refs as supported by current data model | unit + API |
| FR-012 | partial | diagnostics endpoint and launch-blocker diagnostics exist for secret/profile refs; no clear post-persistence preview boundary | add tests for late validation diagnostics and preview/apply response evidence | unit + API |
| FR-013 | missing | new spec preserves `MM-544`; downstream artifacts not complete yet | preserve `MM-544` in plan, tasks, verification, commit/PR text | artifact review |
| SC-001 | partial | descriptor booleans and applies_to exist | verify explicit apply mode and restart metadata coverage | unit + API |
| SC-002 | partial | audit rows exist | verify structured event fields are populated | unit + API |
| SC-003 | missing | no test proving consumer refresh/reload/pending activation outcomes for this story | add integration-level settings API/UI flow | integration |
| SC-004 | partial | UI renders reload/restart badges only | add pending activation display | UI |
| SC-005 | partial | secret redaction and missing ref diagnostics exist | add restored-reference and backup-safe assertions | unit + API |
| SC-006 | partial | validation tests cover writes and diagnostics; not all timing boundaries | add boundary-specific validation evidence | unit + API |
| SC-007 | missing | current artifacts preserve `MM-544`; implementation evidence pending | maintain traceability through remaining stages | artifact review |
| DESIGN-REQ-016 | partial | `SettingsCatalogService` validates writes and diagnostics; descriptor generation and preview/launch timing need explicit coverage | add validation gate/evidence tests | unit + API |
| DESIGN-REQ-019 | partial | descriptor booleans, diagnostics, UI badges, audit read model exist | add apply mode, structured change event, activation status | unit + API + UI |
| DESIGN-REQ-025 | partial | SecretRefs and provider profiles have sanitized diagnostics; no explicit backup/recovery surface found | add/verify backup-safe reference diagnostics | unit + API |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control settings UI  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, React, TanStack Query, Vitest, pytest  
**Storage**: Existing settings override and audit tables; existing managed secret, provider profile, and OAuth/session metadata tables only  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; targeted pytest for backend iteration; `npm run ui:test -- frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for focused UI iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; targeted API/Temporal-backed integration where settings changes cross runtime/operations boundaries  
**Target Platform**: MoonMind API service and Mission Control web UI  
**Project Type**: Web service plus React frontend  
**Performance Goals**: Settings catalog, diagnostics, and effective-value reads should remain bounded to existing small registry and batch DB access patterns; no per-setting query regression  
**Constraints**: No raw managed secret plaintext in settings backup/recovery/audit/diagnostic surfaces; no new persistent storage unless existing tables cannot express the required event/diagnostic evidence; preserve pre-release compatibility policy by updating internal contracts directly  
**Scale/Scope**: One independently testable settings runtime story covering descriptor metadata, change evidence, diagnostics, UI visibility, and restored-reference safety

## Constitution Check

| Principle | Gate | Status | Evidence |
| --- | --- | --- | --- |
| I. Orchestrate, Don't Recreate | Settings semantics must support existing runtimes without adding agent-specific coupling | PASS | Plan extends shared Settings surfaces only |
| II. One-Click Agent Deployment | No mandatory external service or new setup dependency | PASS | Reuses existing API/UI/database baseline |
| III. Avoid Vendor Lock-In | Provider/OAuth references stay generic and adapter-owned | PASS | Settings stores refs and diagnostics, not provider clients |
| IV. Own Your Data | Settings events/diagnostics remain operator-visible and locally stored | PASS | Uses existing DB/API surfaces |
| V. Skills Are First-Class and Easy to Add | No skill runtime mutation | PASS | Out of scope |
| VI. Replaceable Scaffolding | Contracts focus on observable behavior and tests | PASS | Plan adds contract and boundary tests |
| VII. Runtime Configurability | Feature improves runtime setting observability | PASS | Core story |
| VIII. Modular Architecture | Changes stay in settings service/router/UI boundaries | PASS | No cross-cutting rewrite planned |
| IX. Resilient by Default | Restart/reload/pending state and broken refs become explicit | PASS | Core story |
| X. Continuous Improvement | Verification artifacts preserve outcome and Jira traceability | PASS | FR-013/SC-007 |
| XI. Spec-Driven Development | Spec, plan, and tasks drive work | PASS | `specs/274-change-application-semantics` created |
| XII. Canonical Docs Separate Desired State | Source docs remain desired state; implementation artifacts live under `specs/` | PASS | No docs rewrite planned |
| XIII. Pre-Release Compatibility | Internal settings contracts can be updated directly; no compatibility aliases | PASS | Plan updates current contracts rather than adding legacy fallback |

## Project Structure

### Documentation (this feature)

```text
specs/274-change-application-semantics/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-application-semantics.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
└── services/settings_catalog.py

frontend/src/components/settings/
├── GeneratedSettingsSection.tsx
└── GeneratedSettingsSection.test.tsx

tests/
├── unit/services/test_settings_catalog.py
├── unit/api_service/api/routers/test_settings_api.py
└── integration/temporal/test_system_operations_api.py
```

**Structure Decision**: Extend the existing settings service, settings API router, generated settings UI component, and their current tests. Add integration coverage only where the story crosses settings into operations/runtime behavior.

## Complexity Tracking

No constitution violations requiring complexity exceptions.

# Implementation Plan: Settings Authorization Audit Diagnostics

**Branch**: `273-settings-auth-audit-diagnostics` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/273-settings-auth-audit-diagnostics/spec.md`

## Summary

Implement the MM-543 runtime story by adding backend-enforced settings permission categories, exposing a redacted settings audit read surface, enriching settings audit records and diagnostics with policy-safe metadata, and validating that hidden frontend controls are not treated as authorization. Existing settings catalog, override persistence, audit table, SecretRef validation, and provider-profile diagnostics provide a partial foundation; the missing work is explicit permission modeling, audit retrieval/redaction behavior, and broader tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/services/settings_catalog.py` has scopes and audit metadata but no permission taxonomy | add settings permission constants and action mapping | unit + integration |
| FR-002 | missing | settings routes do not require setting permissions | enforce backend permissions on settings routes | integration |
| FR-003 | partial | `SettingsAuditEvent` stores core fields but lacks validation outcome, apply mode, affected systems | extend model/service output where schema already permits or add compact metadata fields if needed | unit |
| FR-004 | missing | no audit read endpoint | add `/settings/audit` with permission checks | integration |
| FR-005 | partial | secret refs redact on write; generic audit read redaction is absent | add audit output redactor for secret-like and descriptor-redacted values | unit + integration |
| FR-006 | partial | SecretRef entries can be redacted at write time | enforce SecretRef metadata visibility by permission | integration |
| FR-007 | partial | audit row has `redacted` boolean | expose redaction status consistently in audit responses | integration |
| FR-008 | partial | effective values include source and some diagnostics | add diagnostics endpoint/output for read-only, effective source, recent changes, validation, restart, and readiness blockers | unit + integration |
| FR-009 | partial | invalid scopes/values and missing secrets have structured errors | verify actionable fail-fast diagnostics across representative cases | unit + integration |
| FR-010 | implemented_unverified | SecretRef diagnostics do not resolve alternate secret sources | add regression tests for no fallback behavior | unit |
| FR-011 | missing | no explicit permission-denied tests for hidden-control bypass | add backend tests that direct calls are denied without permission | integration |
| FR-012 | implemented_unverified | server descriptors come from registry, but no malicious descriptor regression | add test that client-supplied descriptor metadata is ignored | integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-543 | preserve through tasks and verification | final verify |
| DESIGN-REQ-014 | partial | settings route scopes and registry exist | same as FR-001/FR-002 | unit + integration |
| DESIGN-REQ-015 | partial | audit events exist but no audit API | same as FR-003 through FR-007 | unit + integration |
| DESIGN-REQ-018 | partial | diagnostics exist for secret refs/profile refs | same as FR-008 through FR-010 | unit + integration |
| DESIGN-REQ-025 | missing | no backend permission denial on settings routes | same as FR-011/FR-012 | integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, existing settings catalog service  
**Storage**: Existing SQLAlchemy database tables, including `settings_overrides`, `settings_audit_events`, `managed_secrets`, and provider profile rows  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: pytest integration tier via `./tools/test_integration.sh` for required compose-backed checks when needed  
**Target Platform**: MoonMind API/control-plane service  
**Project Type**: FastAPI backend service with existing Mission Control consumers  
**Performance Goals**: Audit and diagnostics responses should remain bounded to requested key/scope filters and avoid per-setting database queries in list paths  
**Constraints**: No raw credentials in logs, comments, audit output, diagnostics, or test assertions; backend authorization is authoritative; compatibility-sensitive schema changes require migration and tests  
**Scale/Scope**: One settings security story covering backend routes, service models, persistence output, and focused tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS. Work stays in existing settings API/service boundaries.
- II One-Click Agent Deployment: PASS. No new required external services.
- III Avoid Vendor Lock-In: PASS. Settings/audit behavior is vendor-neutral.
- IV Own Your Data: PASS. Audit and diagnostics remain local database/control-plane data.
- V Skills Are First-Class: PASS. No skill runtime mutation.
- VI Replaceable Scaffolding: PASS. Thin service contracts and tests anchor behavior.
- VII Runtime Configurability: PASS. Uses existing settings catalog/config surfaces.
- VIII Modular Architecture: PASS. Changes are scoped to settings router/service/models/tests.
- IX Resilient by Default: PASS. Fail-fast diagnostics and explicit errors are required.
- X Continuous Improvement: PASS. Verification artifacts preserve evidence.
- XI Spec-Driven Development: PASS. This plan follows `spec.md`.
- XII Canonical Documentation Separation: PASS. Execution notes remain under `specs/273-settings-auth-audit-diagnostics`.
- XIII Pre-release Compatibility Policy: PASS. No compatibility aliases are planned; any schema additions are direct and tested.

## Project Structure

### Documentation (this feature)

```text
specs/273-settings-auth-audit-diagnostics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-audit-diagnostics-api.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
├── migrations/versions/
└── services/settings_catalog.py

tests/
├── unit/api_service/api/routers/test_settings_api.py
└── unit/services/test_settings_catalog.py
```

**Structure Decision**: Keep the story inside the existing settings API and settings catalog service. Add focused tests beside the current settings API/service tests.

## Complexity Tracking

No constitution violations.

# Implementation Plan: Settings HTTP API Surface

**Branch**: `352-settings-http-api-surface` | **Date**: 2026-05-14 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:d0605b15-f8b2-40f8-9e2f-a9ea20825eef/repo/specs/352-settings-http-api-surface/spec.md`

**Note**: `scripts/bash/setup-plan.sh --json` was attempted, but this checkout does not contain that helper path. Planning continued from `.specify/feature.json`, which points to `specs/352-settings-http-api-surface`.

## Summary

MM-657 requires the Settings System to expose one coherent HTTP API surface for catalog reads, effective reads, user/workspace writes, resets, validation, preview, and audit. Repo analysis found existing FastAPI routes and service behavior for catalog, effective values, update, reset, diagnostics, and audit in `api_service/api/routers/settings.py` and `api_service/services/settings_catalog.py`, with focused unit and hermetic integration coverage. The main implementation gap is the absence of first-class `POST /api/v1/settings/validate` and `POST /api/v1/settings/preview` endpoints that return validation results, effective-value diffs, dependency warnings, and reload requirements without committing. Secondary gaps are contract-level tests proving all three catalog sections in one API path and a complete documented error-envelope matrix for MM-657.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `GET /settings/catalog` in `api_service/api/routers/settings.py`; catalog section/scope API tests in `tests/unit/api_service/api/routers/test_settings_api.py` | preserve behavior | final verify |
| FR-002 | implemented_unverified | `SettingsCatalogBuilder` groups by section/category and filters exposed registry entries; builder tests cover section filtering | add API-level proof that all three top-level sections are represented as expected | unit + integration |
| FR-003 | implemented_verified | `GET /settings/effective` and `GET /settings/effective/{key}` routes exist with unit and integration coverage | preserve behavior | final verify |
| FR-004 | implemented_verified | Effective values include source, source explanation, value version, default/read-only/reload metadata; `test_settings_effective_values_contract.py` covers metadata and operator lock | preserve behavior | final verify |
| FR-005 | implemented_verified | `PATCH /settings/{scope}` accepts `changes`, `expected_versions`, and `reason`; `SettingsCatalogService.apply_overrides()` persists user/workspace overrides | preserve behavior | final verify |
| FR-006 | implemented_verified | Stale version path returns `version_conflict` and is covered by unit and integration tests | preserve behavior | final verify |
| FR-007 | implemented_verified | Successful patch returns changed effective values and records audit events; audit metadata tests exist | preserve behavior | final verify |
| FR-008 | implemented_verified | `DELETE /settings/{scope}/{key}` returns inherited effective value; reset tests cover preserving secrets and audit | preserve behavior | final verify |
| FR-009 | missing | No `POST /settings/validate` route found; service has validation helpers but no public validation API | add validation route and response model without persistence | unit + integration |
| FR-010 | partial | Service has `validate_effective_preview()` and diagnostics/readiness helpers, but no `POST /settings/preview` route, no committed API diff shape, and no route-level no-commit proof | add preview route, diff/warning/reload response shape, and no-commit coverage | unit + integration |
| FR-011 | implemented_verified | `GET /settings/audit` supports key/scope filters and bounded limit; audit endpoint tests cover filtering and scoping | preserve behavior | final verify |
| FR-012 | implemented_verified | Audit redaction service and endpoint tests cover descriptor policy, SecretRef metadata permission, and secret-like values | preserve behavior | final verify |
| FR-013 | partial | Structured `SettingsError` exists and tests cover many codes; documented MM-657 matrix still needs route-level proof for validate/preview, `requires_confirmation`, and scope/read-only naming consistency | add error-matrix tests and align new routes with existing envelope | unit + integration |
| FR-014 | implemented_unverified | Route permission checks exist for catalog/effective/write/audit; sensitive audit metadata permission tests exist | add validation/preview permission checks and route tests | unit + integration |
| FR-015 | implemented_verified | Existing redaction and unsafe payload tests verify raw secret plaintext is not returned | preserve behavior and include validate/preview no-leak tests | unit + integration |
| FR-016 | partial | SecretRef and provider-profile diagnostics exist; missing OAuth volume and policy-blocked dependency behavior is only partially represented through existing diagnostics | include preview/readiness diagnostics for missing references and policy-blocked values where applicable | unit + integration |
| FR-017 | implemented_unverified | `spec.md` and this plan preserve MM-657 and the original preset brief | preserve traceability through plan, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_unverified | Catalog section/scope route exists; full three-section API proof should be added | add contract test for grouped sections | integration |
| SCN-002 | implemented_verified | Effective list/key routes and tests prove scoped source explanations | preserve behavior | final verify |
| SCN-003 | implemented_verified | Update route/service and audit tests prove valid writes, refreshed values, and audit records | preserve behavior | final verify |
| SCN-004 | implemented_verified | Reset route/service and tests prove inherited values after override deletion | preserve behavior | final verify |
| SCN-005 | missing | No public validation/preview endpoints exist | add validate and preview route tests first | unit + integration |
| SCN-006 | partial | Many structured errors exist; validate/preview and complete documented matrix are not yet proven | add error matrix coverage | unit + integration |
| SCN-007 | implemented_verified | Audit endpoint and redaction tests exist | preserve behavior | final verify |
| SCN-008 | implemented_unverified | Current artifacts preserve MM-657; final verification not generated | preserve traceability | final verify |
| SC-001 | partial | Existing tests cover catalog, effective, update, reset, audit; validate and preview API families are missing | add validate/preview tests and contract coverage | unit + integration |
| SC-002 | implemented_unverified | Builder supports three sections; API-level all-section assertion is missing | add section coverage test | integration |
| SC-003 | implemented_verified | Stale update tests prove `version_conflict` and no commit | preserve behavior | final verify |
| SC-004 | partial | Error envelope tests exist for many codes; not every MM-657 documented code is proven | add documented error matrix where supported; fail-fast any unsupported code decision | unit + integration |
| SC-005 | implemented_verified | Audit redaction tests prove sensitive values are hidden and metadata preserved | preserve behavior | final verify |
| SC-006 | implemented_unverified | Spec and plan preserve MM-657 and coverage IDs; final verification pending | preserve traceability through downstream artifacts | final verify |
| DESIGN-REQ-001 | implemented_unverified | Catalog section/scope behavior exists; full MM-657 three-section proof remains planned | add route contract coverage | integration |
| DESIGN-REQ-002 | implemented_verified | Effective list/key behavior and source explanation metadata exist | preserve behavior | final verify |
| DESIGN-REQ-003 | implemented_verified | User/workspace update behavior, expected versions, reason, and refreshed values exist | preserve behavior | final verify |
| DESIGN-REQ-004 | implemented_verified | Reset removes overrides and returns inherited effective values | preserve behavior | final verify |
| DESIGN-REQ-005 | missing | Public validation and preview endpoints are absent | add routes and contracts | unit + integration |
| DESIGN-REQ-006 | implemented_verified | Audit reads and redaction policy behavior exist | preserve behavior | final verify |
| DESIGN-REQ-007 | partial | Error envelope exists; complete MM-657 route matrix missing | add missing matrix coverage with new routes | unit + integration |
| DESIGN-REQ-008 | implemented_unverified | Existing route permissions cover current routes; validate/preview permissions missing with routes | add permission checks/tests for new routes | unit + integration |
| DESIGN-REQ-009 | implemented_verified | Audit and settings outputs redact secrets through descriptor policy and secret-like scanners | preserve behavior | final verify |
| DESIGN-REQ-010 | partial | SecretRef/provider-profile diagnostics exist; policy-blocked and OAuth-volume cases need preview/readiness proof or scoped rationale | add diagnostics tests for representative missing/policy-blocked dependencies | unit + integration |
| DESIGN-REQ-011 | implemented_verified | Version conflict behavior is implemented and tested | preserve behavior | final verify |
| DESIGN-REQ-012 | implemented_verified | Backend catalog exposure controls route writes and catalog output | preserve behavior | final verify |
| DESIGN-REQ-013 | implemented_verified | Scope checks reject unsupported write scopes and key/scope mismatches | preserve behavior | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, pytest, httpx ASGI transport, existing Settings catalog/service models  
**Storage**: Existing `settings_overrides`, settings audit, managed secret, and provider profile rows only; no new persistent table planned  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic `integration_ci`; focused iteration with `pytest tests/integration/api/test_settings_http_api_surface_contract.py tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci'`  
**Target Platform**: MoonMind API service in Linux containers  
**Project Type**: Backend FastAPI web service with API-visible settings contracts  
**Performance Goals**: Validation and preview evaluate only submitted keys plus affected effective-value dependencies; no external provider calls during request handling  
**Constraints**: Preserve secret hygiene; validation/preview must not persist changes; structured errors must use the existing `SettingsError` envelope; no compatibility aliases for unsupported internal contracts; backend authorization remains authoritative  
**Scale/Scope**: Single Settings HTTP API story covering current catalog, effective value, override, reset, validation, preview, and audit surfaces

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Result |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | Keep work in MoonMind settings control-plane contracts; do not change agent cognition. | PASS |
| II. One-Click Agent Deployment | Use existing local API/database surfaces and test tooling; no new external dependency. | PASS |
| III. Avoid Vendor Lock-In | Settings API remains provider-neutral through SecretRef and provider-profile references. | PASS |
| IV. Own Your Data | Settings, preview, validation, and audit data remain in operator-controlled API/database surfaces. | PASS |
| V. Skills Are First-Class | No skill runtime or skill source changes are planned. | PASS |
| VI. Scientific Method | Plan requires unit and integration tests before implementation changes for missing routes. | PASS |
| VII. Runtime Configurability | Story strengthens runtime settings visibility and change control. | PASS |
| VIII. Modular Architecture | Work stays inside existing settings router/service/test boundaries. | PASS |
| IX. Resilient by Default | Validate/preview routes must fail visibly and avoid partial commits. | PASS |
| X. Continuous Improvement | Plan preserves traceability and deterministic evidence. | PASS |
| XI. Spec-Driven Development | `spec.md`, this plan, and downstream tasks remain the source of truth for MM-657. | PASS |
| XII. Canonical Documentation Separation | Planning details remain under `specs/352-settings-http-api-surface/`, not canonical docs. | PASS |
| XIII. Pre-Release Velocity | Missing route/error contracts should be implemented cleanly without compatibility shims. | PASS |

Post-Phase 1 re-check: PASS. The generated research, data model, API contract, and quickstart keep the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/352-settings-http-api-surface/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-http-api-surface.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
└── services/settings_catalog.py

tests/
├── unit/
│   ├── api_service/api/routers/test_settings_api.py
│   └── services/test_settings_catalog.py
└── integration/api/
    ├── test_settings_effective_values_contract.py
    └── test_settings_overrides_contract.py
```

**Structure Decision**: Implement MM-657 in the existing backend settings service and API router boundaries. Keep persistence in current settings/audit/provider/secret tables. Add or update tests in the current unit and hermetic integration locations; no frontend work is planned unless implementation later exposes a necessary Mission Control rendering gap.

## Complexity Tracking

No constitution violations or extra architectural complexity are required for this planning stage.

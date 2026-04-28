# Implementation Plan: Settings Catalog and Effective Values

**Branch**: `267-settings-catalog-effective-values` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/267-settings-catalog-effective-values/spec.md`

## Summary

Implement `MM-537` as a runtime read-side settings contract. Add a backend-owned explicit settings registry, catalog descriptors, effective-value resolution from loaded application settings and deployment environment, non-secret diagnostics for null or unresolved SecretRef states, and structured errors for unknown/unexposed/read-only settings. Validate with service unit tests plus API route tests. Scoped override persistence and mutable settings remain out of scope for this story and are deferred to `MM-538`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/services/settings_catalog.py`, `api_service/api/routers/settings.py`, route tests | complete | unit + API |
| FR-002 | implemented_verified | descriptor model and explicit registry entries | complete | unit + API |
| FR-003 | implemented_verified | unregistered `workflow.github_token` omitted and rejected | complete | unit + API |
| FR-004 | implemented_verified | effective response models and service resolution | complete | unit + API |
| FR-005 | implemented_verified | null and SecretRef diagnostics | complete | unit |
| FR-006 | implemented_verified | stable registry keys and option values | complete | unit |
| FR-007 | implemented_verified | PATCH route structured rejection | complete | API |
| FR-008 | implemented_verified | backend service is only descriptor/effective authority | complete | unit + API |
| FR-009 | implemented_verified | `SettingsError` response model and route coverage | complete | API |
| FR-010 | implemented_verified | `spec.md`, this plan, tasks, verification preserve `MM-537` | complete | final verify |
| SC-001 | implemented_verified | descriptor and omission tests | complete | unit + API |
| SC-002 | implemented_verified | environment source test | complete | unit |
| SC-003 | implemented_verified | unresolved SecretRef diagnostic test | complete | unit |
| SC-004 | implemented_verified | unknown/unexposed write error tests | complete | API |
| SC-005 | implemented_verified | source traceability in artifacts | complete | final verify |
| DESIGN-REQ-003 | implemented_verified | explicit backend registry/service | complete | unit + API |
| DESIGN-REQ-005 | implemented_verified | descriptor shape includes required metadata | complete | unit + API |
| DESIGN-REQ-007 | implemented_verified | unexposed setting omitted and rejected | complete | unit + API |
| DESIGN-REQ-008 | implemented_verified | effective value and diagnostics model | complete | unit |
| DESIGN-REQ-022 | implemented_verified | settings routes and structured error model | complete | API |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, existing `moonmind.config.settings` application settings  
**Storage**: None for `MM-537`; scoped override storage is deferred to `MM-538`  
**Unit Testing**: pytest through `./tools/test_unit.sh` or targeted `pytest`  
**Integration Testing**: FastAPI ASGI route tests with `httpx.ASGITransport`; hermetic integration suite remains available through `./tools/test_integration.sh`  
**Target Platform**: MoonMind API service on Linux containers  
**Project Type**: Backend web service  
**Performance Goals**: Catalog and effective reads are in-process registry lookups suitable for settings UI/bootstrap reads  
**Constraints**: Do not expose raw secrets; do not mutate state without scoped override persistence; preserve stable descriptor keys and option values  
**Scale/Scope**: Initial explicit registry with safe workflow, skills, live-session, and SecretRef descriptor examples

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - settings metadata supports existing orchestration surfaces without replacing agents.
- II. One-Click Agent Deployment: PASS - no new external service or required secret.
- III. Avoid Vendor Lock-In: PASS - generic descriptor/effective-value contracts are provider-neutral.
- IV. Own Your Data: PASS - all data is local configuration metadata and non-secret references.
- V. Skills Are First-Class and Easy to Add: PASS - skill-related settings are exposed as settings metadata without mutating skill sources.
- VI. Evolving Scaffold: PASS - implementation is a thin registry/service with tests around contracts.
- VII. Runtime Configurability: PASS - effective values are resolved from application settings/environment.
- VIII. Modular and Extensible Architecture: PASS - settings service and router are isolated behind clear models.
- IX. Resilient by Default: PASS - unsupported writes fail fast with structured errors.
- X. Facilitate Continuous Improvement: PASS - diagnostics expose resolver state.
- XI. Spec-Driven Development: PASS - this spec/plan/tasks/verification set is the source of truth.
- XII. Canonical Documentation Separation: PASS - rollout and implementation notes stay under this feature directory.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases or hidden fallback transforms were added.

## Project Structure

### Documentation (this feature)

```text
specs/267-settings-catalog-effective-values/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-catalog-effective-values.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
└── services/settings_catalog.py

tests/
└── unit/
    ├── api_service/api/routers/test_settings_api.py
    └── services/test_settings_catalog.py
```

**Structure Decision**: Backend-only read-side API surface. No frontend changes are required for `MM-537`.

## Complexity Tracking

No constitution violations.

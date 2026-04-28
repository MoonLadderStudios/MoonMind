# Implementation Plan: Scoped Override Persistence and Inheritance

**Branch**: `268-scoped-override-persistence` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/268-scoped-override-persistence/spec.md`

## Summary

Implement `MM-538` by extending the existing settings catalog/effective-value backend with scoped override persistence, inheritance-aware effective resolution, reset-by-delete behavior, version conflict handling, safe value validation, and settings audit preservation. Existing `MM-537` read-side descriptors and structured error models provide the foundation, but current writes still return `settings_write_unavailable`, so this story requires code, storage, and tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `SettingsCatalogService.effective_value_async`, focused service/API tests | complete | unit + API passed |
| FR-002 | implemented_verified | `SettingsOverride`, `apply_overrides`, PATCH route, workspace override tests | complete | unit + API passed |
| FR-003 | implemented_verified | user-scope override and workspace inheritance tests | complete | unit + API passed |
| FR-004 | implemented_verified | `reset_override`, DELETE route, reset tests | complete | unit + API passed |
| FR-005 | implemented_verified | intentional null override service test | complete | unit passed |
| FR-006 | implemented_verified | `SettingsOverride` unique constraint and migration | complete | unit table creation passed |
| FR-007 | implemented_verified | value version increment and stale expected-version tests | complete | unit + API passed |
| FR-008 | implemented_verified | unsafe value rejection tests | complete | unit + API passed |
| FR-009 | implemented_verified | reset preservation tests for managed secret and audit rows | complete | unit + API passed |
| FR-010 | implemented_verified | SecretRef reference persistence tests without plaintext resolution | complete | unit + API passed |
| FR-011 | implemented_verified | structured write/reset error tests | complete | API passed |
| FR-012 | implemented_verified | `spec.md`, this plan, tasks, and verification preserve `MM-538` | complete | final verify |
| SC-001 | implemented_verified | workspace override service test | complete | unit passed |
| SC-002 | implemented_verified | user override inheritance service/API tests | complete | unit + API passed |
| SC-003 | implemented_verified | reset preservation API/service tests | complete | unit + API passed |
| SC-004 | implemented_verified | stale expected-version atomicity tests | complete | unit + API passed |
| SC-005 | implemented_verified | unsafe value and SecretRef reference tests | complete | unit + API passed |
| SC-006 | implemented_verified | traceability command output and verification report | complete | final verify |
| DESIGN-REQ-006 | implemented_verified | workspace/user inheritance implementation and tests | complete | unit + API passed |
| DESIGN-REQ-017 | implemented_verified | override storage, migration, reset, and audit implementation | complete | unit + API passed |
| DESIGN-REQ-026 | implemented_verified | persisted value safety validation and tests | complete | unit + API passed |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Alembic, pytest, httpx ASGI test transport  
**Storage**: Existing SQLAlchemy/Alembic database; add `settings_overrides` and `settings_audit_events` tables  
**Unit Testing**: `pytest` through `./tools/test_unit.sh` or targeted unit commands  
**Integration Testing**: FastAPI ASGI route tests in unit suite; hermetic integration suite through `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind API service in Linux containers  
**Project Type**: Backend web service  
**Performance Goals**: Settings reads and writes remain bounded to small indexed settings rows  
**Constraints**: Do not store raw secrets or large operational payloads; do not delete provider profiles, managed secrets, OAuth rows, defaults, or audit history on reset; reject partial batch writes  
**Scale/Scope**: Initial persistence for the existing explicit settings registry and user/workspace scopes

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - scoped settings support existing orchestration configuration without replacing agent behavior.
- II. One-Click Agent Deployment: PASS - uses existing local database and migrations.
- III. Avoid Vendor Lock-In: PASS - generic settings contracts remain provider-neutral.
- IV. Own Your Data: PASS - overrides and audits remain in operator-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS - skill settings are persisted as metadata values, not skill source mutations.
- VI. Evolving Scaffold: PASS - persistence is behind service/router contracts with tests.
- VII. Runtime Configurability: PASS - implements runtime user/workspace configurability.
- VIII. Modular and Extensible Architecture: PASS - storage models, service, and router remain isolated.
- IX. Resilient by Default: PASS - optimistic version checks and atomic batches prevent partial or stale writes.
- X. Facilitate Continuous Improvement: PASS - audit records and diagnostics make settings changes inspectable.
- XI. Spec-Driven Development: PASS - this feature is driven by MoonSpec artifacts.
- XII. Canonical Documentation Separation: PASS - implementation details stay in feature artifacts, not canonical docs.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases; unsupported values fail fast.

## Project Structure

### Documentation (this feature)

```text
specs/268-scoped-override-persistence/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-overrides-api.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
├── migrations/versions/
└── services/settings_catalog.py

tests/
└── unit/
    ├── api_service/api/routers/test_settings_api.py
    └── services/test_settings_catalog.py
```

**Structure Decision**: Backend-only settings persistence story. No frontend changes are required for `MM-538`.

## Complexity Tracking

No constitution violations.

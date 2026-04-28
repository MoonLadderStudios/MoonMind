# Implementation Plan: Settings Migration Invariants

**Branch**: `[275-settings-migration-invariants]` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:be976054-2c25-4b91-8814-1aeff6eb3ee0/repo/specs/275-settings-migration-invariants/spec.md`

## Summary

Implement MM-546 by adding an explicit Settings System migration/deprecation rule boundary on top of the existing catalog, override, diagnostics, and audit service. Current code already enforces explicit catalog exposure, scoped writes, value validation, SecretRef redaction, source explainability, reset inheritance, audit redaction, version conflict handling, and settings permissions. This story adds deterministic handling for renamed, removed/deprecated, and type-changed setting keys so maintainers can prove old overrides are preserved or rejected intentionally rather than silently ignored or reinterpreted. The plan is TDD-first with focused service and API tests, then final unit verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `SettingsCatalogService` uses an explicit `_REGISTRY`; API rejects unexposed keys; tests cover `workflow.github_token` rejection | preserve with regression tests | unit + API |
| FR-002 | implemented_verified | `api_service/services/settings_catalog.py` defines `SettingMigrationRule` and resolves renamed old-key overrides; `tests/unit/services/test_settings_catalog.py` and `tests/unit/api_service/api/routers/test_settings_api.py` verify old override preservation | no new implementation | focused unit + API passed |
| FR-003 | implemented_verified | removed/deprecated keys reject writes and diagnostics expose historical rows without raw values in service and API tests | no new implementation | focused unit + API passed |
| FR-004 | implemented_verified | schema-version mismatch produces `setting_type_migration_required` instead of reinterpreting stored JSON | no new implementation | focused unit passed |
| FR-005 | implemented_verified | migration/deprecation diagnostics provide redacted evidence; tests assert secret-like values are absent from diagnostics and API responses | no new implementation | focused unit + API passed |
| FR-006 | implemented_verified | `test_catalog_invariant_gate_for_future_integrations` verifies descriptor exposure, safe UI type, scope, source, audit, and SecretRef invariants | no new implementation | focused unit passed |
| FR-007 | implemented_verified | SecretRef and provider-profile tests prove no plaintext readback and sanitized diagnostics | preserve with final verification | unit |
| FR-008 | implemented_verified | effective value source, user/workspace inheritance, reset, and operator/read-only patterns exist | preserve with final verification | unit |
| FR-009 | implemented_verified | audit endpoint tests cover redaction, version conflicts, and permission checks | preserve with final verification | unit + API |
| FR-010 | implemented_verified | invariant test and `contracts/settings-migration-invariants.md` bind future settings consumers to descriptor-driven, scoped, validated, audited, secret-safe surfaces | no new implementation | focused unit passed |
| FR-011 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, quickstart, contract, and final report preserve `MM-546` and the canonical preset brief | no new implementation | artifact review |
| SC-001 | implemented_verified | service and API tests verify renamed old-key overrides resolve under the new key with migration diagnostics | no new implementation | focused unit + API passed |
| SC-002 | implemented_verified | service and API tests verify removed/deprecated keys reject writes and expose safe diagnostics without raw values | no new implementation | focused unit + API passed |
| SC-003 | implemented_verified | service test verifies schema-version mismatch blocks ambiguous type reinterpretation | no new implementation | focused unit passed |
| SC-004 | implemented_verified | invariant gate test covers unsafe descriptor exposure, scope, validation, source, audit, and SecretRef regressions | no new implementation | focused unit passed |
| SC-005 | implemented_verified | contract and invariant tests preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior | no new implementation | focused unit passed |
| SC-006 | implemented_verified | `rg` traceability checks and artifacts preserve `MM-546` plus DESIGN-REQ-020/021/024/027/028 | no new implementation | artifact review |
| DESIGN-REQ-020 | implemented_verified | explicit registry and API rejection tests enforce non-goals | preserve with invariant tests | unit + API |
| DESIGN-REQ-021 | implemented_verified | explicit migration rule boundary, deprecated diagnostics, write rejection, and schema-version gate implemented and tested | no new implementation | focused unit + API passed |
| DESIGN-REQ-024 | implemented_verified | focused invariant and migration tests cover the MM-546 regression gate; full unit suite passed | no new implementation | full unit passed |
| DESIGN-REQ-027 | implemented_verified | contract and invariant test preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret safety | no new implementation | focused unit passed |
| DESIGN-REQ-028 | implemented_verified | migration/type-change proof added while existing SecretRef, provider profile, source explainability, reset, audit, and permission tests remain passing | no new implementation | full unit passed |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, pytest  
**Storage**: Existing `settings_overrides` and `settings_audit_events` tables only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` for final verification; targeted `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` during iteration  
**Integration Testing**: Existing settings API route tests are API-boundary unit tests; full hermetic integration remains `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind API service Settings System runtime  
**Project Type**: Web service backend  
**Performance Goals**: Preserve current batched override reads and avoid per-key database queries for catalog/effective-value lists  
**Constraints**: No raw secrets in diagnostics, audit, or artifacts; no compatibility aliases for internal contracts; source docs remain desired state and implementation notes stay under `specs/`  
**Scale/Scope**: One Settings System maintenance-safety story covering migration/deprecation/type-change invariants and regression evidence

## Constitution Check

| Principle | Gate | Status | Evidence |
| --- | --- | --- | --- |
| I. Orchestrate, Don't Recreate | No agent-specific behavior | PASS | Settings service boundary only |
| II. One-Click Agent Deployment | No new service dependency | PASS | Existing DB/test setup only |
| III. Avoid Vendor Lock-In | No provider-specific lock-in | PASS | Generic settings rules |
| IV. Own Your Data | Overrides/audit remain local | PASS | Existing local tables |
| V. Skills Are First-Class | No skill runtime mutation | PASS | Out of scope |
| VI. Replaceable Scaffolding | Behavior backed by tests | PASS | TDD tasks |
| VII. Runtime Configurability | Improves settings evolution safety | PASS | Core story |
| VIII. Modular Architecture | Changes stay in settings service/router tests | PASS | No cross-cutting rewrite |
| IX. Resilient by Default | Unsafe migrations fail explicitly | PASS | Core story |
| X. Continuous Improvement | Final report includes structured outcome | PASS | Orchestration final gate |
| XI. Spec-Driven Development | Spec/plan/tasks drive implementation | PASS | `specs/275-settings-migration-invariants` |
| XII. Canonical Docs Separate Desired State | Canonical docs are read-only source requirements | PASS | No docs rewrite |
| XIII. Pre-Release Compatibility | Internal contracts fail fast instead of adding hidden aliases | PASS | Migration rules are explicit, not automatic compatibility shims |

## Project Structure

### Documentation (this feature)

```text
specs/275-settings-migration-invariants/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-migration-invariants.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
└── services/settings_catalog.py

tests/
└── unit/
    ├── services/test_settings_catalog.py
    └── api_service/api/routers/test_settings_api.py
```

**Structure Decision**: Extend the existing settings catalog service and settings API route tests. No frontend change is planned because MM-546 is a backend migration/test gate story and existing diagnostics API output is the operator-visible surface.

## Complexity Tracking

No constitution violations requiring complexity exceptions.

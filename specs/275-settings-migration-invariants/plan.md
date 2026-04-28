# Implementation Plan: Settings Migration Invariants

**Branch**: `[275-settings-migration-invariants]` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:be976054-2c25-4b91-8814-1aeff6eb3ee0/repo/specs/275-settings-migration-invariants/spec.md`

## Summary

Implement MM-546 by adding an explicit Settings System migration/deprecation rule boundary on top of the existing catalog, override, diagnostics, and audit service. Current code already enforces explicit catalog exposure, scoped writes, value validation, SecretRef redaction, source explainability, reset inheritance, audit redaction, version conflict handling, and settings permissions. The missing runtime gap is deterministic handling for renamed, removed/deprecated, and type-changed setting keys so maintainers can prove old overrides are preserved or rejected intentionally rather than silently ignored or reinterpreted. The plan is TDD-first with focused service and API tests, then final unit verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `SettingsCatalogService` uses an explicit `_REGISTRY`; API rejects unexposed keys; tests cover `workflow.github_token` rejection | preserve with regression tests | unit + API |
| FR-002 | missing | no rename migration rule or old-key override resolution path found | add migration rule model and old-to-new override resolution | unit + API |
| FR-003 | missing | unknown/removed keys are rejected but no deprecated-value diagnostics for existing rows | add removed/deprecated key write rejection plus diagnostics visibility | unit + API |
| FR-004 | missing | `SettingsOverride.schema_version` exists but resolver does not enforce expected schema version | require explicit type migration when schema versions differ | unit |
| FR-005 | partial | audit and diagnostics exist; migration/deprecation events do not | add redacted diagnostics and audit evidence for migrations | unit + API |
| FR-006 | partial | existing tests cover catalog, scope, validation, constraints, version conflict, and drift partially | add catalog invariant snapshot-style test focused on exposed descriptors | unit |
| FR-007 | implemented_verified | SecretRef and provider-profile tests prove no plaintext readback and sanitized diagnostics | preserve with final verification | unit |
| FR-008 | implemented_verified | effective value source, user/workspace inheritance, reset, and operator/read-only patterns exist | preserve with final verification | unit |
| FR-009 | implemented_verified | audit endpoint tests cover redaction, version conflicts, and permission checks | preserve with final verification | unit + API |
| FR-010 | partial | future integrations receive descriptor/effective/diagnostic APIs but no explicit invariant test binds them to safe fields | add contract test for descriptor-driven, scoped, validated, audited, secret-safe surfaces | unit |
| FR-011 | missing | new spec preserves `MM-546`; downstream artifacts and verification not complete | preserve Jira key and brief through artifacts and final report | artifact review |
| SC-001 | missing | no rename migration test exists | add service/API tests | unit + API |
| SC-002 | missing | no removed-key diagnostic test exists | add service/API tests | unit + API |
| SC-003 | missing | no schema/type mismatch failure test exists | add service test | unit |
| SC-004 | partial | broad coverage exists; no single invariant drift test for MM-546 | add invariant coverage | unit |
| SC-005 | partial | APIs expose safe settings surfaces; no MM-546 future integration contract test | add contract/invariant test | unit |
| SC-006 | missing | artifacts in progress | preserve traceability | artifact review |
| DESIGN-REQ-020 | implemented_verified | explicit registry and API rejection tests enforce non-goals | preserve with invariant tests | unit + API |
| DESIGN-REQ-021 | missing | migration/deprecation/type-change rules absent | implement rule boundary and tests | unit + API |
| DESIGN-REQ-024 | partial | many tests exist; drift and migration gate gaps remain | add focused regression tests | unit |
| DESIGN-REQ-027 | partial | descriptor/effective/diagnostic surfaces exist | add future integration invariant test | unit |
| DESIGN-REQ-028 | partial | most invariants exist; migration/type-change proof missing | add migration invariant tests | unit + API |

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

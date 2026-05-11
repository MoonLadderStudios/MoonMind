# Implementation Plan: Sparse Settings Override Persistence and Reset

**Branch**: `339-sparse-settings-overrides` | **Date**: 2026-05-11 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/339-sparse-settings-overrides/spec.md`

## Summary

Implement `MM-654` by completing and verifying sparse user/workspace settings override persistence, inheritance-aware effective reads, reset-by-delete behavior, safe value validation, and optimistic concurrency. Repo analysis found the core persistence, reset, audit, and version-conflict behavior already implemented and covered by focused service/API unit tests from the prior scoped-override work. Remaining delivery risk is concentrated in the `MM-654`-specific validation breadth: explicit size-limit enforcement and fixture coverage for every disallowed payload class named by the Jira brief.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `SettingsCatalogService._resolve_value_async`; `tests/unit/services/test_settings_catalog.py::test_workspace_override_persists_and_reports_version` | no new implementation | final verify |
| FR-002 | implemented_verified | `SettingsOverride` model; `SettingsCatalogService.apply_overrides`; workspace API test | no new implementation | final verify |
| FR-003 | implemented_verified | user override storage in `apply_overrides`; `test_user_override_wins_and_null_override_is_intentional`; API user override test | no new implementation | final verify |
| FR-004 | implemented_verified | reset/delete path removes override rows only; tests verify inherited/default values remain | no new implementation | final verify |
| FR-005 | implemented_verified | `reset_override`; service/API reset tests | no new implementation | final verify |
| FR-006 | implemented_verified | effective value source handling for inherited, workspace, user, migrated, and intentional null overrides | no new implementation | final verify |
| FR-007 | implemented_verified | `value_version` model field and increment behavior; workspace version tests | no new implementation | final verify |
| FR-008 | partial | `_validate_override_value` validates type/schema for registered scalar settings, but no explicit serialized size limit evidence was found | add size-limit policy and tests if absent | unit + API |
| FR-009 | implemented_verified | reset tests preserve managed secret and audit rows | no new implementation | final verify |
| FR-010 | implemented_verified | expected-version checks and atomic conflict tests in service/API suites | no new implementation | final verify |
| FR-011 | partial | raw secret and workflow-payload rejection exists; explicit fixture coverage for OAuth session blobs, decrypted credentials, generated credential config, large artifacts, and command history is incomplete | add exhaustive unsafe-payload fixtures and implementation hardening if tests expose gaps | unit + API |
| FR-012 | partial | many existing tests cover the story, but `MM-654` requires focused final evidence for all acceptance bullets | add or run focused verification tests, then final verify | unit + integration as applicable |
| FR-013 | implemented_unverified | `spec.md` preserves `MM-654`; later artifacts must continue preserving it | preserve key and brief in plan/tasks/verification/PR metadata | final verify |
| SCN-001 | implemented_verified | inherited effective values return configured/default source without creating override rows | no new implementation | final verify |
| SCN-002 | implemented_verified | workspace override persistence and source/version tests | no new implementation | final verify |
| SCN-003 | implemented_verified | user override wins over workspace inheritance in service/API tests | no new implementation | final verify |
| SCN-004 | implemented_verified | reset returns inherited value and preserves adjacent resources | no new implementation | final verify |
| SCN-005 | partial | unsafe raw secret and workflow-payload rejection exists; size and full disallowed-class fixture set need proof | add validation tests and hardening | unit + API |
| SCN-006 | implemented_verified | stale expected version fails atomically in service/API tests | no new implementation | final verify |
| SCN-007 | implemented_unverified | `spec.md` and this plan preserve `MM-654`; downstream artifacts not generated yet | preserve traceability through tasks, implementation notes, verification, and PR metadata | final verify |
| SC-001 | implemented_verified | workspace override round-trip and metadata tests | no new implementation | final verify |
| SC-002 | implemented_verified | user override inheritance/reset behavior covered by service/API tests except final `MM-654` traceability run | no new implementation | final verify |
| SC-003 | implemented_verified | reset preservation tests for managed secrets and audit rows | no new implementation | final verify |
| SC-004 | partial | current fixtures do not prove every named unsafe category and explicit size boundary | add exhaustive validation fixtures | unit + API |
| SC-005 | implemented_verified | version-conflict atomicity tests | no new implementation | final verify |
| SC-006 | implemented_unverified | current artifacts preserve `MM-654`; final verification not yet produced | preserve and verify traceability | final verify |
| DESIGN-REQ-001 | implemented_verified | defaults inherited and reset-by-delete behavior in service/API tests | no new implementation | final verify |
| DESIGN-REQ-002 | implemented_verified | persisted scopes limited to user/workspace by model/service validation | no new implementation | final verify |
| DESIGN-REQ-003 | implemented_verified | sparse override absence/inheritance behavior in effective reads | no new implementation | final verify |
| DESIGN-REQ-004 | implemented_verified | effective source and source explanation fields | no new implementation | final verify |
| DESIGN-REQ-005 | implemented_verified | model uniqueness, version, and audit fields plus migration | no new implementation | final verify |
| DESIGN-REQ-006 | partial | allowed scalar/SecretRef validation exists; no explicit size limit and incomplete disallowed-class fixtures | add tests and implementation hardening for all payload classes | unit + API |
| DESIGN-REQ-007 | implemented_verified | reset deletes only relevant override and preserves defaults/secrets/audit | no new implementation | final verify |
| DESIGN-REQ-008 | implemented_verified | reset validation covered by tests | no new implementation | final verify |
| DESIGN-REQ-009 | implemented_verified | `SettingsCatalogService` persists and retrieves scoped overrides | no new implementation | final verify |
| DESIGN-REQ-010 | implemented_verified | user reset inheritance flow covered by effective-read and reset tests | no new implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Alembic, pytest, httpx ASGI test transport  
**Storage**: Existing SQLAlchemy/Alembic database tables `settings_overrides` and `settings_audit_events`; no new persistent table expected  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; no external credentials required  
**Target Platform**: MoonMind API service in Linux containers  
**Project Type**: Backend web service  
**Performance Goals**: Settings override reads/writes remain bounded to small indexed rows and small serialized payloads  
**Constraints**: Do not store raw secrets, OAuth blobs, decrypted credentials, generated secret-bearing config, large artifacts, workflow payloads, or operational command history; reset must not delete adjacent resources; stale writes must fail atomically  
**Scale/Scope**: Single user/workspace settings override story for existing catalog entries and API surfaces

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - this story supports runtime configuration without replacing agent behavior.
- II. One-Click Agent Deployment: PASS - uses existing database and local test tooling.
- III. Avoid Vendor Lock-In: PASS - settings override behavior is provider-neutral and uses existing SecretRef/resource-reference concepts.
- IV. Own Your Data: PASS - settings data and audit evidence remain operator-controlled.
- V. Skills Are First-Class and Easy to Add: PASS - no skill source mutation is planned.
- VI. Evolving Scaffold: PASS - changes stay behind service/router contracts and tests.
- VII. Runtime Configurability: PASS - this story implements runtime user/workspace configuration behavior.
- VIII. Modular and Extensible Architecture: PASS - storage, service, router, and tests remain in existing module boundaries.
- IX. Resilient by Default: PASS - optimistic concurrency and atomic batches prevent stale partial writes.
- X. Facilitate Continuous Improvement: PASS - audit and verification evidence make outcomes inspectable.
- XI. Spec-Driven Development: PASS - work is driven by `specs/339-sparse-settings-overrides/spec.md`.
- XII. Canonical Documentation Separation: PASS - planning details remain under `specs/`.
- XIII. Pre-Release Velocity: PASS - no compatibility aliases or fallback semantics are planned.

Re-check after Phase 1 design: PASS. Generated data model, contract, and quickstart preserve the same boundaries and add no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/339-sparse-settings-overrides/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-overrides-api.md
└── tasks.md              # created later by /speckit.tasks
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
├── migrations/versions/268_settings_overrides.py
└── services/settings_catalog.py

tests/
└── unit/
    ├── api_service/api/routers/test_settings_api.py
    └── services/test_settings_catalog.py
```

**Structure Decision**: Backend-only settings persistence story using existing service, route, model, migration, and unit/API test surfaces. No frontend work is expected for `MM-654`.

## Complexity Tracking

No constitution violations.

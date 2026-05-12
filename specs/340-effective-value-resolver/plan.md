# Implementation Plan: Effective Value Resolver With Source Explanation and Operator Locks

**Branch**: `340-effective-value-resolver` | **Date**: 2026-05-12 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/340-effective-value-resolver/spec.md`

## Summary

Implement `MM-655` by completing the Settings effective-value resolver contract for source explanation, deterministic precedence, operator locks, and explicit diagnostic states. Repo analysis found core catalog, effective read, scoped override, SecretRef, provider-profile, migration, activation, and diagnostics surfaces already present in `api_service/services/settings_catalog.py` with focused unit/API tests. Remaining delivery risk is concentrated in the canonical source vocabulary, missing operator-lock precedence behavior, missing or incomplete effective-read metadata, and full proof for the distinct missing/null/blocked/invalid diagnostic states named by the Jira brief.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `EffectiveSettingValue` returns value and `source`, but effective reads do not yet expose the full canonical metadata required by `MM-655` | extend/normalize effective value output and tests | unit + API |
| FR-002 | implemented_verified | `_resolve_value` returns catalog default when no stronger source exists; `test_catalog_endpoint_returns_runtime_settings_metadata` covers default/config source | preserve behavior | final verify |
| FR-003 | partial | environment overrides are explicit, but config/default are collapsed into `config_or_default` instead of canonical `config_file` vs `default` source labels | normalize config/default source handling or define deterministic mapping | unit + API |
| FR-004 | implemented_verified | `_resolve_value_from_overrides`; workspace and user override tests prove workspace shadows defaults and user shadows workspace | no new implementation expected | final verify |
| FR-005 | missing | no operator-lock source or lock-precedence resolution path found; read-only metadata exists but is not tied to winning operator-lock values | add operator-lock resolution/source/read-only behavior | unit + API |
| FR-006 | partial | source labels include `environment`, `default`, `workspace_override`, `user_override`, migrated/deprecated labels, and `config_or_default`; missing canonical `config_file`, `provider_profile`, `secret_ref`, `operator_lock` outcomes | align source vocabulary and tests | unit + API |
| FR-007 | partial | `inherited_null`, `unresolved_secret_ref`, provider-profile missing/disabled, and type-migration diagnostics exist; no complete evidence for no default, intentionally null, policy-blocked, and post-migration invalid as distinct states | add diagnostic model/tests and implementation gaps | unit + API |
| FR-008 | partial | source labels distinguish inherited and overrides; intentional null is tested; locked state is missing | add explicit locked/explanation output | unit + API |
| FR-009 | partial | activation metadata and affected systems exist; `EffectiveSettingValue` lacks default value and read-only metadata while descriptors include them | extend effective value/diagnostics contract or route planned consumers through descriptor output | unit + API |
| FR-010 | missing | `read_only` and `read_only_reason` fields exist on descriptors, but no operator-lock descriptor case or test found | add operator-lock descriptor output with populated reason | unit + API |
| FR-011 | implemented_unverified | SecretRef diagnostics redact plaintext; provider-profile diagnostics exist; current source labels do not identify these reference sources canonically | add focused verification and source-label hardening if tests fail | unit + API |
| FR-012 | missing | no `MM-655` verification evidence exists yet | add focused source-precedence, lock, metadata, and diagnostic tests | unit + API + final verify |
| FR-013 | partial | current diagnostics avoid silent fallback for several cases, but not all `MM-655` diagnostic states are proven | add missing diagnostic states and fail-visible behavior | unit + API |
| FR-014 | implemented_unverified | `spec.md` and this plan preserve `MM-655`; downstream artifacts not generated yet | preserve key and brief through tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | defaults/configured values return source and activation metadata; default value is not present in effective read output | add effective metadata coverage | unit + API |
| SCN-002 | implemented_verified | workspace override tests prove workspace wins over defaults | no new implementation expected | final verify |
| SCN-003 | implemented_verified | user override tests prove user wins over workspace/default | no new implementation expected | final verify |
| SCN-004 | missing | operator lock precedence and read-only reason are not implemented as a winning source | add lock path and tests | unit + API |
| SCN-005 | partial | SecretRef and provider-profile diagnostics exist without plaintext; source labels need canonical reference-source behavior | add verification and source-label work | unit + API |
| SCN-006 | partial | some diagnostic states exist; full no-default/null/blocked/invalid matrix is incomplete | add diagnostic matrix tests and gaps | unit + API |
| SCN-007 | implemented_unverified | current artifacts preserve `MM-655`; final verification not generated | preserve traceability | final verify |
| SC-001 | partial | tests cover default/environment/workspace/user/SecretRef/provider-profile; no operator-lock source and incomplete vocabulary | add missing source cases | unit + API |
| SC-002 | missing | no operator-lock read-only test found | add lock read-only tests | unit + API |
| SC-003 | partial | diagnostics exist for inherited null, SecretRef, provider profile, migration; missing full named matrix | add distinct diagnostic tests | unit + API |
| SC-004 | partial | diagnostics route includes activation and affected systems; effective route lacks default/read-only metadata | extend output/tests | unit + API |
| SC-005 | implemented_unverified | existing tests verify no SecretRef plaintext leaks and provider profile internals remain separate; source-label work may affect this | rerun focused tests and add coverage for new labels | unit + API |
| SC-006 | implemented_unverified | current artifacts preserve `MM-655`; final verification not generated | preserve and verify traceability | final verify |
| DESIGN-REQ-001 | partial | descriptor/diagnostics output contains most explainability fields; effective read output is incomplete | extend effective contract or document/use descriptor path | unit + API |
| DESIGN-REQ-002 | partial | source labels are not fully aligned with the documented vocabulary | normalize vocabulary | unit + API |
| DESIGN-REQ-003 | missing | operator lock as enforced winning source is absent | add operator-lock behavior | unit + API |
| DESIGN-REQ-004 | partial | precedence exists for config/env/workspace/user, but config/default source distinction is collapsed | refine source handling | unit + API |
| DESIGN-REQ-005 | missing | operator lock chain not implemented | add lock chain | unit + API |
| DESIGN-REQ-006 | implemented_unverified | provider profile references are separate resources and diagnostics exist | verification tests first, harden if needed | unit + API |
| DESIGN-REQ-007 | implemented_unverified | SecretRef references and redacted diagnostics exist | verification tests first, harden if needed | unit + API |
| DESIGN-REQ-008 | partial | several diagnostic states exist, but full matrix is incomplete | add diagnostic matrix | unit + API |
| DESIGN-REQ-009 | partial | inheritance exists and is visible through source; fuller metadata needed | extend tests/output | unit + API |
| DESIGN-REQ-010 | implemented_unverified | workspace override refresh/source behavior exists; future task creation impact is not directly verified for this story | add verification or final evidence | unit + API as applicable |
| DESIGN-REQ-011 | partial | resolver capability exists but is incomplete for `MM-655` source/lock/diagnostic requirements | complete resolver contract | unit + API |
| DESIGN-REQ-012 | partial | source explanation exists; operator locks cannot yet be proven | add operator-lock behavior/tests | unit + API |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, pytest, httpx ASGI test transport, existing Settings catalog/service models  
**Storage**: Existing `settings_overrides`, settings audit, managed secret, and provider profile rows only; no new persistent table expected  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; no external credentials required  
**Target Platform**: MoonMind API service in Linux containers  
**Project Type**: Backend web service with API-visible settings contracts  
**Performance Goals**: Effective settings reads remain bounded to catalog entries and small scoped lookup rows; no external provider calls during effective-value resolution  
**Constraints**: Preserve secret hygiene; do not resolve SecretRef plaintext in settings output; avoid compatibility aliases for internal contracts; keep provider-profile semantics separate from generic settings; fail explicitly instead of silent fallback  
**Scale/Scope**: Single Settings effective-value story covering existing user/workspace settings catalog entries, effective read output, diagnostics output, and focused route/service tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - the story improves MoonMind settings behavior without replacing external agents.
- II. One-Click Agent Deployment: PASS - uses existing local service and database surfaces without adding mandatory external dependencies.
- III. Avoid Vendor Lock-In: PASS - provider profile and SecretRef references remain provider-neutral abstractions.
- IV. Own Your Data: PASS - settings explanations and diagnostics stay in MoonMind-controlled API responses and tests.
- V. Skills Are First-Class and Easy to Add: PASS - no skill source mutation or runtime skill contract changes are planned.
- VI. Evolving Scaffold: PASS - behavior remains behind explicit service/router contracts and focused tests.
- VII. Runtime Configurability: PASS - this story directly supports observable runtime configuration.
- VIII. Modular and Extensible Architecture: PASS - work stays in Settings registry/service/router/test boundaries.
- IX. Resilient by Default: PASS - fail-visible diagnostics replace hidden fallback behavior.
- X. Facilitate Continuous Improvement: PASS - explicit diagnostics and verification evidence improve operator troubleshooting.
- XI. Spec-Driven Development: PASS - work is driven by `specs/340-effective-value-resolver/spec.md`.
- XII. Canonical Documentation Separation: PASS - planning and rollout details remain under `specs/340-effective-value-resolver/`.
- XIII. Pre-Release Velocity: PASS - planned source vocabulary changes should remove or replace superseded internal labels rather than adding compatibility aliases.

Re-check after Phase 1 design: PASS. The generated research, data model, API contract, and quickstart preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/340-effective-value-resolver/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-effective-values-api.md
└── tasks.md              # created later by /speckit.tasks
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

**Structure Decision**: Backend Settings contract story using existing service, model, route, and unit/API test surfaces. Frontend work is not expected unless downstream implementation discovers that Mission Control requires a rendering change for newly exposed metadata.

## Complexity Tracking

No constitution violations.

# Implementation Plan: Server-Side Validation and Cross-Setting Policy Enforcement

**Branch**: `341-server-side-validation` | **Date**: 2026-05-12 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:6a56ae2e-2dd6-49a9-8d85-885149e190b2/repo/specs/341-server-side-validation/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed branch name `change-jira-issue-mm-656-to-status-in-pr-c8e6469d` does not match the helper's numeric feature-branch guard. Planning continued from `.specify/feature.json`, which points to this feature directory.

## Summary

MM-656 requires the Settings System to reject unsafe setting changes through a server-side validator that is authoritative across writes, previews, descriptor generation, launch and operation readiness, and diagnostics. The repository already has a backend-owned settings registry, setting descriptors, sparse override persistence, effective-value diagnostics, authorization checks, and partial value validation in `api_service/services/settings_catalog.py`, plus API and integration tests. The implementation plan is to evolve that existing settings catalog boundary into an explicit validation contract with typed validation results, value validators for all descriptor value categories, cross-setting policy checks, and shared validation entrypoints for write, preview, descriptor, launch/operation, and diagnostics paths. Unit tests will drive validator rule coverage; integration tests will prove API behavior and boundary consistency.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `SettingsCatalogService.ensure_write_allowed()` and `apply_overrides()` reject unknown keys, invalid scopes, and read-only entries; router checks write permissions in `api_service/api/routers/settings.py`. | Add typed validation result details that carry key, scope, code, and boundary; preserve existing authorization behavior. | unit + integration |
| FR-002 | partial | `_validate_override_value()` covers enum, integer, boolean, secret_ref, and string, but not generic list/object descriptor value types. | Add validation support and descriptors for list/object categories or explicit fail-fast unsupported-type coverage as required by spec. | unit |
| FR-003 | partial | Numeric min/max, enum options, size limit, and unsafe payload guards exist; string patterns/lengths, list constraints, and object schema restrictions are incomplete. | Add full constraint evaluation for string, list, object, and schema-like descriptor constraints. | unit + integration |
| FR-004 | partial | SecretRef syntax and DB/env SecretRef diagnostics exist; provider profile existence/disabled diagnostics exist for effective values. | Promote referenced-resource checks into shared validation so write/preview/readiness can block when required, while keeping diagnostics redacted. | unit + integration |
| FR-005 | missing | No first-class workspace policy model was found for allowed runtimes/providers/canary/publication/SecretRef backend/maintenance constraints. | Introduce a compact policy input/service at the settings validation boundary using current configuration/catalog data where available. | unit + integration |
| FR-006 | missing | No cross-setting validator currently rejects combinations such as disabled profile selectors, nonzero disabled-feature canary, runtime outside allowed list, disallowed SecretRef backend, or maintenance conflicts. | Add cross-setting validation rules and structured errors for each documented combination. | unit + integration |
| FR-007 | partial | Read-only/operator lock checks and invalid scope checks exist; structured error details are route-specific and not normalized through a validator result. | Normalize locked/unsupported/ineligible validation errors through the shared validation contract. | unit + integration |
| FR-008 | missing | Validation exists mainly on write and diagnostics/effective read paths; no shared rule set covers descriptor generation, pre-persistence, preview, launch/operation execution, and readiness diagnostics. | Add named validation boundaries and route existing/new consumers through the shared validator or adapter helpers. | unit + integration |
| FR-009 | partial | `SettingsError` and `SettingDiagnostic` exist, but write errors often collapse to `invalid_setting_value` without the failed key/rule and boundary. | Define and return structured validation errors with key, scope, code/rule, message, boundary, and blocking target. | unit + integration |
| FR-010 | partial | Unsafe payloads, raw token prefixes, missing SecretRef diagnostics, provider profile diagnostics, and read-only checks exist; fallback prevention is not consistently tested across boundaries. | Add fail-fast tests and ensure no invalid sensitive source fallback occurs in writes, previews, launch/operation readiness, or diagnostics. | unit + integration |
| FR-011 | missing | Existing tests cover portions of settings catalog, overrides, diagnostics, and MM-655 resolver behavior, but not MM-656's full value-type, cross-setting, timing-boundary, and fail-fast matrix. | Add MM-656 unit and integration coverage before implementation changes. | unit + integration |
| FR-012 | implemented_unverified | `spec.md` preserves MM-656 and the original preset brief; plan preserves the issue key. | Add traceability guard in downstream tasks/verification if missing. | unit or final verify |
| SCN-001 | partial | Existing write and effective-value tests cover some enum/integer/boolean/string/SecretRef paths. | Expand to every supported value category and both accepted/rejected outcomes. | unit + integration |
| SCN-002 | partial | Current invalid value tests reject oversized, unsafe, stale version, and canary range cases. | Add rejected cases for all mismatch types and structured error codes. | unit + integration |
| SCN-003 | partial | SecretRef and provider-profile diagnostics exist, but writes do not uniformly block missing referenced resources. | Add validation behavior for referenced resources at write/preview/readiness boundaries. | unit + integration |
| SCN-004 | missing | Workspace policy constraints are not modeled as a shared validation input. | Add policy rule evaluation and API-visible errors. | unit + integration |
| SCN-005 | missing | Timing boundaries do not share one validator contract. | Add boundary-specific validation calls and tests. | unit + integration |
| SCN-006 | partial | Unknown, invalid scope, and read-only errors exist. | Normalize error shape and ensure no silent mutation/fallback. | unit + integration |
| SC-001 | partial | Existing tests cover enum/integer/boolean/string/SecretRef portions. | Add list/object and accepted/rejected matrix coverage. | unit |
| SC-002 | partial | Numeric write rejection exists for canary; preview-time constraint rejection is not complete. | Add write and effective preview tests for numeric and string constraints. | unit + integration |
| SC-003 | missing | No complete section 18.2 cross-setting rule test matrix found. | Add one regression test per cross-setting rule. | unit + integration |
| SC-004 | missing | No test matrix covers every section 18.3 validation timing boundary. | Add boundary coverage for descriptor generation, write, pre-persistence, preview, launch/operation, and diagnostics. | unit + integration |
| SC-005 | implemented_unverified | `spec.md` preserves MM-656, original brief, and DESIGN-REQ-001 through DESIGN-REQ-011. | Preserve traceability through plan, tasks, quickstart, and final verification. | final verify |
| DESIGN-REQ-001 | partial | Existing diagnostics and write checks are explicit for some errors; not all fail-fast cases are normalized. | Shared validation result and fail-fast coverage. | unit + integration |
| DESIGN-REQ-002 | missing | Workspace policy constraints are described in docs but not implemented as validation policy. | Add compact policy contract. | unit + integration |
| DESIGN-REQ-003 | partial | Some backend write validation exists. | Complete descriptor constraints, references, dependencies, and policy validation. | unit + integration |
| DESIGN-REQ-004 | missing | Cross-setting rules are not implemented as a rule set. | Add cross-setting validator rules. | unit + integration |
| DESIGN-REQ-005 | missing | Validation timing boundaries are not consistently wired. | Add boundary contract and tests. | unit + integration |
| DESIGN-REQ-006 | partial | Backend-owned registry and API permission checks exist. | Ensure client metadata cannot affect validation and errors are validator-owned. | unit + integration |
| DESIGN-REQ-007 | partial | Size limit exists; schema/constraint validation is incomplete. | Add constraint/schema coverage. | unit |
| DESIGN-REQ-008 | partial | Unsafe payload scanning rejects obvious executable/sensitive tokens in object/string values. | Add object-setting schema guard tests and explicit unsupported executable payload errors. | unit |
| DESIGN-REQ-009 | partial | Existing tests cover unknown keys, invalid scopes, some types, and numeric constraints. | Expand to all MM-656 categories and string constraints. | unit + integration |
| DESIGN-REQ-010 | partial | `SettingsCatalogService` is the current settings validation boundary but does not expose a distinct validator contract. | Extract or formalize SettingsValidator responsibility at service boundary. | unit |
| DESIGN-REQ-011 | partial | Writes generally pass through service validation, but not every boundary is covered. | Shared validator boundary and no-bypass tests. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React remains present but is not expected unless diagnostics UI needs follow-up in later stages  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, existing settings catalog service, existing provider profile and managed secret models  
**Storage**: Existing `settings_overrides`, `settings_audit_events`, `managed_secrets`, and `managed_agent_provider_profiles`; no new persistent table planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused iteration with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm656_traceability.py -q`  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration; focused iteration with `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci'`  
**Target Platform**: Linux server containers running the MoonMind API and workers  
**Project Type**: FastAPI web service with shared backend service contracts and Mission Control consumers  
**Performance Goals**: Settings validation remains bounded to the submitted settings and related referenced resources; ordinary single-setting writes and previews should complete within normal API request latency.  
**Constraints**: Secret values must never be exposed in artifacts or API error payloads; validation must be server-authoritative; unsupported values must fail fast; internal pre-release contracts should be updated cleanly rather than compatibility-shimmed.  
**Scale/Scope**: Single Settings System story for MM-656 covering the currently exposed settings registry and validation boundaries; broad settings UI redesign and unrelated provider-profile CRUD are out of scope.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Result |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | Keep settings validation inside MoonMind control-plane boundaries; do not introduce agent cognitive behavior. | PASS |
| II. One-Click Agent Deployment | Use local FastAPI/SQLAlchemy settings services and existing test tooling; no mandatory external SaaS dependency. | PASS |
| III. Avoid Vendor Lock-In | Validation contract remains provider-neutral; provider profile and SecretRef checks use existing abstract references. | PASS |
| IV. Own Your Data | Settings, diagnostics, and audit evidence remain operator-controlled in existing stores. | PASS |
| V. Skills Are First-Class and Easy to Add | No changes to skill runtime behavior; planning preserves current MoonSpec skill workflow. | PASS |
| VI. Scientific Method | Plan requires red-first unit/integration validation before production changes. | PASS |
| VII. Runtime Configurability | Story strengthens runtime settings validation and explicit errors. | PASS |
| VIII. Modular and Extensible Architecture | Plan formalizes validation at service/API boundaries rather than duplicating ad hoc checks. | PASS |
| IX. Resilient by Default | Fail-fast structured validation prevents unsafe configuration from reaching launches/operations. | PASS |
| X. Continuous Improvement | Plan preserves traceability and deterministic verification evidence. | PASS |
| XI. Spec-Driven Development | Spec, plan, and downstream tasks remain the source of truth for MM-656. | PASS |
| XII. Canonical Documentation Separation | Implementation planning lives in MoonSpec artifacts, not canonical docs. | PASS |
| XIII. Pre-Release Velocity | No compatibility shims planned for internal validation contracts; superseded ad hoc paths should be removed or updated together. | PASS |

Post-Phase 1 re-check: PASS. The generated research, data model, contract, and quickstart keep the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/341-server-side-validation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-validation-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/settings.py
├── db/models.py
└── services/settings_catalog.py

moonmind/
├── auth/secret_refs.py
└── config/settings.py

tests/
├── unit/services/test_settings_catalog.py
├── unit/api_service/api/routers/test_settings_api.py
└── integration/api/
    ├── test_settings_overrides_contract.py
    └── test_settings_effective_values_contract.py
```

**Structure Decision**: Implement MM-656 in the existing backend settings service and API router boundaries. Keep data persistence in existing settings/provider/secret tables. Add or update tests in the current unit and hermetic integration locations; create no new frontend surface unless implementation later reveals an unavoidable diagnostics rendering gap.

## Complexity Tracking

No constitution violations or extra architectural complexity are required for this planning stage.

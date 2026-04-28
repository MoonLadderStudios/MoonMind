# Implementation Plan: Secret-Safe Settings and Managed Secrets Workflows

**Branch**: `270-secret-safe-settings-managed-secrets`
**Date**: 2026-04-28
**Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime spec from MM-540 Jira preset brief.

## Summary

Close the remaining secret-safety gaps in the existing Settings and Managed Secrets surfaces. The backend already stores managed secrets encrypted, suppresses ciphertext in metadata responses, and rejects raw secret-like generic setting overrides. The missing runtime behavior is narrower: expose canonical `db://<slug>` references in secret metadata and UI copy actions, return redacted validation diagnostics rather than a bare boolean, and diagnose `db://` SecretRefs whose backing managed secret is missing or inactive. Validation will use focused API/service tests plus a frontend component test for copyable SecretRefs.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/components/secrets/SecretManager.tsx` keeps plaintext write-only and `frontend/src/components/secrets/SecretManager.test.tsx` asserts plaintext is not rendered. | Preserve with final verification | frontend unit |
| FR-002 | implemented_verified | `SecretMetadataResponse.secretRef` derives `db://<slug>` from metadata and API tests assert plaintext/ciphertext are absent. | Preserve with final verification | API unit |
| FR-003 | implemented_verified | Managed Secrets UI displays and copies canonical `db://<slug>` SecretRefs. | Preserve with final verification | frontend unit |
| FR-004 | implemented_verified | `api_service/services/settings_catalog.py`, `tests/unit/api_service/api/routers/test_settings_api.py` reject raw secret refs | Preserve with final verification | existing pytest |
| FR-005 | implemented_verified | `integrations.github.token_ref` stores `env://` and backend returns reference only | Preserve with final verification | existing pytest |
| FR-006 | implemented_verified | `GET /api/v1/secrets/{slug}/validate` returns metadata-only diagnostics with status and timestamp. | Preserve with final verification | API unit |
| FR-007 | implemented_verified | Settings catalog primes managed-secret metadata and diagnoses active, missing, and inactive `db://` refs. | Preserve with final verification | service/API unit |
| FR-008 | implemented_verified | Registry only exposes explicit SecretRef descriptor; unsafe payload scan exists | Preserve with final verification | existing pytest |
| FR-009 | implemented_verified | Settings PATCH uses catalog descriptors and auth dependencies | Preserve with final verification | existing pytest |
| FR-010 | implemented_verified | MM-540 preserved in `spec.md` | Preserve in downstream artifacts and verification | traceability review |
| SC-001 | implemented_verified | API/service tests assert `secretRef` metadata, validation diagnostics, and no plaintext/ciphertext exposure. | Preserve with final verification | API unit |
| SC-002 | implemented_verified | Frontend test asserts SecretRef display/copy behavior and plaintext suppression. | Preserve with final verification | frontend unit |
| SC-003 | implemented_verified | Existing test rejects `ghp_raw_plaintext` | Preserve with final verification | existing pytest |
| SC-004 | implemented_verified | Settings API tests cover active, disabled, and missing `db://` diagnostics. | Preserve with final verification | service/API unit |
| SC-005 | implemented_verified | MM-540 and source IDs present in `spec.md` | Preserve through tasks and final verification | traceability review |
| DESIGN-REQ-002 | implemented_verified | Secrets service owns metadata validation and Settings UI exposes references without redefining storage. | Preserve with final verification | API + UI unit |
| DESIGN-REQ-010 | implemented_verified | Generic override rejection and SecretRef storage exist | Preserve and cover `db://` diagnostics | API/service unit |
| DESIGN-REQ-011 | implemented_verified | Managed-secret metadata, validation, and UI display expose references and diagnostics without plaintext readback. | Preserve with final verification | API + UI unit |
| DESIGN-REQ-018 | implemented_verified | Validation and broken-reference diagnostics are redacted and tested for `db://` SecretRefs. | Preserve with final verification | API/service unit |

## Technical Context

- **Language/Version**: Python 3.12 and TypeScript/React.
- **Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, React, TanStack Query, Vitest, pytest.
- **Storage**: Existing `managed_secrets` and settings override tables; no new persistent tables.
- **Unit Testing**: pytest for API/service behavior; Vitest/Testing Library for frontend component behavior.
- **Integration Testing**: Existing FastAPI route tests via TestClient / AsyncClient, Settings API route tests, and the repo unit wrapper with `--ui-args` to execute the focused React component test alongside backend coverage.
- **Target Platform**: Mission Control Settings page and MoonMind API.
- **Project Type**: Web application plus API service.
- **Performance Goals**: Secret metadata and diagnostics remain bounded list/detail responses; no plaintext is serialized.
- **Constraints**: Do not redefine Secrets System semantics, expose plaintext, add raw credential fields to generic settings, or trust client-supplied descriptor metadata.
- **Scale/Scope**: One hardening story over existing managed-secret metadata, validation, and SecretRef diagnostics.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Existing Settings and Secrets surfaces are extended at their boundaries.
- **II. One-Click Agent Deployment**: PASS. No new dependency or setup step.
- **III. Avoid Vendor Lock-In**: PASS. SecretRefs remain backend-neutral.
- **IV. Own Your Data**: PASS. Managed secrets remain in operator-controlled storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes.
- **VI. Replaceable Scaffolding**: PASS. Small boundary hardening, no broad framework.
- **VII. Powerful Runtime Configurability**: PASS. SecretRefs remain runtime configuration references.
- **VIII. Modular and Extensible Architecture**: PASS. API schema/service/UI component changes remain scoped.
- **IX. Resilient by Default**: PASS. Broken SecretRefs become explicit diagnostics.
- **X. Facilitate Continuous Improvement**: PASS. Validation results become operator-readable and redacted.
- **XI. Spec-Driven Development**: PASS. Spec, plan, tasks, tests, implementation, and verification are ordered.
- **XII. Documentation Separation**: PASS. Canonical docs are source requirements; runtime work stays in feature artifacts.
- **XIII. Pre-Release Velocity**: PASS. No compatibility alias is introduced for internal contracts.

## Project Structure

```text
api_service/api/schemas.py
api_service/api/routers/secrets.py
api_service/services/secrets.py
api_service/services/settings_catalog.py
frontend/src/components/secrets/SecretManager.tsx
frontend/src/components/secrets/SecretManager.test.tsx
tests/unit/api/test_secrets_api.py
tests/unit/services/test_secrets.py
tests/unit/api_service/api/routers/test_settings_api.py
specs/270-secret-safe-settings-managed-secrets/
```

## Phase 0 Research

See [research.md](./research.md).

## Phase 1 Design

See [data-model.md](./data-model.md), [contracts/secret-safe-settings-contract.md](./contracts/secret-safe-settings-contract.md), and [quickstart.md](./quickstart.md).

## Complexity Tracking

No constitution violations or added complexity exceptions.

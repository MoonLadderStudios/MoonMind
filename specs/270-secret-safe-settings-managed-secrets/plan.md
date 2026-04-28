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
| FR-001 | partial | `frontend/src/components/secrets/SecretManager.tsx` clears create/update plaintext; rotate flow closes modal | Add tests preserving one-way behavior while modifying UI | frontend unit |
| FR-002 | partial | `SecretMetadataResponse` omits ciphertext but lacks `secretRef` | Add `secretRef` to metadata response derived from slug | API unit |
| FR-003 | missing | Secret list shows slug/status/actions only | Add copy SecretRef action and UI test | frontend unit |
| FR-004 | implemented_verified | `api_service/services/settings_catalog.py`, `tests/unit/api_service/api/routers/test_settings_api.py` reject raw secret refs | Preserve with final verification | existing pytest |
| FR-005 | implemented_verified | `integrations.github.token_ref` stores `env://` and backend returns reference only | Preserve with final verification | existing pytest |
| FR-006 | partial | `GET /api/v1/secrets/{slug}/validate` returns only `{valid}` after plaintext lookup | Add redacted diagnostic response with status and timestamp | API unit |
| FR-007 | partial | Settings catalog diagnoses missing `env://`; `db://` status not checked | Add `db://` managed-secret diagnostics for missing/inactive refs | service/API unit |
| FR-008 | implemented_verified | Registry only exposes explicit SecretRef descriptor; unsafe payload scan exists | Preserve with final verification | existing pytest |
| FR-009 | implemented_verified | Settings PATCH uses catalog descriptors and auth dependencies | Preserve with final verification | existing pytest |
| FR-010 | implemented_verified | MM-540 preserved in `spec.md` | Preserve in downstream artifacts and verification | traceability review |
| SC-001 | partial | Existing metadata tests assert no ciphertext; validation response too small | Add response redaction assertions for create/list/validate | API unit |
| SC-002 | missing | No SecretRef copy action | Add UI copy test and implementation | frontend unit |
| SC-003 | implemented_verified | Existing test rejects `ghp_raw_plaintext` | Preserve with final verification | existing pytest |
| SC-004 | partial | Missing `env://` diagnostics; no `db://` inactive diagnostics | Add managed secret status diagnostics | service/API unit |
| SC-005 | implemented_verified | MM-540 and source IDs present in `spec.md` | Preserve through tasks and final verification | traceability review |
| DESIGN-REQ-002 | partial | Secrets service owns storage/resolution; Settings UI exposes workflows | Keep changes at API/UI boundary without redefining storage | API + UI unit |
| DESIGN-REQ-010 | implemented_verified | Generic override rejection and SecretRef storage exist | Preserve and cover `db://` diagnostics | API/service unit |
| DESIGN-REQ-011 | partial | No plaintext readback mostly exists; validation diagnostics need redacted shape | Add redacted validation response and UI SecretRef reference | API + UI unit |
| DESIGN-REQ-018 | partial | Most security requirements exist; validation diagnostics and db diagnostics incomplete | Add focused hardening | API/service unit |

## Technical Context

- **Language/Version**: Python 3.12 and TypeScript/React.
- **Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, React, TanStack Query, Vitest, pytest.
- **Storage**: Existing `managed_secrets` and settings override tables; no new persistent tables.
- **Unit Testing**: pytest for API/service behavior; Vitest/Testing Library for frontend component behavior.
- **Integration Testing**: Existing API route tests via FastAPI TestClient / AsyncClient and Settings API route tests.
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

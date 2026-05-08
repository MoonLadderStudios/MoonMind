# Implementation Plan: Remediation Authority Policy

**Branch**: `319-remediation-authority-policy` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/319-remediation-authority-policy/spec.md`

## Summary

MM-619 requires remediation authority to be explicit, policy-bound, permission-aware, approval-aware, and secret-safe. Repo gap analysis found the required runtime behavior already present in the remediation action authority service, Temporal execution service remediation-link validation, API remediation-link serialization, and Mission Control remediation creation controls. This plan preserves the new MM-619 MoonSpec artifacts and treats remaining work as verification and traceability rather than new implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/remediation_actions.py`, `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_action_authority_enforces_authority_modes` | preserve validation and action evaluation | unit |
| FR-002 | implemented_verified | `tests/unit/workflows/temporal/test_temporal_service.py::test_create_execution_rejects_unsupported_remediation_authority_mode`, `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_action_authority_rejects_unsupported_authority_mode` | no new implementation | unit |
| FR-003 | implemented_verified | `test_remediation_action_authority_enforces_authority_modes` | no new implementation | unit |
| FR-004 | implemented_verified | `test_remediation_action_authority_requires_approval_for_gated_mode`, `test_record_remediation_approval_decision_appends_bounded_audit` | no new implementation | unit |
| FR-005 | implemented_verified | `test_remediation_action_authority_enforces_profile_permissions_and_risk` | no new implementation | unit |
| FR-006 | implemented_verified | `RemediationSecurityProfile`, `_security_profile_error`, `test_remediation_action_authority_enforces_profile_permissions_and_risk` | no new implementation | unit |
| FR-007 | implemented_verified | `RemediationActionAuthorityResult.to_dict`, `test_remediation_action_authority_requires_approval_for_gated_mode`, `test_remediation_action_authority_redacts_audits_and_deduplicates` | no new implementation | unit |
| FR-008 | implemented_verified | `RemediationPermissionSet`, `list_allowed_actions`, `test_remediation_action_authority_lists_policy_compatible_actions`, `api_service/api/routers/executions.py` approval state serialization | no new implementation | unit + API |
| FR-009 | implemented_verified | `test_remediation_action_authority_lists_policy_compatible_actions`, `test_remediation_action_authority_does_not_advertise_raw_admin_actions` | no new implementation | unit |
| FR-010 | implemented_verified | `_redact_text`, `redact_sensitive_text`, `test_remediation_action_authority_redacts_audits_and_deduplicates`, `test_remediation_action_redaction_handles_null_and_single_segment_paths` | no new implementation | unit |
| FR-011 | implemented_verified | `moonmind/workflows/temporal/remediation_context.py`, `tests/unit/workflows/temporal/test_remediation_context.py` bounded context and evidence tests from MM-618/MM-617 | preserve as dependency evidence | unit |
| FR-012 | implemented_verified | `test_remediation_action_authority_denies_raw_access_and_unknown_targets` | no new implementation | unit |
| FR-013 | implemented_verified | authority service redaction tests plus API approval-link serialization tests | no new implementation | unit + API |
| FR-014 | implemented_verified | `_RAW_ACCESS_ACTION_KINDS`, `test_remediation_action_authority_does_not_advertise_raw_admin_actions`, `test_remediation_action_authority_denies_raw_access_and_unknown_targets` | no new implementation | unit |
| FR-015 | implemented_verified | `raw_access_action_denied` decision path and unsupported-action tests | no new implementation | unit |
| FR-016 | implemented_verified | new MM-619 artifacts created in this feature directory; traceability check passed | preserve artifact traceability in final evidence | traceability |
| SCN-001 | implemented_verified | observe-only unit test | no new implementation | unit |
| SCN-002 | implemented_verified | approval-gated unit and service audit tests | no new implementation | unit |
| SCN-003 | implemented_verified | admin profile/principal tests | no new implementation | unit |
| SCN-004 | implemented_verified | high-risk approval tests | no new implementation | unit |
| SCN-005 | implemented_verified | permission and approval-state tests | no new implementation | unit + API |
| SCN-006 | implemented_verified | redaction tests and bounded context dependency tests | no new implementation | unit |
| SCN-007 | implemented_verified | raw-access denial tests | no new implementation | unit |
| DESIGN-REQ-013 | implemented_verified | authority modes, profiles, permissions, approvals, and audit identity tests | preserve traceability | unit |
| DESIGN-REQ-014 | implemented_verified | secret-safe redaction and mediated evidence tests | preserve traceability | unit + API |
| DESIGN-REQ-017 | implemented_verified | raw operation deny-list and no-advertise tests | preserve traceability | unit |
| SC-001 | implemented_verified | focused remediation authority unit tests | rerun focused unit tests | unit |
| SC-002 | implemented_verified | Temporal service and API router tests | rerun focused API/service tests | unit |
| SC-003 | implemented_verified | redaction tests | rerun focused unit tests | unit |
| SC-004 | implemented_verified | capability-list tests | rerun focused unit tests | unit |
| SC-005 | implemented_verified | traceability check covers MM-619 and source design IDs across the feature artifact set | preserve in final verification | traceability |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for existing Mission Control remediation controls  
**Primary Dependencies**: Pydantic v2, SQLAlchemy async ORM, FastAPI, Temporal Python SDK, React, Vitest, pytest  
**Storage**: Existing Temporal execution records and `execution_remediation_links`; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets  
**Integration Testing**: `./tools/test_integration.sh` for compose-backed `integration_ci` when runtime wiring changes; not required for this verification-only story because no code changes are planned  
**Target Platform**: MoonMind API service, Temporal workflow service layer, Mission Control UI  
**Project Type**: Web service plus operational dashboard  
**Performance Goals**: Authority evaluation remains bounded, deterministic, and safe to serialize in workflow/activity paths  
**Constraints**: No raw secrets in workflow payloads, logs, artifacts, summaries, or UI previews; unsupported raw operations fail closed; no compatibility aliases for unsupported authority values  
**Scale/Scope**: One remediation authority/policy story for MM-619

## Constitution Check

- Principle I, Orchestrate, Don't Recreate: PASS. Existing behavior remains behind MoonMind orchestration services and adapter-visible contracts.
- Principle III, Avoid Vendor Lock-In: PASS. Remediation authority is provider-neutral and typed at the MoonMind service boundary.
- Principle IV, Own Your Data: PASS. Evidence and audit remain artifact/ref-backed and locally controlled.
- Principle IX, Resilient by Default: PASS. Unsupported values fail closed; action decisions are idempotency-keyed and bounded.
- Principle XI, Spec-Driven Development Is the Source of Truth: PASS. This MM-619 artifact set preserves the canonical Jira brief and maps requirements before verification.
- Principle XII, Canonical Documentation Separates Desired State from Migration Backlog: PASS. This work records execution state under `specs/319-remediation-authority-policy/`, not canonical docs.
- Principle XIII, Pre-Release Compatibility Policy: PASS. No compatibility aliases or fallback transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/319-remediation-authority-policy/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-authority-policy.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_actions.py
├── remediation_context.py
└── service.py

api_service/api/routers/
└── executions.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

tests/unit/
├── workflows/temporal/test_remediation_context.py
├── workflows/temporal/test_temporal_service.py
└── api/routers/test_executions.py
```

**Structure Decision**: Use the existing remediation service, API, and UI boundaries. The MM-619 story is verification-first because the required behavior is already implemented and tested in those boundaries.

## Complexity Tracking

No constitution violations.

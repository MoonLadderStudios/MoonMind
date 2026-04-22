# Implementation Plan: Remediation Authority Boundaries

**Branch**: `228-remediation-authority-boundaries` | **Date**: 2026-04-22 | **Spec**: `specs/228-remediation-authority-boundaries/spec.md`
**Input**: Single-story feature specification from `/specs/228-remediation-authority-boundaries/spec.md`

## Summary

Implement MM-453 by adding the missing runtime authority boundary for remediation actions. Existing remediation create/link code validates target identity, supported authority modes, and action policy refs; existing context/evidence tools preserve server-mediated evidence access and fresh target health reads. This story adds a narrow typed remediation action authority service that evaluates authority mode, caller permissions, security profile, action risk, approval records, idempotency keys, and redaction-safe outputs before any side-effecting remediation action is accepted as executable. Tests are service-boundary unit tests plus an integration-style service flow that exercises create/link/context/action preparation together without raw host, Docker, SQL, or storage access.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `ALLOWED_REMEDIATION_AUTHORITY_MODES` in `moonmind/workflows/temporal/service.py`; unsupported-mode unit test exists | add focused verification in authority boundary tests | unit |
| FR-002 | partial | `observe_only` is persisted on remediation links; no action execution gate exists | add action authority service rejection for side effects | unit + integration |
| FR-003 | missing | no approval-gated action execution contract found | add proposal/dry-run/approval validation | unit + integration |
| FR-004 | partial | `actionPolicyRef` allowlist exists; no action allowlist execution gate found | add allowlisted action evaluation under policy/profile | unit |
| FR-005 | missing | no high-risk action gating implementation found | add high-risk approval policy enforcement | unit |
| FR-006 | missing | no `securityProfileRef` validation or persisted profile evidence found | add named security profile requirement for elevated execution | unit |
| FR-007 | partial | owner visibility checks exist for remediation target references; no distinct launch/approve/audit permission model found | add compact permission model to authority decisions | unit |
| FR-008 | missing | no admin-remediation permission checks beyond target ownership found | reject admin launch/profile/approval/audit without explicit permissions | unit |
| FR-009 | partial | generic intervention audit exists; remediation link has summary/outcome fields; no privileged remediation audit record shape found | add typed remediation action audit record output | unit |
| FR-010 | missing | no typed side-effecting remediation action registry found | add typed allowlisted action decision surface | unit |
| FR-011 | missing | no remediation action idempotency ledger found | add in-memory/service-level deterministic idempotency result for persisted action requests in this slice | unit |
| FR-012 | partial | unsupported authority and action policy fail closed; profiles/approvals not yet validated | extend fail-closed validation to profiles and approvals | unit |
| FR-013 | implemented_unverified | no raw host/Docker/SQL surface is exposed by current remediation services | add regression assertions that authority service does not expose raw execution channels | unit |
| FR-014 | implemented_verified | `RemediationEvidenceToolService` gates artifacts/logs through context refs and artifact service | no new implementation; preserve via regression | unit |
| FR-015 | implemented_unverified | artifact/log redaction helpers exist; remediation context excludes raw bodies; no action-output redaction test found | redact action request/result/audit payloads | unit |
| FR-016 | implemented_unverified | target reference owner check returns unauthorized without record details | add direct-fetch authority regression | unit |
| FR-017 | missing | no stronger-authority redaction override test found | enforce redaction after authority decision | unit |
| FR-018 | implemented_verified | evidence tools expose live follow as read-only observation and action preparation separately | no new implementation; preserve via regression | unit |
| SC-001 | partial | create-time mode validation exists | add authority decision tests for all modes | unit |
| SC-002 | missing | no approval-gated/admin action tests found | add action execution decision tests | unit |
| SC-003 | missing | no high-risk approval tests found | add risk-gated tests | unit |
| SC-004 | missing | no distinct admin permission tests found | add permission matrix tests | unit |
| SC-005 | partial | generic audit exists; no remediation action audit shape | add audit output tests | unit |
| SC-006 | partial | context/evidence redaction exists; no action result redaction | add redaction tests | unit |
| SC-007 | partial | unsupported mode/action policy fail closed; other cases missing | add fail-closed tests | unit |
| SC-008 | missing | this artifact set is new | preserve MM-453 and source IDs through tasks/verification | final verify |
| DESIGN-REQ-010 | partial | authority modes are accepted and persisted | enforce mode semantics at action boundary | unit + integration |
| DESIGN-REQ-011 | partial | action policy ref validation exists; named principal/profile, approval, and audit behavior missing | implement security profile, permission, approval, risk, audit, and idempotency decision contract | unit + integration |
| DESIGN-REQ-024 | partial | evidence access is server-mediated; action output redaction and no-raw-access assertions missing | add redaction and no-raw-channel enforcement tests | unit |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: SQLAlchemy async ORM, Pydantic v2 style validation patterns, existing Temporal execution service, existing remediation context/evidence services
**Storage**: Existing `execution_remediation_links` row fields and artifact-backed action/audit payloads; no new database table for this slice
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
**Integration Testing**: Service-boundary flow in `tests/unit/workflows/temporal/test_remediation_context.py`; no compose-backed integration needed because this slice does not cross external services
**Target Platform**: Linux server / Docker Compose deployment
**Project Type**: FastAPI control plane plus Temporal workflow service boundary
**Performance Goals**: Action authority evaluation remains bounded to one remediation link lookup, one context read when needed, and compact policy/profile checks
**Constraints**: Runtime mode; preserve MM-453 traceability; do not expose raw host shell, Docker daemon, SQL, presigned URLs, storage keys, local paths, or raw secrets; do not change Temporal workflow payload shapes
**Scale/Scope**: One remediation execution linked to one target execution; one action request decision at a time with deterministic idempotency key handling

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The action boundary orchestrates allowed remediation tools without changing agent cognition.
- II. One-Click Agent Deployment: PASS. No new service dependency or secret is introduced.
- III. Avoid Vendor Lock-In: PASS. Remediation authority is provider-neutral.
- IV. Own Your Data: PASS. Decisions and audit payloads remain MoonMind-owned local artifacts/metadata.
- V. Skills Are First-Class and Easy to Add: PASS. The typed boundary can be consumed by remediation skills without changing instruction bundles.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. A thin service contract is covered by focused boundary tests.
- VII. Runtime Configurability: PASS. Behavior is driven by authority mode, policy refs, approvals, and security profile inputs.
- VIII. Modular Architecture: PASS. Work stays in remediation temporal service modules and tests.
- IX. Resilient by Default: PASS. Unsupported or unauthorized actions fail closed; idempotency keys prevent duplicate side effects.
- X. Continuous Improvement: PASS. Audit records make remediation decisions reviewable.
- XI. Spec-Driven Development: PASS. This plan follows one MM-453 story with traceable requirements.
- XII. Canonical Documentation Separation: PASS. Desired-state docs remain unchanged; implementation artifacts live under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. Adds the canonical runtime boundary without aliases or hidden compatibility transforms.

## Project Structure

### Documentation (this feature)

```text
specs/228-remediation-authority-boundaries/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-authority-boundaries.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
├── remediation_actions.py
├── remediation_context.py
├── remediation_tools.py
└── service.py

tests/unit/workflows/temporal/
└── test_remediation_context.py
```

**Structure Decision**: Add the authority decision surface as a new Temporal remediation boundary module and cover it through the existing remediation context/evidence test file so the full linked remediation flow stays visible in one unit-test area.

## Complexity Tracking

None.

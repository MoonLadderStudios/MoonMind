# Implementation Plan: Remediation Action Registry

**Branch**: `229-remediation-action-registry` | **Date**: 2026-04-22 | **Spec**: `specs/229-remediation-action-registry/spec.md`
**Input**: Single-story feature specification from `/specs/229-remediation-action-registry/spec.md`

## Summary

Implement MM-454 by completing the typed remediation action registry boundary. Existing remediation link, context, evidence, and authority-mode code already evaluate side-effecting action requests through `RemediationActionAuthorityService`; this story adds the missing registry listing contract and v1-shaped request/result serialization while preserving the existing no-raw-execution boundary. Unit and service-boundary tests cover policy-compatible listing, action validation, high-risk approval gating, idempotency, audit redaction, raw access denial, missing target handling, and prepared-action integration.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/remediation_actions.py` evaluates typed action kinds and denies raw access; tests deny `raw_host_shell` | complete | unit |
| FR-002 | implemented_verified | `list_allowed_actions()` returns enabled profile-compatible actions with risk/input metadata | complete | unit |
| FR-003 | implemented_verified | unsupported/blank action path returns `unsupported_action_kind`; existing tests cover unsupported actions | complete | unit |
| FR-004 | implemented_verified | `evaluate_action_request()` validates link, permissions, profile, approval, risk, dry-run, and idempotency | complete | unit + service-boundary |
| FR-005 | implemented_verified | allowed execution requires mode/profile/policy/risk approval checks | complete | unit |
| FR-006 | implemented_verified | high-risk `terminate_session` returns `approval_required` without approval | complete | unit |
| FR-007 | implemented_verified | request-shape cache keys include workflow, idempotency key, action, and dry-run state | complete | unit |
| FR-008 | implemented_verified | `to_dict()` emits v1 request envelope with action identity, requester, target, risk, dry-run, idempotency, and bounded params | complete | unit |
| FR-009 | implemented_verified | `to_dict()` emits v1 result envelope with status, refs, verification fields, and side effects | complete | unit |
| FR-010 | implemented_verified | result status mapping covers applied, approval_required, precondition_failed, rejected, and failed-style fallback | complete | unit |
| FR-011 | implemented_verified | audit includes requesting principal, execution principal, decision, reason, and redacted summary | complete | unit |
| FR-012 | implemented_verified | redaction tests cover secrets, headers, paths, and missing-target summary leakage | complete | unit |
| FR-013 | implemented_verified | action metadata and result envelope include verification hints for executable decisions | complete | unit |
| FR-014 | implemented_verified | action catalog exposes only enabled supported actions; unavailable future actions are not listed | complete | unit |
| FR-015 | implemented_verified | service remains an authority decision boundary and does not execute Docker/host operations | complete | unit inspection |
| FR-016 | implemented_verified | raw host and raw-prefixed actions are denied before execution | complete | unit |
| FR-017 | implemented_verified | missing link, missing idempotency key, missing target-view permission, disabled profile, and missing approvals fail closed | complete | unit |
| FR-018 | implemented_verified | MM-454 is preserved in spec, plan, tasks, quickstart, and final report | complete | final verify |
| SC-001 | implemented_verified | `test_remediation_action_authority_lists_policy_compatible_actions` | complete | unit |
| SC-002 | implemented_verified | authority validation tests in `test_remediation_context.py` | complete | unit |
| SC-003 | implemented_verified | high-risk approval assertions for `terminate_session` | complete | unit |
| SC-004 | implemented_verified | duplicate/idempotency test returns the original result | complete | unit |
| SC-005 | implemented_verified | v1 payload assertions in profile/risk test | complete | unit |
| SC-006 | implemented_verified | raw-access rejection test | complete | unit |
| SC-007 | implemented_verified | redaction and missing-target tests | complete | unit |
| SC-008 | implemented_verified | MoonSpec artifacts preserve MM-454 and DESIGN-REQ mappings | complete | final verify |
| DESIGN-REQ-012 | implemented_verified | typed catalog metadata and authority decision service | complete | unit |
| DESIGN-REQ-013 | implemented_verified | v1 request/result/audit serialization | complete | unit |
| DESIGN-REQ-023 | implemented_verified | disabled/unsupported/unavailable actions denied or omitted without raw fallback | complete | unit |
| DESIGN-REQ-024 | implemented_verified | raw access kinds denied and redaction enforced | complete | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, existing Temporal execution/remediation services, Pydantic-adjacent schema conventions through typed dataclasses  
**Storage**: Existing `execution_remediation_links` rows plus compact in-process idempotency cache for service evaluation; no new persistent table  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`  
**Integration Testing**: Service-boundary flow in `tests/unit/workflows/temporal/test_remediation_context.py`; no compose-backed integration required because the story does not cross external services or credentials  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal workflow service boundary  
**Performance Goals**: Action listing and evaluation remain bounded to local catalog/profile checks plus one remediation-link lookup for evaluation  
**Constraints**: Runtime mode; preserve MM-454 traceability; no raw host shell, Docker daemon, arbitrary SQL, raw storage, secret read, or redaction bypass; do not introduce new persistent storage  
**Scale/Scope**: One remediation execution linked to one target execution; one action request decision at a time with deterministic idempotency handling

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The registry gates MoonMind-owned actions without replacing agent behavior.
- II. One-Click Agent Deployment: PASS. No new service, external credential, or deployment dependency is introduced.
- III. Avoid Vendor Lock-In: PASS. Action decisions are provider-neutral and behind MoonMind runtime boundaries.
- IV. Own Your Data: PASS. Request/result/audit payloads are local bounded data structures.
- V. Skills Are First-Class and Easy to Add: PASS. Remediation skills can consume typed action metadata without mutable instruction-bundle changes.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. The boundary is thin and covered by focused tests.
- VII. Runtime Configurability: PASS. Decisions are driven by profile, permissions, authority mode, risk, and approval inputs.
- VIII. Modular Architecture: PASS. Changes stay in `remediation_actions.py` and the existing remediation test module.
- IX. Resilient by Default: PASS. Unsupported or unauthorized actions fail closed and idempotency prevents duplicate decisions for retries.
- X. Continuous Improvement: PASS. Audit payloads make remediation decisions reviewable.
- XI. Spec-Driven Development: PASS. This plan follows the MM-454 one-story spec.
- XII. Canonical Documentation Separation: PASS. Canonical docs remain unchanged; runtime work and temporary Jira input are separated.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility alias layer or fallback raw access path is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/229-remediation-action-registry/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-action-registry.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
└── remediation_actions.py

tests/unit/workflows/temporal/
└── test_remediation_context.py
```

**Structure Decision**: Keep the action registry as a narrow Temporal remediation service-boundary module and test it inside the existing remediation context test file so link/context/action behavior remains visible in one local test area.

## Complexity Tracking

None.

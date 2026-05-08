# Implementation Plan: Remediation Action Contracts

**Branch**: `320-remediation-action-contracts` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:f14332d1-2a04-407d-acdd-23b4fa3c3448/repo/specs/320-remediation-action-contracts/spec.md`

## Summary

MM-620 requires the remediation runtime to expose only typed, policy-compatible administrative actions and to preserve durable v1 request/result evidence for every requested action. Repo gap analysis found substantial existing implementation in `moonmind/workflows/temporal/remediation_actions.py`, `moonmind/workflows/temporal/remediation_tools.py`, and `tests/unit/workflows/temporal/test_remediation_context.py`: registry listing, authority decisions, high-risk approval gating, raw-access denial, mutation guard checks, pre-action freshness reads, and action lifecycle artifact publication already exist. Remaining work should be TDD-first and focused: tighten v1 request/result contracts, validate result statuses and required fields, verify unsupported raw operation classes more completely, and add hermetic integration evidence for the service/artifact boundary.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `RemediationActionAuthorityService.list_allowed_actions()` and `test_remediation_action_authority_lists_policy_compatible_actions` | preserve registry behavior | unit |
| FR-002 | implemented_verified | `_ACTION_CATALOG` metadata and `test_remediation_action_authority_lists_canonical_mm483_action_registry` assert target type, inputs, risk, preconditions, idempotency, verification, audit shape | preserve metadata behavior | unit |
| FR-003 | partial | authority evaluation, `RemediationMutationGuardService.evaluate()`, and `RemediationEvidenceToolService.prepare_action_request()` exist, but action input validation against listed metadata is not fully proven | add tests first for input/precondition/idempotency shape validation; implement missing validation if tests fail | unit + integration |
| FR-004 | implemented_unverified | `RemediationActionAuthorityResult.to_dict()` produces a v1 `request`; `execute_action()` publishes `remediation.action_request` artifact, but artifact payload shape is not deeply asserted | add contract tests for published request evidence fields and redaction | unit + integration |
| FR-005 | partial | `execute_action()` publishes `remediation.action_result`, but the published payload currently lacks explicit `message`, `appliedAt`, `verificationRequired`, and `verificationHint` fields required by the spec | add failing unit/integration tests, then complete the v1 result artifact payload | unit + integration |
| FR-006 | partial | `RemediationActionAuthorityResult.to_dict()` maps decisions to some statuses; `execute_action()` accepts arbitrary executor `status` strings without enum validation | add status enum validation and tests for applied, no_op, rejected, precondition_failed, approval_required, timed_out, and failed | unit |
| FR-007 | implemented_verified | high-risk catalog entries, approval logic, and `test_remediation_action_authority_enforces_profile_permissions_and_risk` | preserve high-risk approval behavior | unit |
| FR-008 | implemented_unverified | raw access deny paths and no-advertise tests exist for host/Docker/SQL/storage classes; volume, network, secret-reading, and redaction-bypass variants need explicit evaluation proof | add explicit unsupported-operation tests; implement deny-list expansion if any variant is only treated as a generic unsupported action | unit |
| FR-009 | implemented_verified | unsupported/legacy action kinds are filtered or denied in `list_allowed_actions()` and authority tests | preserve bounded omit/deny behavior | unit |
| FR-010 | implemented_verified | `spec.md`, this plan, and design artifacts preserve `MM-620` and the original preset brief | preserve traceability through tasks, verification, commit, and PR metadata | traceability |
| SCN-001 | implemented_verified | registry listing tests cover policy-compatible action metadata | rerun focused unit tests | unit |
| SCN-002 | partial | authority and mutation guard decisions exist; full v1 request artifact proof needs stronger tests | add request artifact contract tests | unit + integration |
| SCN-003 | implemented_verified | high-risk approval-required/rejected behavior covered in authority tests | rerun focused unit tests | unit |
| SCN-004 | partial | action result artifacts are published but not complete against v1 result fields | add result artifact contract tests and implementation fix | unit + integration |
| SCN-005 | implemented_unverified | raw host/Docker/SQL/storage denial covered; remaining raw operation classes require explicit proof | add unsupported-operation coverage | unit |
| SCN-006 | implemented_verified | unsupported actions are filtered or denied without raw channel exposure | rerun focused unit tests | unit |
| DESIGN-REQ-015 | partial | typed evidence/action service exists; action execution artifact boundary needs integration proof | add hermetic integration coverage for typed action request/execute/read artifact path | integration |
| DESIGN-REQ-016 | partial | registry/request/result contracts exist in code but result contract is incomplete and status validation is loose | complete v1 result/status contract | unit + integration |
| DESIGN-REQ-017 | partial | practical registry exists; exclusive lock and artifact behavior exist; v1 action evidence completeness needs proof | add focused unit/integration coverage | unit + integration |
| DESIGN-REQ-026 | partial | policy inputs influence authority/guard decisions; retry/cooldown/locking evidence exists in guard service but action contract needs end-to-end proof | add policy decision and artifact contract tests | unit + integration |
| SC-001 | implemented_verified | metadata tests assert required fields for listed actions | rerun focused unit tests | unit |
| SC-002 | implemented_unverified | request evidence is generated but not deeply asserted at artifact boundary | add request evidence artifact tests | unit + integration |
| SC-003 | partial | result evidence artifact exists but lacks all required v1 fields | add tests and implementation fix | unit + integration |
| SC-004 | implemented_verified | high-risk approval logic tested | rerun focused unit tests | unit |
| SC-005 | implemented_unverified | some raw operation classes tested; full spec list needs explicit cases | add unit tests for all unsupported operation classes | unit |
| SC-006 | implemented_verified | artifacts preserve `MM-620` and source mappings | preserve through final verification | traceability |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, SQLAlchemy async ORM, FastAPI service models where exposed, Temporal Python SDK activity/service boundaries, pytest  
**Storage**: Existing `execution_remediation_links`, Temporal execution source records, Temporal artifact metadata/content store, and in-memory guard/ledger state in the current service; no new persistent database tables planned  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets under `tests/unit/workflows/temporal/test_remediation_context.py` and related Temporal service tests  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage of the remediation action service/artifact boundary planned by this story  
**Target Platform**: MoonMind API/Temporal service runtime on Linux containers  
**Project Type**: Python web service plus Temporal orchestration services  
**Performance Goals**: Registry listing and action decision serialization remain bounded and deterministic for one remediation action request; action evidence payloads contain refs and compact metadata rather than raw logs, secrets, or large bodies  
**Constraints**: No raw secrets or raw administrative handles in workflow payloads, artifacts, logs, summaries, or UI previews; unsupported raw operations fail closed; no compatibility aliases or fallback transforms for internal action kinds/statuses; source loading and action execution stay at service/activity boundaries  
**Scale/Scope**: One remediation task evaluating one typed action request against one pinned target execution/run and one active action policy

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I, Orchestrate, Don't Recreate: PASS. Action execution remains behind MoonMind-owned typed service/control-plane boundaries rather than exposing raw host or provider access to agents.
- Principle II, One-Click Agent Deployment: PASS. No new required external service or credential is planned.
- Principle III, Avoid Vendor Lock-In: PASS. Action contracts are provider-neutral MoonMind runtime contracts.
- Principle IV, Own Your Data: PASS. Evidence is stored as local Temporal artifacts/refs and compact records, not external SaaS state.
- Principle V, Skills Are First-Class: PASS. No mutation of active or checked-in skill folders is planned; remediation action tools remain ordinary runtime surfaces.
- Principle VI, Replaceable Scaffolding: PASS. The plan tightens contracts around service boundaries and keeps cognitive/runtime behavior outside bespoke agent logic.
- Principle VII, Runtime Configurability: PASS. Policy decisions remain driven by action policy/security profile inputs rather than hardcoded operator choices.
- Principle VIII, Modular and Extensible Architecture: PASS. Work is scoped to remediation action, tool, guard, and artifact boundaries.
- Principle IX, Resilient by Default: PASS. The story strengthens idempotency, precondition, status, verification, and bounded failure behavior.
- Principle X, Continuous Improvement: PASS. Durable request/result/verification evidence improves run diagnosis and follow-up analysis.
- Principle XI, Spec-Driven Development: PASS. `spec.md` preserves the MM-620 source brief and this plan maps every in-scope requirement before task generation.
- Principle XII, Canonical Docs vs Migration Backlog: PASS. Planning and rollout notes live under `specs/320-remediation-action-contracts/`; canonical docs are source requirements only.
- Principle XIII, Pre-Release Delete Don't Deprecate: PASS. Planned status/action contract tightening should remove or reject unsupported internal values rather than add aliases.

## Project Structure

### Documentation (this feature)

```text
specs/320-remediation-action-contracts/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-action-contracts.md
└── tasks.md             # Phase 2 output only; not created by this plan step
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_actions.py      # action registry, authority decision, mutation guard contracts
├── remediation_tools.py        # typed evidence/action execution artifact boundary
├── remediation_context.py      # bounded context and lifecycle artifact publisher
└── service.py                  # remediation link creation and target/run validation

tests/unit/workflows/temporal/
├── test_remediation_context.py # primary unit coverage for registry, authority, guard, tools, artifacts
└── test_temporal_service.py    # service-boundary remediation validation coverage

tests/integration/
└── temporal/test_remediation_action_contracts.py  # hermetic integration_ci action/tool artifact boundary coverage
```

**Structure Decision**: Use the existing remediation service layout. Runtime behavior belongs in `moonmind/workflows/temporal/remediation_actions.py` and `remediation_tools.py`; durable evidence publication uses the existing remediation lifecycle publisher and Temporal artifact service. Unit tests remain focused in the existing remediation test module, with hermetic integration coverage added for the real service/artifact boundary.

## Complexity Tracking

No constitution violations.

# Implementation Plan: Remediation Lock, Ledger, and Loop Guards

**Branch**: `run-jira-orchestrate-for-mm-621-add-reme-954b708c` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/321-remediation-lock-ledger-guards/spec.md`

## Summary

MM-621 requires remediation side effects to be guarded by exclusive target locks, stable action idempotency, ledger-backed duplicate decisions, retry budgets, cooldowns, target freshness checks, and nested-remediation limits. Repo gap analysis found the core mutation guard service, durable guard state, and focused tests already present in the remediation action stack. Planned work is validation-oriented: preserve traceability, use the existing implementation paths, and run targeted unit plus hermetic integration tests before final verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `RemediationMutationGuardService.evaluate()` requires remediation/target workflow IDs and target run inputs; `tests/unit/workflows/temporal/test_remediation_context.py` covers target/remediation setup. | no new implementation | final unit verification |
| FR-002 | implemented_verified | `RemediationMutationGuardPolicy.lock_scope` defaults to `target_execution`; `_acquire_lock()` enforces one active lock per target/run; unit lock test covers conflict. | no new implementation | final unit verification |
| FR-003 | implemented_verified | Same holder/idempotency replay returns the existing lock/decision in `test_remediation_mutation_guard_enforces_exclusive_locks_and_recovery`. | no new implementation | final unit verification |
| FR-004 | implemented_verified | `_freshness_decision()` and `require_target_freshness` support fresh target checks; unit target-change test covers unavailable and materially changed target health. | no new implementation | final unit verification |
| FR-005 | implemented_verified | `_acquire_lock()` returns `mutation_lock_conflict`; unit durable restart test verifies conflict across service instances. | no new implementation | final unit verification |
| FR-006 | implemented_verified | `release_lock()` persists released lock state and later holder evaluation returns `mutation_lock_lost`; unit release/restart test covers this. | no new implementation | final unit verification |
| FR-007 | implemented_verified | `_freshness_decision()` compares pinned/current run, state, summary, and session identity; unit test verifies `rediagnose` and `escalate` outcomes. | no new implementation | final unit verification |
| FR-008 | implemented_verified | Empty idempotency key is denied with `idempotency_key_required`; action authority and guard result models expose `idempotencyKey`. | no new implementation | final unit verification |
| FR-009 | implemented_verified | `mutation_guard_ledger_state` persists ledger entries; `_hydrate_ledger_from_link()` reloads durable decisions. | no new implementation | final unit verification |
| FR-010 | implemented_verified | Duplicate ledger requests return prior decision and unsafe reuse is denied in unit ledger tests. | no new implementation | final unit verification |
| FR-011 | implemented_verified | `_evaluate_budget()` enforces max actions, per-kind attempts, and cooldowns; unit budget/cooldown test covers outcomes. | no new implementation | final unit verification |
| FR-012 | implemented_verified | `_nested_decision()` blocks self-targeting and nested remediation by default; unit nested-target test covers allowed override. | no new implementation | final unit verification |
| FR-013 | implemented_verified | Guard result includes bounded `decision` and `reason` for lock conflict, lock loss, target health unavailable, material target change, budgets, cooldowns, nested denial, and unsafe reuse. | no new implementation | final unit verification |
| FR-014 | implemented_verified | `spec.md` preserves MM-621 and the canonical Jira preset brief; this plan and generated artifacts preserve MM-621. | no new implementation | final MoonSpec verification |
| SCN-001 | implemented_verified | Unit lock/recovery test covers simultaneous target mutation conflict behavior. | no new implementation | final unit verification |
| SCN-002 | implemented_verified | Unit ledger/budget test covers replay and duplicate decision behavior. | no new implementation | final unit verification |
| SCN-003 | implemented_verified | Unit target freshness test covers changed target and precondition outcomes. | no new implementation | final unit verification |
| SCN-004 | implemented_verified | Unit budget/cooldown and lock-loss tests cover bounded operator-visible reasons. | no new implementation | final unit verification |
| SCN-005 | implemented_verified | Unit nested target test covers self and remediation-on-remediation denial by default. | no new implementation | final unit verification |
| SCN-006 | implemented_verified | Unit freshness-unavailable case covers denial when fresh target health is required but missing. | no new implementation | final unit verification |
| SC-001 | implemented_verified | Exclusive lock and conflict tests assert one holder and denied competing mutation. | no new implementation | final unit verification |
| SC-002 | implemented_verified | Ledger duplicate tests assert one prior decision and no duplicated side-effect permission. | no new implementation | final unit verification |
| SC-003 | implemented_verified | Freshness tests assert unavailable/material change paths before execution. | no new implementation | final unit verification |
| SC-004 | implemented_verified | Guard tests assert bounded reasons for lock loss, target change, budget, cooldown, and nested denial. | no new implementation | final unit verification |
| SC-005 | implemented_verified | `spec.md` and planning artifacts preserve MM-621 and source coverage IDs. | no new implementation | final MoonSpec verification |
| DESIGN-REQ-011 | implemented_verified | Remediation context/action modules use explicit remediation links, pinned target IDs, server-mediated artifacts, typed actions, redaction, idempotency, locks, and bounded failures. | no new implementation | unit + integration final verification |
| DESIGN-REQ-018 | implemented_verified | Guard freshness decision and tests cover fresh target-health checks before side effects. | no new implementation | final unit verification |
| DESIGN-REQ-019 | implemented_verified | Guard policy/service, durable state migration, and tests cover locks, ledger, budgets, cooldowns, nested defaults, and target-change guards. | no new implementation | unit + integration final verification |
| DESIGN-REQ-025 | implemented_verified | Lock conflict and precondition outcomes are explicit `mutation_lock_conflict`, `no_op`, `rediagnose`, `escalate`, or denied reasons. | no new implementation | final unit verification |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 models where exposed, SQLAlchemy async ORM, Temporal-facing activity/service boundaries, pytest, existing Temporal artifact service  
**Storage**: Existing `execution_remediation_links` row fields, including `active_lock_scope`, `active_lock_holder`, `mutation_guard_lock_state`, and `mutation_guard_ledger_state`; no new persistent storage planned  
**Unit Testing**: pytest via `./tools/test_unit.sh` with targeted Python test filters during iteration  
**Integration Testing**: pytest hermetic integration via `./tools/test_integration.sh`; focused integration coverage is `tests/integration/temporal/test_remediation_action_contracts.py`  
**Target Platform**: MoonMind server/runtime control plane in containerized Linux environments  
**Project Type**: Python backend workflow/service feature with Temporal-facing contracts and artifact evidence surfaces  
**Performance Goals**: Guard decisions remain bounded and deterministic for one remediation action request; duplicate decisions reuse ledger state without re-running side-effect authorization  
**Constraints**: No raw credentials in artifacts/logs, no unbounded upstream data in workflow history, no compatibility aliases for internal contracts, no new tables unless existing remediation link state is insufficient  
**Scale/Scope**: One independently testable MM-621 remediation mutation-safety story covering action guard evaluation, durable lock/ledger state, and evidence contract handoff

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses MoonMind's orchestration/control-plane guard around agent actions; does not replace agent behavior. |
| II. One-Click Agent Deployment | PASS | No new required external services or setup beyond existing local stack. |
| III. Avoid Vendor Lock-In | PASS | Guard logic is provider-neutral and action-kind based. |
| IV. Own Your Data | PASS | Lock/ledger state remains in operator-controlled MoonMind persistence/artifacts. |
| V. Skills Are First-Class and Easy to Add | PASS | No runtime skill source mutation; planning preserves skill/runtime boundaries. |
| VI. Replaceable Scaffolding, Thick Contracts | PASS | The existing service contract is explicit and test-covered, with compact outputs. |
| VII. Runtime Configurability | PASS | Behavior is policy-input driven, not hardcoded to one target/action. |
| VIII. Modular and Extensible Architecture | PASS | Existing remediation action/context modules isolate guard logic. |
| IX. Resilient by Default | PASS | Idempotency, locks, durable ledger state, and retry/cooldown policy directly support resiliency. |
| X. Facilitate Continuous Improvement | PASS | Bounded reasons and artifact evidence make outcomes reviewable. |
| XI. Spec-Driven Development | PASS | `spec.md`, `plan.md`, and design artifacts preserve traceability before task generation. |
| XII. Canonical Docs vs Migration Backlog | PASS | Planning/rollout detail is contained in `specs/321-remediation-lock-ledger-guards/`, not canonical docs. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or legacy transforms are planned. |

Post-Phase 1 re-check: PASS. Generated artifacts do not introduce new constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/321-remediation-lock-ledger-guards/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-mutation-guard.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_actions.py      # action authority and mutation guard service contracts
├── remediation_context.py      # remediation context and bounded evidence packaging
└── remediation_tools.py        # action evidence publication and verification artifacts

api_service/db/
└── models.py                   # execution_remediation_links persistent guard fields

api_service/migrations/versions/
└── f2a3b4c5d6e7_remediation_guard_state.py

tests/unit/workflows/temporal/
└── test_remediation_context.py # focused guard, lock, ledger, budget, freshness, nested-remediation coverage

tests/integration/temporal/
└── test_remediation_action_contracts.py # hermetic evidence/action contract coverage
```

**Structure Decision**: Use the existing backend workflow/service layout. No frontend or new package structure is needed for MM-621 planning.

## Complexity Tracking

No constitution violations or added complexity require justification.

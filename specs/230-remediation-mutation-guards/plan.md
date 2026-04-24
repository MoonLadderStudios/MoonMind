# Implementation Plan: Remediation Mutation Guards

**Branch**: `230-remediation-mutation-guards` | **Date**: 2026-04-22 | **Spec**: `specs/230-remediation-mutation-guards/spec.md`
**Input**: Single-story feature specification from `/specs/230-remediation-mutation-guards/spec.md`

## Summary

Implement MM-455 by extending the existing remediation action authority boundary with mutation guard decisions. The current runtime has remediation links, context/evidence tools, action authority decisions, in-memory idempotency for duplicate request shapes, a pre-action target-health read, and now explicit mutation lock state, action-ledger entries, retry/cooldown budgets, nested-remediation policy checks, and target-freshness decisions at the same service boundary. Side-effecting actions are allowed only after guard evaluation. Unit tests cover the guard service directly; service-boundary tests exercise linked remediation executions, current target state, and redaction-safe serialized outputs.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `TemporalExecutionRemediationLink` stores target workflow/run; guard tests evaluate linked target inputs | no further implementation | final verify |
| FR-002 | implemented_verified | `RemediationEvidenceToolService.prepare_action_request()` re-reads bounded target health | no further implementation | final verify |
| FR-003 | implemented_verified | `RemediationActionAuthorityService` denies unsupported/raw action kinds; guard denies raw-access action kinds | no further implementation | final verify |
| FR-004 | implemented_verified | `RemediationMutationGuardService` includes lock decision model/service | no further implementation | final verify |
| FR-005 | implemented_verified | `RemediationMutationGuardPolicy.lock_scope` defaults to `target_execution` | no further implementation | final verify |
| FR-006 | implemented_verified | `RemediationMutationLockDecision.to_dict()` serializes lock fields | no further implementation | final verify |
| FR-007 | implemented_verified | guard tests cover idempotent acquisition by holder/target/scope | no further implementation | final verify |
| FR-008 | implemented_verified | guard tests cover stale lock expiration and recovery | no further implementation | final verify |
| FR-009 | implemented_verified | guard tests cover lock-loss non-mutating outcome | no further implementation | final verify |
| FR-010 | implemented_verified | action authority and guard require nonblank idempotency key | no further implementation | final verify |
| FR-011 | implemented_verified | `RemediationActionLedgerDecision` and ledger entries provide explicit duplicate-suppression output | no further implementation | final verify |
| FR-012 | implemented_verified | guard tests cover duplicate replay and unsafe idempotency-key reuse | no further implementation | final verify |
| FR-013 | implemented_verified | guard budget checks enforce max actions per target | no further implementation | final verify |
| FR-014 | implemented_verified | guard budget checks enforce per-action-kind attempt limit | no further implementation | final verify |
| FR-015 | implemented_verified | guard budget checks enforce repeated-action cooldown | no further implementation | final verify |
| FR-016 | implemented_verified | guard returns bounded denial/escalation reasons for budget and cooldown outcomes | no further implementation | final verify |
| FR-017 | implemented_verified | guard tests cover self-target and nested-remediation policy decisions | no further implementation | final verify |
| FR-018 | implemented_verified | `RemediationMutationGuardPolicy.max_self_healing_depth` defaults to `1` | no further implementation | final verify |
| FR-019 | implemented_verified | target health snapshot from `prepare_action_request()` is consumable by guard evaluation | no further implementation | final verify |
| FR-020 | implemented_verified | target freshness comparison includes run, state, summary, and session identity fields | no further implementation | final verify |
| FR-021 | implemented_verified | target-change policy maps material changes to no-op, re-diagnosis, or escalation decisions | no further implementation | final verify |
| FR-022 | implemented_verified | unavailable target freshness produces bounded non-executable guard output | no further implementation | final verify |
| FR-023 | implemented_verified | guard `to_dict()` output uses existing redaction helpers for parameters and decisions | no further implementation | final verify |
| FR-024 | implemented_verified | raw-access action kinds are denied without fallback execution | no further implementation | final verify |
| FR-025 | implemented_verified | MM-455 is preserved in spec, plan, tasks, quickstart, tests, and implementation artifacts | no further implementation | final verify |
| SC-001 | implemented_verified | tests cover exclusive `target_execution` lock conflict | no further implementation | final verify |
| SC-002 | implemented_verified | tests cover stale recovery and lost-lock denial | no further implementation | final verify |
| SC-003 | implemented_verified | tests cover ledger duplicate replay | no further implementation | final verify |
| SC-004 | implemented_verified | tests cover missing idempotency and unsafe reuse | no further implementation | final verify |
| SC-005 | implemented_verified | tests cover action budget, per-kind attempt budget, and cooldown denial | no further implementation | final verify |
| SC-006 | implemented_verified | tests cover self-targeting and nested-remediation guards | no further implementation | final verify |
| SC-007 | implemented_verified | tests cover target run/state/summary/session freshness decisions | no further implementation | final verify |
| SC-008 | implemented_verified | tests cover unavailable target-health decision output | no further implementation | final verify |
| SC-009 | implemented_verified | tests cover redaction-safe guard serialization | no further implementation | final verify |
| SC-010 | implemented_verified | MoonSpec artifacts preserve MM-455 and mapped design requirements | no further implementation | final verify |
| DESIGN-REQ-009 | implemented_verified | context/action boundaries cover target/evidence/action/redaction, locks, and nested-loop prevention | no further implementation | final verify |
| DESIGN-REQ-014 | implemented_verified | mutation lock model/service implements default exclusive target lock behavior | no further implementation | final verify |
| DESIGN-REQ-015 | implemented_verified | action-ledger result model implements duplicate suppression and unsafe reuse detection | no further implementation | final verify |
| DESIGN-REQ-016 | implemented_verified | budgets, cooldowns, nested-remediation, and self-healing-depth checks are implemented | no further implementation | final verify |
| DESIGN-REQ-022 | implemented_verified | target freshness guard consumes current target-health inputs before action execution | no further implementation | final verify |
| DESIGN-REQ-023 | implemented_verified | guard failures return bounded non-mutating outcomes and redaction-safe output | no further implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: SQLAlchemy async ORM, existing Temporal execution/remediation services, dataclasses, existing redaction helpers 
**Storage**: Existing `execution_remediation_links` rows plus in-service guard state for lock and ledger decisions in this slice; no new persistent table 
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` 
**Integration Testing**: Service-boundary tests in `tests/unit/workflows/temporal/test_remediation_context.py` using async DB fixtures; no provider credentials or compose-backed integration required for this slice 
**Target Platform**: Linux server / Docker Compose deployment 
**Project Type**: FastAPI control plane plus Temporal workflow service boundary 
**Performance Goals**: Guard evaluation is bounded to in-memory policy/ledger checks plus existing remediation-link and target-health reads 
**Constraints**: Runtime mode; preserve MM-455 traceability; no raw host, Docker daemon, arbitrary SQL, secret read, storage-key, or redaction bypass; do not add persistent storage in this story 
**Scale/Scope**: One remediation execution linked to one target execution; one side-effecting action request guard decision at a time, with deterministic duplicate and lock behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The change gates MoonMind remediation actions without replacing agent behavior.
- II. One-Click Agent Deployment: PASS. No new service, external credential, or deployment dependency is introduced.
- III. Avoid Vendor Lock-In: PASS. Guard behavior is provider-neutral.
- IV. Own Your Data: PASS. Guard decisions are local bounded data structures.
- V. Skills Are First-Class and Easy to Add: PASS. Skills can consume typed guard outcomes without instruction-bundle mutation.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. The guard is a thin service boundary with focused tests.
- VII. Runtime Configurability: PASS. Budgets, cooldowns, nested policy, and target-change mode are request/policy inputs.
- VIII. Modular Architecture: PASS. Changes stay in remediation service modules and tests.
- IX. Resilient by Default: PASS. Locks, idempotency, budgets, and freshness checks directly reduce duplicate or stale side effects.
- X. Continuous Improvement: PASS. Bounded reasons and serialized outputs make guard outcomes auditable.
- XI. Spec-Driven Development: PASS. This plan follows the MM-455 one-story spec.
- XII. Canonical Documentation Separation: PASS. Canonical docs remain desired-state; temporary Jira input remains under `local-only handoffs`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility alias layer is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/230-remediation-mutation-guards/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── remediation-mutation-guards.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
├── remediation_actions.py
└── remediation_tools.py

tests/unit/workflows/temporal/
└── test_remediation_context.py
```

**Structure Decision**: Keep mutation guard decisions at the same remediation service boundary as action authority. `remediation_tools.py` remains the source of fresh target health; `remediation_actions.py` owns action guard evaluation and serialized guard outcomes.

## Complexity Tracking

None.

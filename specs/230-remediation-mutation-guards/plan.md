# Implementation Plan: Remediation Mutation Guards

**Branch**: `230-remediation-mutation-guards` | **Date**: 2026-04-22 | **Spec**: `specs/230-remediation-mutation-guards/spec.md`
**Input**: Single-story feature specification from `/specs/230-remediation-mutation-guards/spec.md`

## Summary

Implement MM-455 by extending the existing remediation action authority boundary with mutation guard decisions. The current runtime already has remediation links, context/evidence tools, action authority decisions, in-memory idempotency for duplicate request shapes, and a pre-action target-health read. This story adds explicit mutation lock state, action-ledger entries, retry/cooldown budgets, nested-remediation policy checks, and target-freshness decisions to the same service boundary so side-effecting actions are allowed only after guard evaluation. Unit tests cover the guard service directly; service-boundary tests exercise linked remediation executions, current target state, and redaction-safe serialized outputs.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `TemporalExecutionRemediationLink` stores target workflow/run | verify in guard tests | unit |
| FR-002 | implemented_verified | `RemediationEvidenceToolService.prepare_action_request()` re-reads bounded target health | keep as input to guard | unit |
| FR-003 | implemented_verified | `RemediationActionAuthorityService` denies unsupported/raw action kinds | preserve existing behavior | unit |
| FR-004 | missing | no explicit remediation mutation lock object or evaluator found | add guard lock decision model/service | unit + service-boundary |
| FR-005 | missing | no default `target_execution` lock evaluation found | add default lock policy | unit |
| FR-006 | missing | no lock record/result contract found | add serialized lock fields | unit |
| FR-007 | missing | no idempotent lock acquisition helper found | add idempotent acquisition by holder/target/scope | unit |
| FR-008 | missing | no stale lock recovery rule found | add expiration/recovery behavior | unit |
| FR-009 | missing | no lock-loss guard found | add lock-loss non-mutating outcome | unit |
| FR-010 | implemented_verified | action authority requires nonblank idempotency key | preserve and reuse | unit |
| FR-011 | partial | current `_decisions` cache is process-local and not a named ledger contract | add explicit action-ledger entry/result model | unit |
| FR-012 | implemented_unverified | current duplicate cache returns original result for same workflow/key/action/dry-run | add request-shape-aware ledger tests | unit |
| FR-013 | missing | no action count budget found | add max-actions-per-target check | unit |
| FR-014 | missing | no per-action-kind attempt budget found | add max-attempts-per-kind check | unit |
| FR-015 | missing | no remediation action cooldown found | add repeated-action cooldown check | unit |
| FR-016 | missing | no budget/cooldown terminal outcome model found | add denial/escalation reasons | unit |
| FR-017 | partial | context can carry nested policy, but no action guard enforces it | add nested-remediation policy guard | unit |
| FR-018 | missing | no default self-healing depth guard found | add default depth=1 behavior | unit |
| FR-019 | implemented_verified | `prepare_action_request()` performs current target health read | consume target snapshot in guard | unit |
| FR-020 | partial | snapshot includes run/state/summary but not session identity comparison | extend target snapshot/guard contract as needed | unit |
| FR-021 | partial | target run change is detected but no action decision consumes it | add no-op/re-diagnose/escalate decision | unit + service-boundary |
| FR-022 | implemented_unverified | missing target raises bounded `RemediationEvidenceToolError` | add guard coverage for unavailable target health | unit |
| FR-023 | implemented_verified | existing action result redaction helpers cover parameters/audit | reuse for guard outputs | unit |
| FR-024 | implemented_verified | raw access paths are denied by action authority | preserve as precondition | unit |
| FR-025 | missing | new artifacts not yet complete | preserve MM-455 in artifacts and verification | final verify |
| SC-001 | missing | no lock concurrency test found | add direct service test | unit |
| SC-002 | missing | no stale/lost lock tests found | add direct service tests | unit |
| SC-003 | partial | duplicate cache exists but not ledger-named | add ledger duplicate tests | unit |
| SC-004 | partial | missing idempotency test exists; unsafe reuse not explicit | add guard test | unit |
| SC-005 | missing | no budget/cooldown tests found | add direct service tests | unit |
| SC-006 | missing | no nested remediation guard tests found | add direct service tests | unit |
| SC-007 | partial | target run changed test exists for evidence tool only | add decision test | unit + service-boundary |
| SC-008 | implemented_unverified | missing target health errors are bounded | add guard-level assertion | unit |
| SC-009 | partial | action redaction exists; guard outputs do not exist | add serialized guard redaction tests | unit |
| SC-010 | missing | new artifacts not yet verified | final MoonSpec verify | final verify |
| DESIGN-REQ-009 | partial | existing context/action boundaries cover target/evidence/action/redaction; lock/nested loops missing | add guard service behavior | unit + service-boundary |
| DESIGN-REQ-014 | missing | no mutation lock behavior found | add lock model/service | unit |
| DESIGN-REQ-015 | partial | idempotency cache exists but no explicit ledger | add action-ledger model/result | unit |
| DESIGN-REQ-016 | missing | no budgets/cooldowns/nested guards found | add budget/cooldown/nested checks | unit |
| DESIGN-REQ-022 | partial | current health read exists; action decision not connected | add freshness decision inputs | unit |
| DESIGN-REQ-023 | partial | bounded errors exist in evidence/action services; guard failures missing | add bounded guard outcomes | unit |

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
- XII. Canonical Documentation Separation: PASS. Canonical docs remain desired-state; temporary Jira input remains under `docs/tmp`.
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
│   └── remediation-mutation-guards.md
├── checklists/
│   └── requirements.md
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

# Implementation Plan: Observable Remediation Repair and Prevention Lifecycle

**Branch**: `run-jira-orchestrate-for-mm-622-run-obse-0c34c6a4` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:ef43e84e-d4ac-47cb-bb8d-b94b2283ce80/repo/specs/322-remediation-lifecycle-repair-prevention/spec.md`

## Summary

MM-622 requires remediation runs to expose one bounded lifecycle phase, attempt only the smallest safe immediate repair, verify and classify the repair outcome, always record recurrence-prevention analysis, and preserve cancellation/rerun/Continue-As-New continuity evidence. Repo gap analysis found strong lower-level remediation primitives already present in `remediation_context.py`, `remediation_actions.py`, `remediation_tools.py`, and existing unit/integration tests: bounded phase helpers, context artifacts, action authority, mutation guards, target freshness reads, lifecycle artifact publishing, and action request/result/verification artifacts. Planned work is TDD-first and focused on the missing lifecycle decision layer: repair/prevention decision models, decision-log schema, summary publication, cancellation finalization, and end-to-end service-boundary tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `REMEDIATION_PHASES`, `normalize_remediation_phase()`, `build_remediation_summary_block()`, and `test_remediation_lifecycle_summary_audit_and_continuation_are_bounded` | preserve subordinate phase contract | final unit verification |
| FR-002 | implemented_verified | bounded phase set in `remediation_context.py` and normalization tests | preserve phase enum behavior | final unit verification |
| FR-003 | partial | `RemediationLifecyclePublisher` can publish lifecycle artifacts, but no unified lifecycle progression/summary service exists for repair/prevention decisions | add lifecycle decision/finalization service and tests | unit + integration |
| FR-004 | implemented_unverified | `prepare_action_request()` rereads target health; mutation guard evaluates lock/freshness/policy | add lifecycle tests proving repair is attempted only after fresh evidence, allowed action, and lock success | unit + integration |
| FR-005 | partial | typed action catalog and guard deny unsupported/raw actions; no lifecycle planner records smallest-plausible repair selection or skipped rationale | add repair candidate decision model and no-broadening tests | unit |
| FR-006 | partial | `execute_action()` publishes verification artifact; result statuses are action statuses, not final repair outcomes | add repair outcome classification for repaired/still_failed/not_attempted/unsafe/approval_required/escalated | unit + integration |
| FR-007 | missing | no recurrence-prevention analysis/output model found | add prevention outcome model and finalization path | unit + integration |
| FR-008 | missing | no structured prevention output for created PR/findings/no-fix/policy-blocked | add prevention output schema and artifact/summary publication | unit + integration |
| FR-009 | partial | `remediation.decision_log` artifact type is supported, but payload is ad hoc and not tied to repair/prevention decisions | add bounded decision-log contract and tests for all decision branches | unit + integration |
| FR-010 | missing | no current `execution.retry_failed_step_with_remediation_context` implementation found | add corrected-instruction retry provenance shape or explicitly denied outcome | unit |
| FR-011 | implemented_unverified | mutation guard prevents unsafe target changes; cancellation finalization is not proven for remediation lifecycle | add cancellation finalization tests and implementation if failing | unit + integration |
| FR-012 | partial | summary helper and lifecycle publisher exist; best-effort lock release/final audit publication is not a unified terminal path | add terminal finalization helper that records lock-release/audit attempts | unit + integration |
| FR-013 | implemented_unverified | freshness decision and summary `resultingTargetRunId` support exist | add lifecycle tests for pinned and resulting run recording | unit |
| FR-014 | implemented_unverified | `build_remediation_continue_as_new_state()` preserves required refs and is unit tested | add lifecycle continuity coverage using action ledger/approval/budget refs | unit |
| FR-015 | implemented_verified | `spec.md`, this plan, and generated artifacts preserve MM-622 and the preset brief | preserve traceability through tasks, implementation notes, verification, commit, and PR metadata | traceability |
| SCN-001 | implemented_verified | phase helpers and summary tests cover bounded subordinate phase values | rerun focused unit tests | unit |
| SCN-002 | partial | action execution publishes action result and verification artifacts, but final repair decision/outcome is missing | add repair outcome tests first | unit + integration |
| SCN-003 | partial | authority/guard can deny or require approval; lifecycle escalation rationale is not recorded in one decision log | add unsafe/denied/approval-required/escalated lifecycle tests | unit |
| SCN-004 | missing | no recurrence-prevention output model found | add prevention output tests and implementation | unit + integration |
| SCN-005 | partial | lock release exists in guard service; cancellation final summary/audit proof missing | add cancellation finalization tests | unit + integration |
| SCN-006 | implemented_unverified | Continue-As-New helper exists; lifecycle final summary does not yet prove all refs are carried through | add continuity tests | unit |
| SC-001 | implemented_verified | 100% bounded phase behavior covered by normalization tests | rerun focused unit tests | unit |
| SC-002 | partial | action request/result/verification artifacts are covered, but repair decision-log entry is missing | add decision-log and repair outcome artifact tests | unit + integration |
| SC-003 | missing | no prevention output coverage | add prevention output tests | unit + integration |
| SC-004 | partial | summary publisher exists; terminal cancellation/escalation/failure lock-release/audit attempts are not proven | add terminal finalization tests | unit + integration |
| SC-005 | implemented_unverified | continuation helper preserves refs; end-to-end lifecycle evidence is not yet proven | add lifecycle continuity tests | unit |
| SC-006 | implemented_verified | MM-622 and DESIGN-REQ mappings are preserved in `spec.md` and this plan | preserve in downstream artifacts | final verify |
| DESIGN-REQ-001 | partial | lower-level action/verification primitives exist; two-track repair/prevention workflow is not modeled end to end | add lifecycle decision model | unit + integration |
| DESIGN-REQ-002 | partial | lifecycle publisher can write decision logs; required fields are not enforced | add decision-log schema | unit + integration |
| DESIGN-REQ-003 | implemented_verified | bounded phases and subordinate summary helper exist | no new implementation | final unit verification |
| DESIGN-REQ-004 | partial | lifecycle artifact publisher exists; no unified progression/finalization path | add lifecycle finalizer | unit + integration |
| DESIGN-REQ-005 | partial | cancellation guard principles exist; remediation cancellation finalization not proven | add cancellation tests/implementation | unit + integration |
| DESIGN-REQ-006 | implemented_unverified | freshness/resulting-run helpers exist | add rerun summary proof | unit |
| DESIGN-REQ-007 | implemented_unverified | continuation helper exists and is unit tested | add lifecycle-specific continuity proof | unit |
| DESIGN-REQ-008 | partial | action guard supports smallest safe actions; prevention PR/finding/no-fix outputs are missing | add prevention result contract | unit + integration |
| DESIGN-REQ-009 | partial | typed actions, locks, artifacts, redaction, and continuity helpers exist; separate repair/prevention outputs and corrected-instruction provenance missing | add missing lifecycle outputs and provenance tests | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 where models are exposed, SQLAlchemy async ORM, Temporal Python SDK service/activity boundaries, existing Temporal artifact service, pytest  
**Storage**: Existing `execution_remediation_links`, Temporal execution source records, Temporal artifact metadata/content store, and existing remediation lock/ledger state; no new persistent database tables planned  
**Unit Testing**: `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` for focused remediation lifecycle unit coverage, followed by full `./tools/test_unit.sh` before completion  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; focused integration coverage should extend `tests/integration/temporal/test_remediation_action_contracts.py` or add an adjacent remediation lifecycle integration test  
**Target Platform**: MoonMind API/Temporal service runtime on Linux containers  
**Project Type**: Python backend workflow/service feature with Temporal-facing contracts and artifact evidence surfaces  
**Performance Goals**: Lifecycle decisions remain bounded to compact JSON summaries and refs for one remediation run; no raw log bodies, artifact contents, or secrets enter workflow history  
**Constraints**: Preserve MM-622 traceability; keep top-level execution state unchanged; publish evidence through server-mediated artifacts; fail closed for unsafe/unsupported actions; no compatibility aliases for internal lifecycle values; no raw credentials or presigned URLs in artifacts/logs/summaries  
**Scale/Scope**: One remediation task linked to one target execution/run, with at most one active immediate repair decision chain and one recurrence-prevention result for the final summary

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | The plan coordinates existing remediation/runtime control-plane services rather than replacing agent behavior. |
| II. One-Click Agent Deployment | PASS | No new required external services or credentials are planned. |
| III. Avoid Vendor Lock-In | PASS | Repair/prevention lifecycle contracts are MoonMind runtime contracts, not provider-specific APIs. |
| IV. Own Your Data | PASS | Evidence stays in operator-controlled execution records and artifacts. |
| V. Skills Are First-Class | PASS | No runtime skill source mutation is planned. |
| VI. Replaceable Scaffolding | PASS | Work strengthens explicit service/artifact contracts and tests around volatile runtime behavior. |
| VII. Runtime Configurability | PASS | Action authority remains policy/profile driven. |
| VIII. Modular Architecture | PASS | Changes are scoped to remediation context/actions/tools and tests. |
| IX. Resilient by Default | PASS | The story improves idempotent repair decisions, cancellation finalization, continuity, and degraded outcomes. |
| X. Continuous Improvement | PASS | Prevention outputs and decision logs create reviewable follow-up evidence. |
| XI. Spec-Driven Development | PASS | MM-622 is specified and this plan maps each requirement before task generation. |
| XII. Canonical Docs vs Migration Backlog | PASS | Planning and rollout notes stay under `specs/322-remediation-lifecycle-repair-prevention/`. |
| XIII. Delete, Don't Deprecate | PASS | Unsupported internal values should fail closed; no compatibility aliases are planned. |

Post-Phase 1 re-check: PASS. Generated artifacts preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/322-remediation-lifecycle-repair-prevention/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-lifecycle-repair-prevention.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output only; not created by this plan step
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_context.py      # lifecycle phases, summary/audit/continuity helpers, artifact publisher
├── remediation_actions.py      # action authority, policy, lock, ledger, freshness guard
├── remediation_tools.py        # typed evidence access and action request/result/verification artifacts
└── service.py                  # remediation link creation and target/run validation

tests/unit/workflows/temporal/
└── test_remediation_context.py # focused unit coverage for lifecycle helpers, guard, tools, artifacts

tests/integration/temporal/
└── test_remediation_action_contracts.py # hermetic integration_ci service/artifact boundary coverage
```

**Structure Decision**: Use the existing remediation service layout. Add the lifecycle repair/prevention decision contract at the remediation service/artifact boundary so workflows continue to carry compact refs and metadata only.

## Complexity Tracking

No constitution violations.

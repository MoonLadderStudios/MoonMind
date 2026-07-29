# Remediation Operator Acceptance Matrix

**Status:** Desired-state gate + published evidence index
**Document Class:** System / Feature Design View
**Owners:** MoonMind Platform + dashboard
**Related:** `docs/Workflows/WorkflowRemediation.md`, `docs/Workflows/RemediationVerificationCadence.md`, `docs/Observability/RemediationFleetMetrics.md`, `docs/MoonMindRoadmap.md`

This document is the consolidated, operator-driven acceptance matrix required by
issue MoonLadderStudios/MoonMind#3512 (Area 6). Autonomous remediation must not
be enabled until every scenario below has passing evidence in a
production-shaped environment, the matrix proves **no raw host/Docker/SQL/secret
authority**, and every side effect leaves complete audit evidence.

The rollout gate is fail-closed: autonomous (`admin_auto`) mutation stays
disabled behind `FEATURE_FLAGS__REMEDIATION_AUTONOMOUS_ROLLOUT_ENABLED`
(`moonmind.workflows.temporal.remediation_rollout.autonomous_remediation_rollout_enabled`)
until this matrix passes.

## How to read this matrix

Each scenario lists:

- **Behavior** — the operator-observable outcome that must hold.
- **Contract evidence** — the hermetic, deterministic test that proves the
  decision authority (fast, required, runs in the unit tier).
- **Boundary evidence** — the compose-backed integration test that proves the
  real workflow/activity/artifact boundary (run in the `integration_ci` tier or
  local dev where noted).

The consolidated contract-level suite is
`tests/unit/workflows/temporal/test_remediation_acceptance_matrix.py` — it
references each scenario by id (`AM-1` … `AM-12`) so the matrix is a single
executable index rather than scattered coverage.

## Scenarios

| Id | Scenario | Behavior | Contract evidence | Boundary evidence |
| --- | --- | --- | --- | --- |
| AM-1 | Diagnosis-only remediation | `observe_only` authority never mutates; produces diagnosis, no side effects | `test_remediation_acceptance_matrix.py::test_am1_diagnosis_only` | `tests/integration/temporal/test_ops_diagnostics_execution_contract.py` |
| AM-2 | Evidence-gated resume | Action acts on fresh target evidence; verification links the exact action result | `::test_am2_evidence_gated_resume` | `tests/integration/temporal/test_remediation_action_contracts.py` |
| AM-3 | Corrected-instruction Checkpoint Branch repair | Corrected instructions require a Checkpoint Branch, not input mutation | `::test_am3_corrected_instruction_branch` | `tests/unit/workflows/temporal/test_remediation_issue_3511.py` |
| AM-4 | Denied and approval-gated actions | Approval is durable, actor-attributed, expiring; denial produces no side effects | `::test_am4_denied_and_approval_gated` | `tests/integration/temporal/test_remediation_action_contracts.py` |
| AM-5 | Stale target / approval / lock conflict | Stale approvals are rejected; one mutating owner per target | `::test_am5_stale_and_lock_conflict` | `tests/unit/workflows/temporal/test_remediation_context.py` (mutation-guard locks) |
| AM-6 | Interrupt / cancel / cleanup | Operator takeover/cancel is explicit; cleanup/janitor state is reported | `::test_am6_interrupt_cancel_cleanup` | `tests/integration/temporal/test_remediation_action_contracts.py` (cancellation + continuity) |
| AM-7 | Unsuccessful repair followed by escalation | A failed repair escalates and is never relabeled resolved | `::test_am7_unsuccessful_repair_escalates` | `tests/integration/temporal/test_remediation_action_contracts.py` |
| AM-8 | Cumulative multi-attempt remediation | Attempts continue from the prior head; no clean-baseline restart | `::test_am8_cumulative_multi_attempt` | `tests/unit/workflows/temporal/test_remediation_workspace_head.py`, `tests/unit/omnigent/test_remediation_workspace.py` |
| AM-9 | Prevention PR creation and verification | Prevention is separate from repair; a prevention PR never relabels the target as repaired; prevention has its own verification | `::test_am9_prevention_pr_separate_from_repair` | `tests/integration/temporal/test_remediation_action_contracts.py` |
| AM-10 | Missing historical evidence / degraded mode | Missing evidence degrades, never deadlocks; `evidence_unavailable` verification is valid | `::test_am10_degraded_mode` | `tests/unit/workflows/temporal/test_remediation_context.py` (degraded evidence) |
| AM-11 | Cancellation + worker restart during phases | Locks/ledger/head survive restart; no duplicate action on replay | `::test_am11_cancellation_and_worker_restart` | `tests/unit/workflows/temporal/test_remediation_context.py` (lock/ledger persistence) |
| AM-12 | No raw authority + complete audit | No raw host/Docker/SQL/secret action is ever accepted; all side effects audited | `::test_am12_no_raw_authority` | `tests/integration/temporal/test_remediation_action_contracts.py` (raw action rejection), `tests/unit/omnigent/test_policy_authority.py` |

## Bounded-behavior gates proven by the matrix

- **Verification is the authority.** A delivered action is not a repair; the
  seven-value verification resolution taxonomy
  (`verified_resolved`, `verified_no_change`, `still_failed`, `regressed`,
  `evidence_unavailable`, `approval_required`, `verification_failed`) is the
  completion authority (AM-2, AM-7, AM-9, AM-10).
- **Prevention never relabels the target.** `resolved_after_action` requires a
  proven immediate repair; a prevention PR cannot flip the target to repaired
  (AM-9).
- **Approvals are deterministic and replay-safe.** Durable, expiring,
  actor-attributed, and rejected when target/approval/lock/credential/policy
  state drifts (AM-4, AM-5).
- **Loop/lock/cooldown/budget controls are durable.** One mutating owner per
  target, cumulative attempts, wall-clock and branch budgets, and duplicate
  suppression across replay (AM-5, AM-8, AM-11).
- **No unrestricted authority.** Raw host/Docker/SQL/secret actions are denied
  and leave no side-effect artifacts; secrets never appear in evidence (AM-12).

## Rollout checklist (Area 7)

Autonomous remediation may be enabled only when all of the following hold:

- [ ] AM-1 … AM-12 pass in a production-shaped environment.
- [ ] Action policies and approval rules are versioned and enforced.
- [ ] Loop/lock/cooldown/wall-clock/branch controls are durable.
- [ ] Every side effect produces before/after and verification evidence.
- [ ] The dashboard identifies autonomous origin and allows operator takeover.
- [ ] Fleet metrics/alerts exist for action rate, repeated failure, lock
      conflict, denial, escalation, and unverified mutation
      (`docs/Observability/RemediationFleetMetrics.md`).
- [ ] `FEATURE_FLAGS__REMEDIATION_AUTONOMOUS_ROLLOUT_ENABLED` is enabled only
      after the above, starting with diagnosis-only or a narrow allowlist of
      low/medium-risk idempotent actions — never a blanket `admin_auto` grant.

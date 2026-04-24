# Research: Remediation Mutation Guards

## Input Classification

Decision: MM-455 is a single-story runtime feature request.
Evidence: `spec.md` (Input) defines one user story, one source design path, one acceptance-criteria set, and one bounded runtime behavior area.
Rationale: The story focuses on mutation guardrails for remediation action execution and does not require story splitting.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as a broad declarative design was rejected because the Jira preset brief already selected one independently testable slice.
Test implications: Unit and service-boundary tests should target this single story.

## Existing Remediation Boundaries

Decision: Reuse `RemediationActionAuthorityService` and `RemediationEvidenceToolService` as the implementation boundaries.
Evidence: `moonmind/workflows/temporal/remediation_actions.py` evaluates action kind, authority mode, profile, risk, approval, idempotency, and redaction; `moonmind/workflows/temporal/remediation_tools.py` exposes `prepare_action_request()` for fresh target health.
Rationale: The feature is a runtime guard around side-effecting action requests, so these existing service boundaries are the narrowest place to add lock, ledger, budget, nested-policy, and target-freshness decisions.
Alternatives considered: Adding workflow-level state was rejected for this slice because no persistent storage is planned and the current action authority service is already the action-decision boundary.
Test implications: Covered by focused unit tests in `tests/unit/workflows/temporal/test_remediation_context.py`.

## DESIGN-REQ-014 - Mutation Locks

Decision: Implemented and verified; use a typed in-service mutation lock model and guard evaluator.
Evidence: `RemediationMutationGuardService` and `RemediationMutationLockDecision` in `moonmind/workflows/temporal/remediation_actions.py`; guard tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: Explicit guard state proves exclusive default `target_execution` mutation behavior, stale lock recovery, idempotent acquisition, and lock-loss denial with auditable serialized outcomes.
Alternatives considered: Relying on generic Python locks was rejected because the requirement needs auditable target/holder/scope fields and serialized outcomes.
Test implications: Covered by unit tests for exclusive conflict, idempotent reacquisition, stale recovery, and lost-lock denial.

## DESIGN-REQ-015 - Action Ledger Idempotency

Decision: Implemented and verified; duplicate suppression is exposed through an explicit action-ledger guard result.
Evidence: `RemediationActionLedgerDecision` and ledger entries in `moonmind/workflows/temporal/remediation_actions.py`; duplicate replay and unsafe-reuse tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: The story requires a canonical action ledger surface, not merely an unnamed cache. This slice models the ledger contract without adding persistent storage.
Alternatives considered: Adding a database table was rejected because the Jira brief and active technology notes specify no new persistent storage for this story.
Test implications: Covered by unit tests for duplicate replay, unsafe idempotency reuse, and serialized ledger fields.

## DESIGN-REQ-016 - Budgets, Cooldowns, And Nested Remediation

Decision: Implemented and verified; budget/cooldown policy checks and nested-remediation defaults are guard inputs.
Evidence: `RemediationMutationGuardPolicy`, `RemediationActionBudgetDecision`, and `RemediationNestedDecision` in `moonmind/workflows/temporal/remediation_actions.py`; budget, cooldown, self-target, nested, and self-healing-depth tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: These checks prevent automatic remediation from repeating destructive actions or targeting remediation tasks contrary to the source design.
Alternatives considered: Deferring budgets to a future scheduler was rejected because the acceptance criteria require terminal escalation when exhausted.
Test implications: Covered by unit tests for max action count, max attempts per action kind, cooldown violation, self-target rejection, remediation-to-remediation target rejection, and explicit nested-policy allowance.

## DESIGN-REQ-022 - Target Freshness

Decision: Implemented and verified; current target health is consumed by mutation guard evaluation before action execution.
Evidence: `RemediationTargetFreshnessDecision` and target-freshness evaluation in `moonmind/workflows/temporal/remediation_actions.py`; target freshness tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: The guard service converts material target changes into no-op, re-diagnosis, or escalation decisions before a side effect can execute.
Alternatives considered: Letting callers inspect `target_run_changed` manually was rejected because enforcement must be centralized and testable.
Test implications: Covered by service-boundary tests that pass fresh target snapshots into guard evaluation and assert non-mutating target-change outcomes.

## DESIGN-REQ-023 - Bounded Failure

Decision: Implemented and verified; mutation guard failures have explicit reason codes and serialized outcomes.
Evidence: `RemediationMutationGuardResult.to_dict()` in `moonmind/workflows/temporal/remediation_actions.py`; redaction-safe serialization tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: Operators need deterministic no-op, denial, re-diagnosis, or escalation reasons rather than unbounded waiting or fallback raw access.
Alternatives considered: Raising generic errors for every guard failure was rejected because the acceptance criteria require recorded reasons.
Test implications: Covered by unit tests asserting reason codes and redaction-safe `to_dict()` output for each guard family.

## Testing Strategy

Decision: Use unit and service-boundary tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Evidence: Adjacent remediation specs use this test module for async DB-backed remediation link, context, evidence, and action authority behavior.
Rationale: The story does not require external providers or compose-backed services; the highest-risk boundary is the local remediation service API.
Alternatives considered: Full Temporal workflow tests were rejected for this slice because no workflow signature, signal/update, or activity binding changes are planned.
Test implications: Targeted remediation tests and full `./tools/test_unit.sh` have been used as the verification path for this story.

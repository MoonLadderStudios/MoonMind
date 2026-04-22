# Research: Remediation Mutation Guards

## Input Classification

Decision: MM-455 is a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-455-moonspec-orchestration-input.md` defines one user story, one source design path, one acceptance-criteria set, and one bounded runtime behavior area.
Rationale: The story focuses on mutation guardrails for remediation action execution and does not require story splitting.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as a broad declarative design was rejected because the Jira preset brief already selected one independently testable slice.
Test implications: Unit and service-boundary tests should target this single story.

## Existing Remediation Boundaries

Decision: Reuse `RemediationActionAuthorityService` and `RemediationEvidenceToolService` as the implementation boundaries.
Evidence: `moonmind/workflows/temporal/remediation_actions.py` evaluates action kind, authority mode, profile, risk, approval, idempotency, and redaction; `moonmind/workflows/temporal/remediation_tools.py` exposes `prepare_action_request()` for fresh target health.
Rationale: The feature is a runtime guard around side-effecting action requests, so these existing service boundaries are the narrowest place to add lock, ledger, budget, nested-policy, and target-freshness decisions.
Alternatives considered: Adding workflow-level state was rejected for this slice because no persistent storage is planned and the current action authority service is already the action-decision boundary.
Test implications: Add focused unit tests in `tests/unit/workflows/temporal/test_remediation_context.py`.

## DESIGN-REQ-014 - Mutation Locks

Decision: Missing; add a typed in-service mutation lock model and guard evaluator.
Evidence: `rg` found no remediation-specific lock evaluator or lock result contract; only `lockPolicy` is carried in remediation context.
Rationale: Existing code cannot prove exclusive default `target_execution` mutation behavior, stale lock recovery, idempotent acquisition, or lock-loss denial.
Alternatives considered: Relying on generic Python locks was rejected because the requirement needs auditable target/holder/scope fields and serialized outcomes.
Test implications: Unit tests for exclusive conflict, idempotent reacquisition, stale recovery, and lost-lock denial.

## DESIGN-REQ-015 - Action Ledger Idempotency

Decision: Partial; current duplicate cache should be promoted into an explicit action-ledger guard result.
Evidence: `RemediationActionAuthorityService._decisions` caches decisions by workflow, idempotency key, action kind, and dry-run state; tests cover duplicate shape at the authority layer.
Rationale: The story requires a canonical action ledger surface, not merely an unnamed cache. This slice can model the ledger contract without adding persistent storage.
Alternatives considered: Adding a database table was rejected because the Jira brief and active technology notes specify no new persistent storage for this story.
Test implications: Unit tests for duplicate replay, unsafe idempotency reuse, and serialized ledger fields.

## DESIGN-REQ-016 - Budgets, Cooldowns, And Nested Remediation

Decision: Missing; add budget/cooldown policy checks and nested-remediation defaults.
Evidence: No remediation action count, per-kind attempt, cooldown, or nested-remediation guard exists in the action authority service.
Rationale: Without these checks, automatic remediation can repeat destructive actions or target remediation tasks contrary to the source design.
Alternatives considered: Deferring budgets to a future scheduler was rejected because the acceptance criteria require terminal escalation when exhausted.
Test implications: Unit tests for max action count, max attempts per action kind, cooldown violation, self-target rejection, remediation-to-remediation target rejection, and explicit nested-policy allowance.

## DESIGN-REQ-022 - Target Freshness

Decision: Partial; current target health is re-read but not consumed by action authorization.
Evidence: `RemediationEvidenceToolService.prepare_action_request()` returns pinned/current run IDs, state, title, summary, and `target_run_changed`; tests verify the re-read.
Rationale: The guard service must convert material target changes into no-op, re-diagnosis, or escalation decisions before a side effect can execute.
Alternatives considered: Letting callers inspect `target_run_changed` manually was rejected because enforcement must be centralized and testable.
Test implications: Service-boundary tests should pass a fresh target snapshot into guard evaluation and assert non-mutating target-change outcomes.

## DESIGN-REQ-023 - Bounded Failure

Decision: Partial; bounded evidence/action errors exist, but mutation guard failures need explicit reason codes and serialized outcomes.
Evidence: Existing services return denied decisions or raise `RemediationEvidenceToolError`; no lock, budget, cooldown, nested, or target-change output exists yet.
Rationale: Operators need deterministic no-op, denial, re-diagnosis, or escalation reasons rather than unbounded waiting or fallback raw access.
Alternatives considered: Raising generic errors for every guard failure was rejected because the acceptance criteria require recorded reasons.
Test implications: Unit tests should assert reason codes and redaction-safe `to_dict()` output for each guard family.

## Testing Strategy

Decision: Use unit and service-boundary tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Evidence: Adjacent remediation specs use this test module for async DB-backed remediation link, context, evidence, and action authority behavior.
Rationale: The story does not require external providers or compose-backed services; the highest-risk boundary is the local remediation service API.
Alternatives considered: Full Temporal workflow tests were rejected for this slice because no workflow signature, signal/update, or activity binding changes are planned.
Test implications: Run targeted unit tests during implementation and full `./tools/test_unit.sh` before finalization when feasible.

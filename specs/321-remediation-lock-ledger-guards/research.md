# Research: Remediation Lock, Ledger, and Loop Guards

## Setup Script

Decision: Continue planning from `.specify/feature.json` because the setup helper rejected the managed branch name.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` returned `ERROR: Not on a feature branch. Current branch: run-jira-orchestrate-for-mm-621-add-reme-954b708c`; `.specify/feature.json` points to `specs/321-remediation-lock-ledger-guards`.
Rationale: Managed Jira-orchestrate branches do not follow the helper's numeric branch-name requirement, but the active feature directory is explicit and already contains a valid single-story spec.
Alternatives considered: Renaming or switching branches was rejected because this managed step is limited to planning artifacts and should not mutate branch state.
Test implications: none beyond final verification.

## Agent Context Update

Decision: Agent context update is blocked by the same managed branch/spec-directory mismatch; no manual agent context file edits are planned.
Evidence: `.specify/scripts/bash/update-agent-context.sh` looked for `specs/run-jira-orchestrate-for-mm-621-add-reme-954b708c/plan.md` and failed because the active feature directory is `specs/321-remediation-lock-ledger-guards`.
Rationale: The context updater derives a path from the branch name instead of `.specify/feature.json`; editing agent context by hand would be outside this plan-stage scope and could disturb unrelated managed skill state.
Alternatives considered: Creating a duplicate branch-named spec directory was rejected because it would split MM-621 artifacts and violate the active feature pointer.
Test implications: none beyond final verification.

## FR-001 Through FR-003: Remediation Identity And Exclusive Locks

Decision: implemented_verified; no new implementation planned.
Evidence: `moonmind/workflows/temporal/remediation_actions.py` defines `RemediationMutationGuardService.evaluate()`, `RemediationMutationGuardPolicy`, and `_acquire_lock()`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_mutation_guard_enforces_exclusive_locks_and_recovery` covers acquisition, duplicate holder behavior, conflict, recovery, and lock loss.
Rationale: The current guard requires remediation and target identifiers, defaults to `target_execution` exclusive locks, and returns bounded conflict/loss decisions.
Alternatives considered: Adding a separate lock table was rejected because existing `execution_remediation_links` guard fields already persist the active lock state.
Test implications: final unit verification is sufficient for these requirements.

## FR-004 And FR-007: Target Freshness And Target-Change Guard

Decision: implemented_verified; no new implementation planned.
Evidence: `_freshness_decision()` compares pinned/current run, state, summary, and session identity; `test_remediation_mutation_guard_rejects_nested_and_changed_targets` covers unavailable health, materially changed target state, and policy-controlled `rediagnose`/`escalate` decisions.
Rationale: Side-effecting action evaluation can require target freshness and blocks execution when health is unavailable or materially changed.
Alternatives considered: Binding freshness reads directly inside the guard was rejected for planning because the existing contract accepts a bounded health view from the service/activity boundary, keeping workflow history compact.
Test implications: final unit verification for changed and unavailable target health.

## FR-005 And FR-006: Conflict And Lock Loss Outcomes

Decision: implemented_verified; no new implementation planned.
Evidence: `_acquire_lock()` returns `mutation_lock_conflict`; `release_lock()` persists released lock state; unit tests cover conflict across service restart and released lock loss.
Rationale: The current behavior prevents concurrent mutation and prevents a previous holder from silently continuing after losing the lock.
Alternatives considered: Queueing conflicted mutations was rejected because the source story allows fail-fast/downgrade and does not require a queue.
Test implications: final unit verification.

## FR-008 Through FR-010: Stable Idempotency And Action Ledger

Decision: implemented_verified; no new implementation planned.
Evidence: Guard evaluation denies missing idempotency keys, hashes request shape, rejects unsafe idempotency-key reuse, persists `mutation_guard_ledger_state`, hydrates durable ledger entries, and returns duplicate prior decisions; unit ledger and restart tests cover these paths.
Rationale: The remediation-owned ledger is already the duplicate-suppression surface for logical action requests.
Alternatives considered: Using generic execution update idempotency was rejected because the source design explicitly requires a remediation-owned ledger.
Test implications: final unit verification; hermetic integration confirms guard output can feed action evidence publication.

## FR-011: Retry Budgets And Cooldowns

Decision: implemented_verified; no new implementation planned.
Evidence: `RemediationMutationGuardPolicy` defines `max_actions_per_target`, `max_attempts_per_action_kind`, and `cooldown_seconds`; `_evaluate_budget()` enforces each; `test_remediation_mutation_guard_enforces_ledger_budgets_and_cooldowns` covers cooldown and budget exhaustion.
Rationale: Existing policy inputs and bounded outcomes match the MM-621 acceptance criteria.
Alternatives considered: Making limits global settings was rejected for this story because policy input keeps action evaluation explicit and testable.
Test implications: final unit verification.

## FR-012: Loop Prevention And Nested Remediation

Decision: implemented_verified; no new implementation planned.
Evidence: `_nested_decision()` blocks self-targeting, remediation-on-remediation targeting, and depth violations by default; unit nested-target test covers default denial and explicit allow override.
Rationale: Loop prevention is policy-enforced by default while preserving explicit opt-in for supported nested remediation.
Alternatives considered: Hard-forbidding all nested remediation forever was rejected because the source design allows explicit policy override.
Test implications: final unit verification.

## FR-013: Bounded Operator-Visible Reasons

Decision: implemented_verified; no new implementation planned.
Evidence: `RemediationMutationGuardResult.to_dict()` includes `decision`, `reason`, lock, ledger, budget, nested, freshness, and redacted parameters; tests assert reasons including `mutation_lock_conflict`, `mutation_lock_lost`, `target_materially_changed`, `target_health_unavailable`, `action_budget_exhausted`, `action_cooldown_active`, `self_target_denied`, and `nested_remediation_denied`.
Rationale: Operators and downstream artifact publishers receive compact bounded reasons instead of silent success.
Alternatives considered: Free-form text-only errors were rejected because structured reasons are easier to verify and publish.
Test implications: final unit verification and integration evidence-contract verification.

## FR-014 And Traceability

Decision: implemented_verified; no new implementation planned.
Evidence: `spec.md` preserves MM-621 and the canonical Jira preset brief; generated `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and contract artifacts preserve the issue key and source coverage mapping.
Rationale: Final verification can compare implementation evidence against the original Jira preset brief.
Alternatives considered: Referencing the brief only in an external artifact was rejected because final verification requires `spec.md` traceability.
Test implications: final MoonSpec verification.

## Unit Test Strategy

Decision: Use focused pytest unit coverage through `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` during iteration, then full `./tools/test_unit.sh` before final closure.
Evidence: Existing unit tests cover lock acquisition/conflict/recovery, ledger duplicate and unsafe reuse, budgets/cooldowns, durable lock/ledger hydration, released lock loss, nested remediation, target freshness, and redaction.
Rationale: The story's primary behavior is deterministic service-level guard evaluation and is well suited to hermetic unit tests.
Alternatives considered: Only relying on integration tests was rejected because the guard matrix needs tight scenario coverage.
Test implications: unit tests are the primary validation path.

## Integration Test Strategy

Decision: Use `./tools/test_integration.sh` for required hermetic integration coverage, with focused evidence in `tests/integration/temporal/test_remediation_action_contracts.py`.
Evidence: The integration test exercises context build, authority decision, mutation guard result, action evidence publication, result artifact, and verification artifact. It is marked `integration_ci`.
Rationale: The integration suite verifies the service boundary and artifact contract without requiring external credentials.
Alternatives considered: Provider verification tests were rejected because MM-621 is local control-plane behavior and does not require third-party provider credentials.
Test implications: integration verification remains explicit and credential-free.

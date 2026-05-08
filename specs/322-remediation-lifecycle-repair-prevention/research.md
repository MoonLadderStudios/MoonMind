# Research: Observable Remediation Repair and Prevention Lifecycle

## Setup Helper

Decision: Use `.specify/feature.json` and the active feature directory because `.specify/scripts/bash/setup-plan.sh --json` rejected the managed branch name.
Evidence: The helper returned `ERROR: Not on a feature branch. Current branch: run-jira-orchestrate-for-mm-622-run-obse-0c34c6a4`; `.specify/feature.json` points to `specs/322-remediation-lifecycle-repair-prevention`.
Rationale: The feature directory already exists and passes the specify gate; stopping on branch naming would block valid managed-run planning.
Alternatives considered: Renaming branches was rejected because the managed run owns the branch name.
Test implications: None beyond recording this blocker in planning evidence.

## FR-001, FR-002, DESIGN-REQ-003

Decision: Bounded remediation phase behavior is implemented and verified.
Evidence: `moonmind/workflows/temporal/remediation_context.py` defines `REMEDIATION_PHASES`, `normalize_remediation_phase()`, and `build_remediation_summary_block()`; `tests/unit/workflows/temporal/test_remediation_context.py` verifies known and unknown phase normalization.
Rationale: The current helper exposes remediation-specific phase as subordinate summary state and does not alter top-level execution state.
Alternatives considered: Adding a new top-level task state was rejected by the source design and spec.
Test implications: Rerun focused unit tests and final full unit suite.

## FR-003, DESIGN-REQ-004

Decision: Lifecycle artifact primitives are partial; a unified lifecycle progression/finalization service is still needed.
Evidence: `RemediationLifecyclePublisher.publish_json_artifact()` can publish `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.verification`, and `remediation.summary`; tests prove publication. No higher-level repair/prevention lifecycle finalizer exists.
Rationale: The spec requires observable progression and terminal summary semantics, not only a generic artifact publisher.
Alternatives considered: Treating raw artifact publication as sufficient was rejected because it does not enforce repair/prevention decisions or terminal behavior.
Test implications: Unit tests for lifecycle finalization plus hermetic integration for artifact/read-model boundary.

## FR-004, FR-005, DESIGN-REQ-001, DESIGN-REQ-008

Decision: Safe action guard primitives exist, but the lifecycle-level repair candidate decision is partial.
Evidence: `RemediationEvidenceToolService.prepare_action_request()` rereads target health; `RemediationActionAuthorityService` and `RemediationMutationGuardService` enforce action policy, lock, ledger, freshness, budgets, and raw-action denial. Existing tests cover these primitives.
Rationale: The lifecycle still needs to record the repair candidate considered and prove that attempted actions are the smallest plausible repair rather than an unbounded rerun or destructive mutation.
Alternatives considered: Inferring smallest action from `actionKind` alone was rejected because skipped/denied/escalated branches must be auditable.
Test implications: Add unit tests for attempted, skipped, denied, unsafe, approval-required, and escalated repair decisions.

## FR-006, SC-002

Decision: Action verification artifacts exist, but final repair outcome classification is partial.
Evidence: `RemediationEvidenceToolService.execute_action()` publishes request/result/verification artifacts and integration tests assert those artifacts. The result status is an action result such as `applied`; it does not classify the target outcome as `repaired`, `still_failed`, `not_attempted`, `unsafe`, `approval_required`, or `escalated`.
Rationale: Operators need a target-level repair outcome, not just an action execution status.
Alternatives considered: Reusing action result statuses was rejected because applied actions can still leave the target failed.
Test implications: Add repair outcome classifier tests and integration evidence for published verification/summary refs.

## FR-007, FR-008, SC-003

Decision: Recurrence-prevention output is missing.
Evidence: Searches found no structured prevention output model or recurrence-prevention finalization path in `moonmind/workflows/temporal`.
Rationale: MM-622 explicitly requires recurrence-prevention analysis whether repair succeeds, fails, is skipped, or is unsafe.
Alternatives considered: Leaving prevention to free-form agent prose was rejected because final verification needs deterministic artifacts.
Test implications: Add unit/integration coverage for created reviewable change, findings-only, no reviewable fix, and policy-blocked prevention outputs.

## FR-009, DESIGN-REQ-002

Decision: Decision-log artifact support is partial.
Evidence: `REMEDIATION_ARTIFACT_TYPES` includes `remediation.decision_log`, and publication tests cover the artifact type, but the payload shape is not enforced for repair/prevention decisions.
Rationale: The spec requires decision logs to capture candidates, reasons, action/verification refs, recurrence category, and prevention refs or no-change reasons.
Alternatives considered: Accepting arbitrary JSON was rejected because task generation and verification need stable contract fields.
Test implications: Add schema-like unit assertions and service-boundary artifact tests.

## FR-010, DESIGN-REQ-009

Decision: Corrected-instruction retry provenance is missing.
Evidence: No current code references `execution.retry_failed_step_with_remediation_context` or corrected-instruction retry provenance.
Rationale: The source design forbids silently mutating original task input and requires corrected instructions to be explicit remediation context.
Alternatives considered: Reusing ordinary resume-from-failed-step was rejected because it blurs original input snapshot integrity.
Test implications: Add tests for either a supported corrected-instruction repair context or a deterministic unsupported/escalated decision.

## FR-011, FR-012, SC-004

Decision: Cancellation and terminal finalization are partial.
Evidence: Guard services support lock decisions and release, while `build_remediation_summary_block()` and `RemediationLifecyclePublisher` can publish final summary artifacts. There is no unified terminal path proving no new target mutation after cancellation while attempting lock release and final audit publication.
Rationale: Cancellation safety is an operator-visible lifecycle guarantee, not only a lock primitive.
Alternatives considered: Relying on generic execution cancellation was rejected because remediation has target mutation and lock-release responsibilities.
Test implications: Add unit/integration tests for canceled, escalated, failed, and resolved finalization paths.

## FR-013, FR-014, SC-005

Decision: Continuity helpers are implemented but lifecycle-level continuity remains unverified.
Evidence: `build_remediation_continue_as_new_state()` preserves target identity, context artifact ref, lock identity, action ledger ref, approval state, retry budget state, and live-follow cursor; tests verify sanitization and field preservation. `build_remediation_summary_block()` supports `resultingTargetRunId`.
Rationale: The helper is strong evidence, but the final lifecycle summary must prove these refs are carried through repair/prevention finalization.
Alternatives considered: Marking as fully verified was rejected because the end-to-end lifecycle path does not consume the helper yet.
Test implications: Add lifecycle-specific continuity tests and final summary assertions.

## FR-015, SC-006

Decision: Traceability is implemented.
Evidence: `spec.md`, `plan.md`, this research, and design artifacts preserve MM-622 and the canonical preset brief/source mappings.
Rationale: Downstream tasks, verification, commit, and PR metadata must continue this traceability.
Alternatives considered: None.
Test implications: Final MoonSpec verification should assert MM-622 preservation.

## Test Strategy

Decision: Use focused unit tests first, then hermetic integration tests.
Evidence: Repo instructions require `./tools/test_unit.sh` for unit verification and `./tools/test_integration.sh` for required `integration_ci` coverage; existing remediation tests live in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py`.
Rationale: The feature touches service/activity boundaries and artifact contracts, so unit-only coverage would be insufficient.
Alternatives considered: Provider verification was rejected because this story requires no external credentials.
Test implications: Write failing unit tests before implementation, then integration tests at the remediation service/artifact boundary, then run the full unit suite and hermetic integration suite where available.

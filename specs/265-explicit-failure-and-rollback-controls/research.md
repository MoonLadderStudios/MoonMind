# Research: Explicit Failure and Rollback Controls

## FR-001 / DESIGN-REQ-001 Failure Classification

Decision: Treat failure classification as partial and complete it at the deployment executor/API boundary.
Evidence: `DeploymentUpdateExecutor.execute()` records verification failures and command failures with final `FAILED` or `PARTIALLY_VERIFIED`; `_parse_inputs()` and `DeploymentUpdateLockManager` raise fail-closed `ToolFailure`s; tests cover command failure, non-allowlisted stack, forbidden fields, lock contention, failed verification, and invalid verification status. There is no complete MM-523 matrix for invalid input, authorization, policy, unavailable lock, Compose validation, image pull, service recreation, and verification failure as named failure classes.
Rationale: Operators need consistent failure records, not just exceptions or partial command payloads. The executor is the point that knows lifecycle phase and evidence refs, while the API owns authorization and policy validation.
Alternatives considered: Leave failures as raw exception codes; rejected because the spec requires clear documented failure classes and actionable reasons.
Test implications: Unit matrix for executor/service failure classes and one integration dispatch assertion for failure-class propagation.

## FR-002 / DESIGN-REQ-002 No Automatic Multi-Attempt Retry

Decision: Treat as implemented verified and preserve the behavior.
Evidence: `moonmind/workflows/skills/deployment_tools.py` declares `policies.retries.max_attempts = 1`; `tests/unit/workflows/skills/test_deployment_tool_contracts.py` asserts retry policy; `DeploymentUpdateExecutor` has no internal retry loop.
Rationale: The existing executable tool contract already makes deployment updates single-attempt by default.
Alternatives considered: Add another retry guard in the executor; rejected because it would duplicate the registry policy and create two sources of truth.
Test implications: No new implementation; final verification should include the existing retry-policy test and a traceability check.

## FR-003 Explicit Retry As New Operator Action

Decision: Add verification that re-running after failure is a new audited update submission.
Evidence: `DeploymentOperationsService.queue_update()` creates a new `MoonMind.Run` request for each accepted submission, but there is no test that ties this behavior to post-failure retry semantics.
Rationale: The desired behavior can be satisfied by the existing typed update queue path if tests prove a retry is explicit and separately auditable.
Alternatives considered: Add a special retry endpoint; rejected because the source design says re-running uses the same audited path.
Test implications: Unit/API test with two explicit submissions and distinct idempotency/audit context.

## FR-004 / FR-005 / DESIGN-REQ-003 Rollback Uses Normal Update Path

Decision: Implement rollback as a typed deployment update submission with rollback metadata and a previous image target derived from trusted before-state evidence.
Evidence: Normal update submission already requires admin authorization, reason, policy-valid image, lock, artifacts, and verification. No rollback-specific eligibility, confirmation, or metadata exists in `DeploymentOperationsService`, `DeploymentUpdateRequest`, or `OperationsSettingsSection`.
Rationale: Reusing the existing update path preserves allowlists, audit, artifacts, and verification without creating a second privileged deployment mechanism.
Alternatives considered: Introduce a dedicated rollback executor; rejected because it would duplicate the deployment update safety boundary.
Test implications: Backend tests for rollback request metadata and policy validation; frontend tests for confirmation and request payload; integration test that rollback still dispatches `deployment.update_compose_stack`.

## FR-006 / FR-007 / DESIGN-REQ-004 Rollback Eligibility

Decision: Add a fail-closed rollback eligibility contract to deployment stack state/recent actions.
Evidence: `DeploymentStackStateResponse` currently has no rollback fields, while the frontend schema is permissive and renders recent actions only when provided. No code determines whether before-state artifacts can safely produce a previous image target.
Rationale: The UI can offer rollback only when the backend provides trusted eligibility from prior evidence. Missing, ambiguous, or unsafe evidence must withhold the action.
Alternatives considered: Let the browser infer rollback targets from displayed current image; rejected because the source requires before-state artifact evidence, not UI guesses.
Test implications: Unit tests for eligible, missing, ambiguous, and unsafe evidence; frontend tests that rollback controls appear only for eligible actions.

## FR-008 No Silent Rollback

Decision: Add explicit regression tests around failed execution and failed update projection proving no rollback is enqueued or submitted without operator action.
Evidence: No automatic rollback code path was found in `DeploymentUpdateExecutor`, `DeploymentOperationsService`, or `OperationsSettingsSection`.
Rationale: Absence of a code path should be locked with tests because silent rollback is a high-risk operational behavior.
Alternatives considered: Add a config flag for automatic rollback now; rejected because the spec says automatic rollback requires a separately documented explicit policy.
Test implications: Unit and integration regression tests for failure with no rollback submission.

## FR-009 / DESIGN-REQ-006 Recent Action And Audit Visibility

Decision: Surface failure and rollback records through deployment stack state using existing execution/artifact metadata rather than adding a new table.
Evidence: Executor outputs contain audit and artifact refs; frontend `OperationsSettingsSection` can render `recentActions`; API `_stack_state()` currently returns only placeholder state and no recent action list.
Rationale: Operators need visible recent action records. Existing execution rows and artifact refs are the durable source for the current pre-release design.
Alternatives considered: Add a new deployment action table; rejected because existing execution/artifact records should be sufficient for this bounded story.
Test implications: API tests for recent failure/rollback action fields; frontend tests for visibility of status, reason, timestamps, links, before/after summary, and hidden raw command log unless permitted.

## FR-010 / DESIGN-REQ-005 Scope Boundaries

Decision: Preserve existing allowlisted typed update boundaries for rollback.
Evidence: `DeploymentUpdateRequest` forbids extra fields, service validation rejects non-allowlisted stack/repository/mode, executor rejects forbidden runner/path fields, and existing unit tests cover these denials.
Rationale: Rollback is just another target-image update and must not expand into arbitrary GitOps, Kubernetes, host path, runner image, or shell management.
Alternatives considered: Accept arbitrary previous image strings outside policy for rollback convenience; rejected because it violates the source non-goals.
Test implications: Final verification plus rollback-specific negative tests for non-allowlisted target evidence.

## FR-011 / SC-008 Traceability

Decision: Preserve MM-523 and the canonical Jira preset brief across all feature artifacts and final delivery metadata.
Evidence: `spec.md` and `plan.md` contain MM-523 and the preserved brief. Downstream artifacts are not yet generated.
Rationale: Final verification needs to compare implementation against the original Jira input.
Alternatives considered: Keep only a Jira issue key; rejected because `/speckit.verify` depends on source-request preservation.
Test implications: Traceability grep across specs, code comments only where useful, tests, verification, commit, and PR body.

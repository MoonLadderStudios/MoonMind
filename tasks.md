# Tasks: MM-1103 Compare Branches and Explicitly Promote One Result

## Story

As an operator, I can compare candidate checkpoint branches using durable evidence and explicitly promote one branch result into canonical workflow progress only after validation, gate, side-effect, and approval requirements pass.

## Tasks

- [ ] 1. Add red-first unit tests for branch comparison artifacts and API behavior.
  - [ ] 1.1 In `tests/unit/services/test_checkpoint_branch_service.py`, add failing coverage that compares two candidate branches sharing a valid base checkpoint and expects a durable comparison record with branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and bounded summary refs.
  - [ ] 1.2 In `tests/unit/api/routers/test_checkpoint_branch_apis.py`, add failing coverage for `GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}` returning only bounded metadata and artifact refs, not large diffs, diagnostics bodies, or secret-like values.
  - [ ] 1.3 Assert comparison fails closed when either branch is missing, unauthorized for the workflow, has an invalid checkpoint lineage, or lacks the evidence required to produce artifact refs.

- [ ] 2. Add red-first unit tests for explicit promotion policy gates and audit records.
  - [ ] 2.1 In `tests/unit/services/test_checkpoint_branch_service.py`, add failing coverage that successful promotion records branch id, branch turn id, Step Execution id, accepted output refs, git commit/branch/PR refs, gate verdict refs, side-effect disposition refs, downstream invalidation or revalidation effects, and approval evidence.
  - [ ] 2.2 Add failing coverage that promotion requires caller-provided expected head validation and fresh branch-head validation immediately before canonical acceptance.
  - [ ] 2.3 Add failing coverage that promotion rejects `approval_required`, `side_effect_policy_blocked`, `budget_exhausted`, checkpoint invalidity, missing gate pass, and expected-head mismatch without advancing canonical workflow progress.
  - [ ] 2.4 Add failing coverage that promotion does not delete, archive, or mutate competing branch evidence.
  - [ ] 2.5 Add failing coverage that publication state, including pushed branch or PR refs, remains separate from promoted canonical acceptance.

- [ ] 3. Add integration tests for persisted comparison and promotion behavior.
  - [ ] 3.1 In `tests/integration/api/test_checkpoint_branch_migration.py` or a focused new integration test module, add failing database-backed coverage for persisted comparison artifacts and operation idempotency.
  - [ ] 3.2 Add failing integration coverage for promotion record persistence, downstream invalidation artifact refs, branch state transition to `promoted`, and unchanged competing branches.
  - [ ] 3.3 Add failing integration coverage for fail-closed promotion outcomes persisting audit evidence without canonical advancement.

- [ ] 4. Implement durable comparison record generation.
  - [ ] 4.1 Extend checkpoint branch schemas/models with explicit comparison request/response and persisted artifact metadata using existing artifact-ref patterns.
  - [ ] 4.2 Implement service behavior that validates branch lineage and base checkpoint compatibility, records the compared branch ids and base checkpoint ref, and emits artifact refs for left/right diffs, range diff, diagnostics, gate verdict summaries, and bounded summary metadata.
  - [ ] 4.3 Add the compare API route behavior for `GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}`.
  - [ ] 4.4 Ensure large or sensitive evidence remains behind artifact refs and that artifact refs are treated as identifiers, not storage access grants.

- [ ] 5. Implement explicit promotion with policy gates and audit artifacts.
  - [ ] 5.1 Add promotion request/response schemas that require expected branch head identity and carry approval evidence through the existing policy/audit patterns.
  - [ ] 5.2 Implement service validation for expected head, fresh branch head, passed gates, side-effect policy compliance, applicable approval, budget state, and checkpoint validity.
  - [ ] 5.3 Persist `output.branch_promotion.record.json` and `output.branch_promotion.downstream_invalidation.json` refs with promoted branch, turn, Step Execution, accepted outputs, git/PR refs, gate refs, side-effect refs, invalidation effects, and approval evidence.
  - [ ] 5.4 Add the promote API route behavior for `POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/promote`.
  - [ ] 5.5 Preserve competing branches and keep publication separate from canonical acceptance.

- [ ] 6. Update operator-visible contracts and generated API expectations as needed.
  - [ ] 6.1 Update OpenAPI/schema expectations for compare and promote request/response shapes.
  - [ ] 6.2 Update only durable canonical docs if implementation reveals a verified contract gap; otherwise leave `docs/Workflows/CheckpointBranchSystem.md` unchanged.

- [ ] 7. Run targeted validation.
  - [ ] 7.1 Run the focused unit tests for checkpoint branch service and API router coverage.
  - [ ] 7.2 Run the focused integration tests for checkpoint branch persistence because this story touches persisted API behavior and database-backed artifact evidence.
  - [ ] 7.3 Run OpenAPI or schema contract tests if API schemas changed.
  - [ ] 7.4 Confirm the final implementation satisfies every `spec.md` functional requirement FR-001 through FR-012 and all four acceptance scenarios.

- [ ] 8. Run final MoonSpec verification.
  - [ ] 8.1 Execute `/moonspec-verify` for MM-1103 after implementation and test validation are complete.
  - [ ] 8.2 Address any verification findings before marking the story complete.

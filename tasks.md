# Tasks: MM-1103 Compare Branches and Explicitly Promote One Result

## Story

As an operator, I can compare candidate checkpoint branches using durable evidence and explicitly promote one branch result into canonical workflow progress only after validation, gate, side-effect, and approval requirements pass.

## Tasks

- [ ] 1. Add red-first unit tests for branch comparison artifacts and API behavior.
  - [ ] 1.1 In `tests/unit/api/routers/test_checkpoint_branch_apis.py`, add failing coverage that comparing two candidate branches with a shared valid base checkpoint persists a `checkpoint_branch.compare` operation payload.
  - [ ] 1.2 Assert the compare response includes branch ids, workflow id, base checkpoint ref, left/right diff refs, range diff ref, gate verdict summaries, diagnostics refs, bounded summary ref, and comparison artifact refs.
  - [ ] 1.3 Assert the compare API returns bounded metadata and artifact refs only, without inlining large diffs, diagnostic bodies, provider payloads, or secret-like values.
  - [ ] 1.4 Assert comparison fails closed when either branch is missing, belongs to another workflow, has incompatible checkpoint lineage, or lacks evidence required to produce artifact refs.
  - [ ] 1.5 Assert comparison is idempotent for unchanged branch heads and refreshes comparison refs when branch head or promotion evidence changes.

- [ ] 2. Add red-first unit tests for explicit promotion gates and audit records.
  - [ ] 2.1 In `tests/unit/api/routers/test_checkpoint_branch_apis.py`, add failing coverage that successful promotion records branch id, branch turn id, Step Execution id, accepted output refs, git commit/branch/PR refs, gate verdict refs, side-effect disposition refs, downstream invalidation or revalidation effects, approval evidence, policy evidence, and promotion artifact refs.
  - [ ] 2.2 Assert promotion requires caller-provided expected head Step Execution id and validates expected commit when provided.
  - [ ] 2.3 Assert promotion performs fresh branch-head validation immediately before canonical acceptance.
  - [ ] 2.4 Assert promotion rejects missing approval when approval is required, non-passing gate evidence, unsafe side-effect disposition, budget-exhausted policy evidence, invalid or unverifiable checkpoint/head evidence, stale expected head, and conflicting accepted output refs.
  - [ ] 2.5 Assert failed promotion does not advance canonical workflow progress and persists enough audit evidence for diagnosis where the API contract requires it.
  - [ ] 2.6 Assert promotion does not delete, archive, supersede, or mutate competing branch evidence.
  - [ ] 2.7 Assert publication state, including pushed branch or PR refs, remains separate from promoted canonical acceptance.

- [ ] 3. Add integration tests for persisted comparison and promotion behavior.
  - [ ] 3.1 Add database-backed coverage for persisted comparison operation records, comparison artifact refs, and idempotency across repeated compare requests.
  - [ ] 3.2 Add database-backed coverage for promotion record persistence, `output.branch_promotion.record.json`, `output.branch_promotion.downstream_invalidation.json`, branch state transition to `promoted`, and unchanged competing branches.
  - [ ] 3.3 Add database-backed coverage for fail-closed promotion outcomes preserving audit evidence without canonical advancement.
  - [ ] 3.4 Keep integration coverage hermetic and local-dependency-only; do not require provider credentials.

- [ ] 4. Implement durable comparison record generation.
  - [ ] 4.1 Extend checkpoint branch schemas/models as needed for explicit comparison response and persisted artifact metadata using existing operation-ledger and artifact-ref patterns.
  - [ ] 4.2 Validate branch ownership, lineage, and base checkpoint compatibility before producing comparison evidence.
  - [ ] 4.3 Record compared branch ids, workflow id, base checkpoint ref, branch head identities, gate verdict summaries, diagnostics refs, bounded summary metadata, and artifact refs.
  - [ ] 4.4 Emit or persist refs for `output.branch_comparison.summary.json`, `output.branch_comparison.metadata.json`, and `output.branch_comparison.range_diff.patch`.
  - [ ] 4.5 Implement or complete `GET /api/executions/{workflowId}/checkpoint-branches/{branchId}/compare?against={otherBranchId}` behavior.
  - [ ] 4.6 Ensure large or sensitive evidence remains behind artifact refs and that artifact refs are treated as identifiers, not storage access grants.

- [ ] 5. Implement explicit promotion with policy gates and audit artifacts.
  - [ ] 5.1 Add or complete promotion request/response schemas requiring expected branch head identity and carrying accepted output refs, gate evidence, side-effect disposition, approval evidence, policy evidence, downstream invalidation, and idempotency key.
  - [ ] 5.2 Validate expected head Step Execution id, expected commit when provided, accepted output refs, current branch head, passed gates, side-effect policy compliance, approval requirements, budget state, and checkpoint/head validity before canonical acceptance.
  - [ ] 5.3 Fail closed for `approval_required`, `side_effect_policy_blocked`, `budget_exhausted`, checkpoint invalidity, unverifiable head evidence, missing gate pass, expected-head mismatch, and conflicting accepted output refs.
  - [ ] 5.4 Persist promotion evidence with promoted branch, branch turn, Step Execution, accepted outputs, git/PR refs, gate refs, side-effect refs, invalidation effects, approval evidence, policy evidence, and artifact refs.
  - [ ] 5.5 Persist `output.branch_promotion.record.json` and `output.branch_promotion.downstream_invalidation.json` refs.
  - [ ] 5.6 Implement or complete `POST /api/executions/{workflowId}/checkpoint-branches/{branchId}/promote` behavior.
  - [ ] 5.7 Preserve competing branches and keep publication separate from canonical acceptance.

- [ ] 6. Update operator-visible contracts and generated API expectations as needed.
  - [ ] 6.1 Update `contracts/checkpoint-branch-compare-promote.openapi.yaml` if request/response shapes change during implementation.
  - [ ] 6.2 Update schema or OpenAPI snapshot expectations for compare and promote behavior.
  - [ ] 6.3 Update durable canonical docs only if implementation reveals a verified target-state contract gap; otherwise leave `docs/Workflows/CheckpointBranchSystem.md` unchanged.

- [ ] 7. Run story validation.
  - [ ] 7.1 Run focused unit tests for checkpoint branch API coverage: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_checkpoint_branch_apis.py`.
  - [ ] 7.2 Run focused integration tests for checkpoint branch persistence because this story touches persisted API behavior and database-backed artifact evidence.
  - [ ] 7.3 Run OpenAPI or schema contract tests if API schemas changed.
  - [ ] 7.4 Confirm the implementation satisfies `spec.md` FR-001 through FR-012.
  - [ ] 7.5 Confirm the implementation satisfies all four `spec.md` acceptance scenarios.

- [ ] 8. Run final MoonSpec verification.
  - [ ] 8.1 Execute `/moonspec-verify` for MM-1103 after implementation and story validation are complete.
  - [ ] 8.2 Address any verification findings before marking the story complete.

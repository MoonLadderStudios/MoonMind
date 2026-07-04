# Implementation Plan: MM-1103 Branch Compare and Promotion

**Jira Issue:** MM-1103  
**Spec:** `spec.md`  
**Source Design:** `docs/Workflows/CheckpointBranchSystem.md`  
**Status:** Planning artifact  

## Summary

Implement and verify durable checkpoint-branch comparison and explicit promotion behavior. The work is centered on the existing checkpoint branch API surface in `api_service/api/routers/executions.py`, schema models in `moonmind/schemas/checkpoint_branch_models.py`, persistence models in `api_service/db/models.py`, and API-focused tests in `tests/unit/api/routers/test_checkpoint_branch_apis.py`.

The repository already contains compare and promote endpoints. This plan treats the implementation as partially present and focuses the remaining work on contract completeness, fail-closed behavior, artifact evidence shape, and targeted verification.

## Technical Context

- Product branch and git branch identities remain distinct.
- Comparison is evidence-backed and bounded; large diffs and diagnostics remain behind artifact refs.
- Promotion is an explicit canonical acceptance operation, separate from publication.
- Promotion requires expected head validation, passing gate evidence, safe side-effect disposition, approval evidence when required, and fresh branch-head validation.
- Promotion must also fail closed when policy evidence reports budget exhaustion or when checkpoint/head validity cannot be proven.
- Promotion must not delete or mutate competing branch evidence.

## Scope

In scope:

- Branch comparison response and durable operation record.
- Comparison artifact refs for summary, metadata, and range diff.
- Promotion request validation and durable promotion evidence.
- Promotion artifact refs for promotion record and downstream invalidation.
- Fail-closed handling for stale heads, missing approval, blocked side effects, failed gates, missing checkpoint head, and conflicting accepted output refs.
- Unit tests for API behavior, persistence evidence, and idempotency.
- Integration test strategy for database-backed API and artifact-ref boundary coverage.

Out of scope:

- Branch create, continue, fork, archive, and publish behavior except where compare/promote consumes their evidence.
- Rich UI comparison views.
- Multi-branch merge behavior.
- Provider-specific continuation semantics.

## Data Model Impact

Use the existing checkpoint branch tables and schemas:

- `workflow_checkpoint_branches`
- `workflow_checkpoint_branch_turns`
- `workflow_checkpoint_branch_artifacts`
- `workflow_checkpoint_branch_operations`
- `CheckpointBranchCompareResponse`
- `CheckpointBranchPromoteRequest`

No new table is expected for this story unless verification shows the current operation/artifact records cannot preserve required evidence.

## API Contract Impact

Affected endpoints:

- `GET /api/executions/{workflow_id}/checkpoint-branches/{branch_id}/compare?against={against_branch_id}`
- `POST /api/executions/{workflow_id}/checkpoint-branches/{branch_id}/promote`

The API contract is captured in `contracts/checkpoint-branch-compare-promote.openapi.yaml`.

## Implementation Steps

1. Confirm compare output contains branch ids, workflow id, base checkpoint evidence, diff refs, gate verdict summaries, diagnostics refs, bounded summary ref, and operation artifact refs.
2. Confirm compare operation is idempotent for unchanged branch heads and refreshes when branch head or promotion evidence changes.
3. Confirm promotion validates expected head Step Execution id and expected commit when provided.
4. Confirm promotion validates accepted output refs against the current branch head.
5. Confirm promotion requires passing gate evidence and safe side-effect disposition.
6. Confirm promotion requires approval evidence when policy requires approval.
7. Confirm promotion rejects budget-exhausted policy evidence before canonical acceptance.
8. Confirm promotion rejects invalid or unverifiable checkpoint/head evidence before canonical acceptance.
9. Confirm promotion records branch id, branch turn id, Step Execution id, accepted output refs, git/PR refs, gate refs, side-effect disposition, downstream invalidation, approval evidence, policy evidence, and artifact refs.
10. Confirm promotion does not delete, archive, or supersede competing branches.
11. Confirm publication state remains separate from promotion state.

## Unit Test Strategy

Targeted unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_checkpoint_branch_apis.py
```

Required unit coverage:

- Compare persists `checkpoint_branch.compare` operation payload and artifact refs.
- Compare returns the same summary ref for unchanged heads.
- Compare returns a new summary ref when branch head or promotion evidence changes.
- Promotion rejects missing approval when `policyRequiresApproval=true`.
- Promotion rejects non-passing gate evidence.
- Promotion rejects unsafe side-effect disposition.
- Promotion rejects budget-exhausted policy evidence.
- Promotion rejects invalid or unverifiable checkpoint/head evidence.
- Promotion rejects stale expected head Step Execution id.
- Promotion rejects conflicting accepted output refs.
- Promotion writes promotion evidence and artifact refs.
- Promotion leaves sibling/competing branch records intact.

## Integration Test Strategy

Run hermetic integration only if implementation changes cross database migrations, compose-backed services, artifact storage integration, or runtime infrastructure:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_integration.sh
```

Integration coverage should verify the same API boundary with a real database session and artifact-ref persistence, but it should not require external provider credentials. Provider verification tests are not required for this story.

## Risks

- Compare currently synthesizes artifact refs; if later consumers require physical artifact bodies, an artifact writer boundary may be needed.
- Promotion freshness is represented by current database head validation; workflow-bound promotion may need an additional activity-level revalidation if later moved into Temporal workflow code.
- Fail-closed reason codes must stay aligned with `docs/Workflows/CheckpointBranchSystem.md` so operators can diagnose blocked promotion safely.

## Acceptance Mapping

| Acceptance Criterion | Plan Coverage |
| --- | --- |
| Comparison artifacts contain branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and bounded summary refs. | Steps 1-2; unit compare tests; API contract. |
| Promotion records branch id, turn id, Step Execution id, accepted output refs, git/PR refs, gate verdict refs, side-effect disposition refs, invalidation effects, and approval evidence. | Steps 3-9; promotion evidence tests; data model. |
| Promotion requires expected head validation, passed gates, applicable approval, and fresh branch-head validation. | Steps 3-8; fail-closed tests. |
| Promotion does not delete competing branches and publication remains separate from canonical acceptance. | Steps 10-11; sibling branch test; contract fields keep `publishStatus` separate. |

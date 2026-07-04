# Quickstart: MM-1103 Branch Compare and Promotion

## Verify Planning Artifacts

```bash
test -f spec.md
test -f plan.md
test -f research.md
test -f quickstart.md
test -f data-model.md
test -f contracts/checkpoint-branch-compare-promote.openapi.yaml
```

## Run Targeted Unit Tests

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_checkpoint_branch_apis.py
```

## Exercise Compare API Shape

Use the existing checkpoint branch API test fixtures or a local authenticated API session to create two branches from the same checkpoint, then call:

```http
GET /api/executions/{workflow_id}/checkpoint-branches/{branch_id}/compare?against={against_branch_id}
```

Expected response characteristics:

- `branchId` and `againstBranchId` identify the compared branches.
- `summaryRef` points to bounded comparison summary evidence.
- `comparisonRecord.recordType` is `checkpoint_branch_comparison`.
- `comparisonRecord.git.leftDiffRef`, `rightDiffRef`, and `rangeDiffRef` are artifact refs.
- `comparisonRecord.quality` contains gate verdict summaries.

## Exercise Promotion API Shape

Call:

```http
POST /api/executions/{workflow_id}/checkpoint-branches/{branch_id}/promote
Content-Type: application/json

{
  "expectedHeadStepExecutionId": "step-execution-id",
  "expectedHeadCommit": "optional-head-commit",
  "acceptedOutputRefs": {
    "headStepExecutionId": "step-execution-id"
  },
  "gateEvidence": {
    "verdict": "passed",
    "artifactRef": "artifact://gate"
  },
  "sideEffectDisposition": {
    "status": "isolated"
  },
  "approvalEvidence": {
    "artifactRef": "artifact://approval"
  },
  "policyRequiresApproval": true,
  "idempotencyKey": "unique-promotion-key"
}
```

Expected behavior:

- Valid promotion returns the branch with `state` set to `promoted`.
- Promotion evidence records output refs, git/PR refs, gate evidence, side-effect disposition, downstream invalidation, approval evidence, and artifact refs.
- Stale expected head, missing required approval, failed gates, unsafe side effects, budget-exhausted policy evidence, invalid or unverifiable checkpoint/head evidence, and conflicting accepted output refs return `409`.

## Integration Test Guidance

Run hermetic integration only when code changes affect database migrations, artifact storage, compose/runtime infrastructure, or cross-service boundaries:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_integration.sh
```

Provider verification is not required for this story.

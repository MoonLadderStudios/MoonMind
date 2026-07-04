# Data Model: MM-1103 Branch Compare and Promotion

## Existing Entities

### WorkflowCheckpointBranch

Represents a product-level checkpoint branch.

Relevant fields:

- `branch_id`
- `workflow_id`
- `source_checkpoint_ref`
- `source_checkpoint_digest`
- `state`
- `current_head_step_execution_id`
- `current_head_checkpoint_ref`
- `current_head_commit`
- `git_repository`
- `git_base_branch`
- `git_base_commit`
- `git_work_branch`
- `pull_request_url`
- `publish_status`
- `promotion_evidence`
- `promoted_at`

Promotion updates `state`, `promotion_evidence`, and `promoted_at`. It must not delete competing branches.

### WorkflowCheckpointBranchTurn

Represents one branch-local instruction turn that may produce Step Execution evidence.

Relevant fields:

- `branch_turn_id`
- `branch_id`
- `created_step_execution_id`
- `step_execution_manifest_ref`
- `instruction_ref`
- `instruction_digest`
- `source_checkpoint_ref`
- `source_checkpoint_digest`

Promotion uses the latest turn matching the promoted head Step Execution id to record branch turn and manifest evidence.

### WorkflowCheckpointBranchOperation

Represents durable records for branch operations.

Relevant fields:

- `workflow_id`
- `branch_id`
- `branch_turn_id`
- `operation`
- `idempotency_key`
- `request_digest`
- `response_payload`

Compare records use `operation = checkpoint_branch.compare`. Promotion records use `operation = checkpoint_branch.promote`.

### WorkflowCheckpointBranchArtifact

Represents artifact refs associated with branch operations and evidence.

Relevant comparison artifact kinds:

- `output.branch_comparison.summary.json`
- `output.branch_comparison.metadata.json`
- `output.branch_comparison.range_diff.patch`

Relevant promotion artifact kinds:

- `output.branch_promotion.record.json`
- `output.branch_promotion.downstream_invalidation.json`

## Compare Record Shape

Required fields:

- `schemaVersion`
- `recordType = checkpoint_branch_comparison`
- `workflowId`
- `branchId`
- `againstBranchId`
- `summaryText`
- `summary`
- `quality`
- `git.leftDiffRef`
- `git.rightDiffRef`
- `git.rangeDiffRef`
- `evidenceRefs.baseCheckpointRef`
- `diagnosticsRefs`
- `summaryRef`
- `artifactRefs`
- `digest`

Bounded summary fields are API-visible. Large diffs and diagnostics stay behind refs.

## Promotion Record Shape

Required fields:

- `schemaVersion`
- `recordType = checkpoint_branch_promotion`
- `workflowId`
- `branchId`
- `branchTurnId`
- `stepExecutionId`
- `acceptedOutputRefs`
- `gitEvidence`
- `gateEvidence`
- `sideEffectDisposition`
- `downstreamInvalidation`
- `approvalEvidence`
- `policyEvidence`
- `promotedAt`
- `promotionRecordRef`
- `downstreamInvalidationRef`
- `artifactRefs`
- `digest`

## Validation Rules

- `expectedHeadStepExecutionId` must match `current_head_step_execution_id`.
- `expectedHeadCommit`, when provided, must match `current_head_commit`.
- `acceptedOutputRefs.headStepExecutionId`, when provided, must match `current_head_step_execution_id`.
- `acceptedOutputRefs.headCheckpointRef`, when provided, must match `current_head_checkpoint_ref`.
- Gate evidence must contain a passing verdict/status.
- Side-effect disposition must be in the safe promotion set.
- Approval evidence is required when `policyRequiresApproval=true`.
- Archived or superseded branches cannot be promoted.
- A competing promoted sibling from the same source checkpoint blocks promotion.

## State Transitions

Promotion:

```text
active|succeeded|promotable -> promoted
```

Invalid promotion inputs leave branch state unchanged.

Comparison:

```text
no branch state transition
```


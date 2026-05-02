# Feature Specification: Merge Automation Merged PR Recovery

**Feature Branch**: `293-merge-automation-merged-pr-recovery`  
**Created**: 2026-05-02  
**Status**: Draft  
**Input**: Make merge automation resilient when a PR is merged but the resolver child returns an incomplete merge disposition contract.

## User Scenarios & Testing

### Primary User Story

As an operator running PR merge automation, I need the workflow to move on successfully when GitHub authoritatively reports that the target PR is already merged, even if the resolver child returned an incomplete or invalid merge automation disposition.

### Acceptance Scenarios

1. **Given** `pr-resolver` returns success without `mergeAutomationDisposition`, **when** a fresh merge automation readiness check reports `pullRequestMerged=true`, **then** merge automation completes successfully as `already_merged`.
2. **Given** `pr-resolver` returns an unsupported disposition, **when** GitHub does not report the PR as merged, **then** merge automation keeps the deterministic `resolver_disposition_invalid` failure.
3. **Given** the resolver child reports failure or manual review, **when** a fresh readiness check reports `pullRequestMerged=true`, **then** merge automation completes successfully after required post-merge Jira completion.
4. **Given** resolver result artifacts are written by `pr-resolver`, **when** the adapter fetches the child run result, **then** explicit `mergeAutomationDisposition` remains propagated to `MoonMind.Run` output metadata.

## Requirements

- **FR-001**: Merge automation MUST perform one fresh authoritative readiness evaluation before failing a resolver child for missing, unsupported, manual-review, or failed merge disposition.
- **FR-002**: Merge automation MUST only recover to success when the fresh readiness evidence reports `pullRequestMerged=true`.
- **FR-003**: Recovery MUST run the same required post-merge Jira completion path used by normal `merged` and `already_merged` resolver dispositions.
- **FR-004**: Recovery MUST NOT infer merge success from resolver free-form output text.
- **FR-005**: If the fresh readiness evidence does not confirm the PR is merged, missing and unsupported dispositions MUST continue to fail deterministically.
- **FR-006**: Resolver producer and adapter boundaries MUST continue to emit and propagate explicit `mergeAutomationDisposition` values for normal terminal results.

## Success Criteria

- **SC-001**: Workflow tests prove missing resolver dispositions recover when the second readiness check observes the PR as merged.
- **SC-002**: Workflow tests prove invalid dispositions still fail when GitHub does not confirm a merge.
- **SC-003**: Existing adapter and resolver tests continue to prove explicit disposition propagation.

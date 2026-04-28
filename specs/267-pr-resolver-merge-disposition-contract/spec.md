# Feature Specification: PR Resolver Merge Automation Disposition Contract

**Feature Branch**: `267-pr-resolver-merge-disposition-contract`  
**Created**: 2026-04-28  
**Status**: Draft  
**Input**: Repair merge automation false failures when `pr-resolver` completes without a machine-readable merge disposition.

## User Scenarios & Testing

### Primary User Story

As an operator running PR merge automation, I need a `pr-resolver` child run to publish an explicit terminal merge disposition so the parent `MoonMind.MergeAutomation` workflow can complete deterministically after the PR is merged or fail with a precise resolver blocker when it is not.

### Acceptance Scenarios

1. **Given** `pr-resolver` merges a PR, **when** its terminal result is written, **then** the result includes `mergeAutomationDisposition="merged"` and merge automation accepts the child result.
2. **Given** `pr-resolver` observes an already-merged PR, **when** its terminal result is written, **then** the result includes `mergeAutomationDisposition="already_merged"`.
3. **Given** a resolver run is expected by merge automation but no resolver result artifact exists, **when** the adapter fetches the child result, **then** the child result is classified as failed instead of generic success.
4. **Given** a resolver terminal blocker or exhausted retry budget, **when** its result is written, **then** the result includes `mergeAutomationDisposition="manual_review"`.
5. **Given** a resolver hard failure, **when** its result is written, **then** the result includes `mergeAutomationDisposition="failed"`.

## Requirements

- **FR-001**: `var/pr_resolver/result.json` and compatible resolver result artifacts MUST include `mergeAutomationDisposition` for terminal resolver outcomes.
- **FR-002**: The resolver result schema MUST declare allowed disposition values: `merged`, `already_merged`, `reenter_gate`, `manual_review`, and `failed`.
- **FR-003**: Managed runtime adapters MUST pass an explicit `mergeAutomationDisposition` from resolver artifacts through to `MoonMind.Run` output metadata.
- **FR-004**: When `pr_resolver_expected` is true and no resolver result artifact can be loaded, adapters MUST classify the child result as a resolver failure instead of returning generic success.
- **FR-005**: Existing merge automation validation MUST remain fail-fast for missing or unsupported dispositions at the workflow boundary.

## Success Criteria

- **SC-001**: Unit tests prove resolver writers emit dispositions for merged, already-merged, blocked, exhausted, and failed terminal results.
- **SC-002**: Adapter tests prove explicit dispositions are propagated and missing expected resolver artifacts fail.
- **SC-003**: Workflow-boundary tests prove `MoonMind.Run` preserves disposition metadata and `MoonMind.MergeAutomation` succeeds when the resolver returns `merged`.

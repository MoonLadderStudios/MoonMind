# Implementation Plan: Merge Automation Merged PR Recovery

## Summary

Add a narrow post-resolver recovery path in `MoonMind.MergeAutomation`: before turning a malformed or non-success resolver disposition into a failed merge automation result, re-run the existing readiness activity and accept success only if GitHub reports the PR is merged. Keep the resolver output contract strict for all non-merged evidence.

## Technical Context

- Python 3.12
- Temporal Python SDK workflows and activities
- Existing GitHub readiness activity: `merge_automation.evaluate_readiness`
- Existing post-merge Jira activity: `merge_automation.complete_post_merge_jira`
- Existing pr-resolver result writers and managed runtime adapters

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Recovery uses existing provider readiness activity.
- II. One-Click Agent Deployment: PASS. No new dependencies or services.
- III. Avoid Vendor Lock-In: PASS. GitHub-specific evidence remains inside the existing GitHub readiness boundary.
- IV. Own Your Data: PASS. Recovery evidence is captured in existing local artifacts and workflow state.
- V. Skills Are First-Class and Easy to Add: PASS. The resolver skill contract remains explicit.
- VI. Thin Scaffolding, Thick Contracts: PASS. The workflow does not parse agent prose; it checks provider state.
- VII. Runtime Configurability: PASS. No new required operator config.
- VIII. Modular and Extensible Architecture: PASS. Edits stay within merge automation and existing adapter tests.
- IX. Resilient by Default: PASS. A completed external merge no longer produces a false failed task.
- X. Facilitate Continuous Improvement: PASS. Failure summaries remain deterministic when recovery is not justified.
- XI. Spec-Driven Development: PASS. This spec defines the behavior change.
- XII. Canonical Documentation: PASS. Migration detail stays in this feature artifact.
- XIII. Pre-Release Compatibility Policy: PASS. A Temporal patch marker guards the new workflow branch for replay safety.

## Implementation Strategy

1. Extract the existing readiness activity call into a workflow helper without changing command ordering.
2. Add a patched recovery helper that re-evaluates readiness before resolver-disposition failures.
3. If the fresh evidence reports the PR merged, refresh tracked PR metadata, run post-merge Jira completion, and finish as `already_merged`.
4. Add workflow tests for missing disposition recovery and continued invalid-disposition failure.
5. Run focused tests, then the full unit suite.

## Test Strategy

- `pytest tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py`
- `pytest tests/unit/test_pr_resolver_tools.py`
- `pytest tests/unit/workflows/adapters/test_managed_agent_adapter.py`
- `pytest tests/unit/workflows/adapters/test_codex_session_adapter.py`
- `./tools/test_unit.sh`

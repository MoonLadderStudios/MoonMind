# Implementation Plan: PR Resolver Merge Automation Disposition Contract

## Summary

Make the `pr-resolver` machine-readable terminal result the authoritative source for merge automation disposition. Keep `MoonMind.MergeAutomation` strict: it should continue to reject missing or unsupported dispositions. Fix the producer and adapter boundary so successful resolver children no longer collapse into generic `{"status":"success"}`.

## Technical Context

- Python 3.12
- Pydantic v2 runtime models
- Existing Temporal `MoonMind.Run`, `MoonMind.AgentRun`, and `MoonMind.MergeAutomation` workflows
- Existing `.agents/skills/pr-resolver` scripts and schemas
- Existing pytest unit and workflow-boundary test suites

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The resolver remains a skill executed through existing runtime adapters.
- II. One-Click Agent Deployment: PASS. No new deployment dependencies.
- III. Avoid Vendor Lock-In: PASS. GitHub-specific resolver behavior remains isolated in the existing PR resolver skill boundary.
- IV. Own Your Data: PASS. Results remain local JSON artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. The skill declares a stronger output contract.
- VI. Thin Scaffolding, Thick Contracts: PASS. The change strengthens the contract rather than inference.
- VII. Runtime Configurability: PASS. No hardcoded operator config changes.
- VIII. Modular and Extensible Architecture: PASS. Edits stay in resolver scripts, adapters, and workflow-boundary tests.
- IX. Resilient by Default: PASS. Missing resolver artifacts fail explicitly and merged PR reverify can still recover stale failures.
- X. Facilitate Continuous Improvement: PASS. Terminal result summaries become more actionable.
- XI. Spec-Driven Development: PASS. This spec/plan/tasks set defines the change.
- XII. Canonical Documentation: PASS. Long-lived docs describe desired resolver contract; rollout details stay in this spec.
- XIII. Pre-Release Compatibility Policy: PASS. The new resolver contract is explicit; unsupported values continue to fail at the merge automation boundary.

## Implementation Strategy

1. Add shared helper logic in `pr_resolve_contract.py` to derive merge automation disposition from resolver terminal status, merge outcome, and final reason.
2. Update `pr_resolve_finalize.py`, `pr_resolve_orchestrate.py`, and `pr_resolve_full.py` to write `mergeAutomationDisposition`.
3. Update `pr_resolver_result.schema.json`, `SKILL.md`, and the PR resolver docs.
4. Update managed adapter metadata extraction to prefer explicit disposition values and fail expected resolver runs with missing result artifacts.
5. Add focused unit tests for result writers, adapter extraction/failure classification, and `MoonMind.Run` metadata propagation.

## Test Strategy

- Run focused pytest targets for:
  - `tests/unit/test_pr_resolver_tools.py`
  - `tests/unit/workflows/adapters/test_managed_agent_adapter.py`
  - `tests/unit/workflows/adapters/test_codex_session_adapter.py`
  - `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py`
  - `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py`
- Run `./tools/test_unit.sh` before finalizing.

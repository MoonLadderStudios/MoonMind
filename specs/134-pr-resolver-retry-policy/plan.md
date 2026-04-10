# Implementation Plan: pr-resolver-retry-policy

**Branch**: `codex/phase-8-managed-session-artifacts` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/134-pr-resolver-retry-policy/spec.md`

## Summary

Align the `pr-resolver` orchestration retry-state machine with its documented behavior by allowing transitions from transient finalize-only retry states into actionable merge-conflict remediation. Add regression coverage so future retry-policy changes do not silently reintroduce the manual-review stop.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: `pr_resolve_orchestrate.py`, `pr_resolve_contract.py`, `tests/unit/test_pr_resolver_tools.py`
**Testing**: focused pytest coverage for pr-resolver tooling
**Project Type**: agent skill orchestration script and unit tests
**Constraints**: preserve bounded retry/manual-review safeguards for unknown reason transitions; keep remediation routing delegated through existing specialized skills

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change only corrects which existing specialized skill path the orchestrator chooses.
- **V. Skills Are First-Class and Easy to Add**: PASS. The fix keeps remediation routed through the declared `fix-merge-conflicts` skill instead of ad hoc behavior.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The retry-policy contract is made explicit with a regression test instead of hidden in ad hoc state transitions.
- **IX. Resilient by Default**: PASS. Actionable merge-conflict blockers no longer stop unnecessarily after transient CI waits.
- **XI. Spec-Driven Development**: PASS. This change is tracked with spec/plan/tasks before implementation.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The live transition logic is corrected directly without compatibility shims.

## Research

- `docs/ManagedAgents/SkillGithubPrResolver.md` already defines merge conflicts as higher priority than CI waiting and says `DIRTY` should delegate to `fix-merge-conflicts` before any wait path.
- The current orchestration code only special-cases transitions into `ci_failures`, causing `ci_running -> merge_conflicts` to fall into the generic manual-review stop.
- Existing unit tests already cover `ci_running` waits and `ci_failures` escalation, so the new regression fits naturally into `tests/unit/test_pr_resolver_tools.py`.

## Implementation Plan

1. Add a regression test for `ci_running -> merge_conflicts -> merged`.
2. Update the orchestration transition guard so actionable remediation reasons reached after finalize-only retry states continue into full remediation.
3. Run focused pr-resolver unit tests to verify the new escalation and existing retry behavior.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/test_pr_resolver_tools.py`

### Manual Validation

1. Re-run `pr-resolve_orchestrate.py` against a PR that transitions from running checks to `DIRTY` mergeability and confirm the result directs `run_fix_merge_conflicts_skill` instead of `manual_review`.

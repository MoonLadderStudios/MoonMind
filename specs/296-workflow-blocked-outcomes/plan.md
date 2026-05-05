# Implementation Plan: Workflow Blocked Outcomes

**Branch**: `296-workflow-blocked-outcomes` | **Date**: 2026-05-04 | **Spec**: [spec.md](./spec.md)

## Summary

Add a replay-stable workflow helper that recognizes explicit structured blocked outcomes in step outputs. On detection, the parent `MoonMind.Run` workflow records the current step, skips remaining plan steps, suppresses publish handling, and completes with a blocked result message.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, pytest  
**Storage**: Existing workflow history, memo, search attributes, and step ledger only  
**Tests**: `./tools/test_unit.sh tests/unit/workflows/temporal/test_run_artifacts.py`

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing workflow/agent contracts.
- II. One-Click Agent Deployment: PASS. No deployment dependencies added.
- III. Avoid Vendor Lock-In: PASS. Detection is generic structured output parsing.
- IV. Own Your Data: PASS. Uses local workflow outputs only.
- V. Skills Are First-Class: PASS. Works for agent and tool steps.
- VI. Design for Deletion / Thick Contracts: PASS. Compact status contract, no prompt-specific branching.
- VII. Runtime Configurability: PASS. No hidden external calls or hardcoded repo data.
- VIII. Modular Architecture: PASS. Changes stay in `MoonMind.Run` workflow helpers.
- IX. Resilient by Default: PASS. Prevents misleading publish failures and unnecessary downstream work.
- X. Continuous Improvement: PASS. Final output reports the actionable blocker.
- XI. Spec-Driven Development: PASS. This artifact records the behavior.
- XII. Canonical Docs Separation: PASS. No canonical docs changed.
- XIII. Pre-Release Compatibility: PASS. No compatibility aliases; existing behavior remains for non-blocked no-change publishes.

## Implementation Strategy

1. Add blocked outcome parsing for structured mappings and JSON summaries.
2. Stop remaining plan execution and mark downstream steps skipped.
3. Suppress publish handling for blocked outcomes.
4. Add unit coverage for parsing, final publish outcome, and execution-stage stop behavior.

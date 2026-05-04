# Implementation Plan: Jira Blocker Direction

**Branch**: `297-jira-blocker-direction` | **Date**: 2026-05-04 | **Spec**: [spec.md](./spec.md)

## Summary

Keep Jira dependency creation aligned with the existing `linear_blocker_chain`
contract, and replace Jira Orchestrate's prompt-only blocker decision with a
first-party executable tool that parses Jira `Blocks` link direction.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal tool dispatcher, pytest  
**Storage**: Existing workflow outputs only  
**Tests**: Focused story-output, template expansion, and startup seed tests.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing trusted Jira service.
- II. One-Click Agent Deployment: PASS. No new services or secrets.
- III. Avoid Vendor Lock-In: PASS. Jira-specific logic stays behind the Jira tool boundary.
- IV. Own Your Data: PASS. Uses trusted local integration responses.
- V. Skills Are First-Class: PASS. Adds a deterministic executable tool.
- VI. Thick Contracts: PASS. Tool output is compact structured JSON.
- VII. Runtime Configurability: PASS. Link type remains an input with `Blocks` default.
- VIII. Modular Architecture: PASS. Parser and handler stay in first-party tool code.
- IX. Resilient by Default: PASS. Fails closed only when trusted blocker status is unavailable.
- X. Continuous Improvement: PASS. Blocked summaries explain the exact Jira issue.
- XI. Spec-Driven Development: PASS. This artifact records the fix.
- XII. Canonical Docs Separation: PASS. No canonical docs changed.
- XIII. Pre-Release Compatibility: PASS. Updates internal preset/tool contracts directly.

## Implementation Strategy

1. Add deterministic Jira blocker-preflight parsing for `Blocks` link direction.
2. Register the preflight as a first-party `mm.tool.execute` handler.
3. Update Jira Orchestrate to use the deterministic tool step.
4. Add regressions for outward-link continuation, inward-link blocking, status fetch, and preset seeding.

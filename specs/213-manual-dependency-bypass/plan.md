# Implementation Plan: Manual Dependency Wait Bypass

**Branch**: `213-manual-dependency-bypass` | **Date**: 2026-04-20 | **Spec**: `specs/213-manual-dependency-bypass/spec.md`

## Summary

Add an operator-confirmed `BypassDependencies` signal for `MoonMind.Run`, expose a matching action capability from the execution detail API only while a run is blocked on dependencies, and render the control in the task detail Dependencies panel.

## Technical Context

**Language/Version**: Python 3.12, TypeScript/React  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, FastAPI, React, Vitest, pytest  
**Storage**: Existing workflow memo/search attributes and execution projection only; no new tables.  
**Testing**: Focused workflow unit tests, Temporal service tests, execution API serialization tests, and task-detail Vitest coverage.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Adds a Temporal signal to existing orchestration.
- II. One-Click Agent Deployment: PASS. No new deployment dependency.
- III. Avoid Vendor Lock-In: PASS. Workflow-level behavior is provider-neutral.
- IV. Own Your Data: PASS. Bypass metadata stays in local workflow state/projections.
- V. Skills Are First-Class: PASS. No skill contract changes.
- VI. Replaceable Scaffolding: PASS. Small signal/action surface with focused tests.
- VII. Runtime Configurability: PASS. Respects the existing dashboard actions flag.
- VIII. Modular Architecture: PASS. Changes stay within schema, service, workflow, and detail UI boundaries.
- IX. Resilient by Default: PASS. Manual bypass is explicit, recorded, and does not pretend prerequisites succeeded.
- X. Continuous Improvement: PASS. Operator action is auditable.
- XI. Spec-Driven Development: PASS. This spec covers the change.
- XII. Canonical Documentation Separation: PASS. No canonical docs needed for this narrow runtime addition.
- XIII. Pre-Release Compatibility Policy: PASS. Adds one explicit signal name without hidden aliases.

## Project Structure

```text
moonmind/schemas/temporal_models.py
moonmind/workflows/temporal/workflows/run.py
moonmind/workflows/temporal/service.py
api_service/api/routers/executions.py
frontend/src/entrypoints/task-detail.tsx
frontend/src/entrypoints/task-detail.test.tsx
tests/unit/workflows/temporal/workflows/test_run_signals_updates.py
tests/unit/workflows/temporal/test_temporal_service.py
tests/unit/api/routers/test_executions.py
```

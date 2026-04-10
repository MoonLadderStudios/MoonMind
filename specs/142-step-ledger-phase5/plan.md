# Implementation Plan: Step Ledger Phase 5

**Branch**: `142-step-ledger-phase5` | **Date**: 2026-04-08 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/142-step-ledger-phase5/spec.md`

## Summary

Reconcile approval-policy review state into the workflow-owned step ledger. `MoonMind.Run` will execute structured review for eligible steps, mutate `checks[]` and `reviewing` status as the authoritative live state, offload full review evidence to artifacts, and expose retry counts plus review evidence in the existing Mission Control Checks section.

## Technical Context

**Language/Version**: Python 3.11 for workflow/runtime code, TypeScript + React 18 for Mission Control  
**Primary Dependencies**: Temporal Python SDK, existing artifact activities, existing `step.review` activity route, React/Vitest task-detail surface  
**Storage**: Compact workflow state for step status/check summaries; JSON artifacts for full review request/verdict payloads; existing browser query cache only  
**Testing**: `pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/workflows/temporal/workflows/test_run_step_ledger.py`  
**Target Platform**: `MoonMind.Run` workflow and Mission Control task-detail UI  
**Project Type**: Backend workflow/state integration with small frontend evidence rendering updates  
**Performance Goals**: Keep review payloads out of workflow state; do not add whole-page observability reads; keep execution-detail polling bounded  
**Constraints**: Preserve latest-run-only semantics, keep review evidence artifact-backed, avoid workflow-state bloat, and keep workflow mutations deterministic and replay-safe  
**Scale/Scope**: `MoonMind.Run` review/check integration, step-ledger helpers, step-detail check rendering/tests, no new page routes or compatibility layers

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. `MoonMind.Run` remains the owner of operator-facing review/check state instead of introducing a second review status system outside the workflow.
- **IV. Own Your Data**: PASS. Full review feedback and issue payloads remain in MoonMind artifacts, not in logs or external-only stores.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work reuses the existing `step.review` route, `checks[]` schema, and Steps UI contract instead of inventing new read paths.
- **IX. Resilient by Default**: PASS. Review state remains deterministic, artifact-backed, and covered by workflow-boundary tests for reviewing/retry transitions.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Phase 5 gets a dedicated feature package with source traceability and explicit validation tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical semantics stay in the step-ledger/review docs; this plan captures rollout work only.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The implementation extends the canonical step ledger rather than preserving a parallel observability-only review story.

## Project Structure

### Documentation (this feature)

```text
specs/142-step-ledger-phase5/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── review-check-ledger-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/step_ledger.py                    # MODIFY: structured check helpers for bounded step-check mutation
moonmind/workflows/temporal/workflows/run.py                 # MODIFY: approval-policy review loop, retry/check mutation, review evidence artifacts
tests/unit/workflows/temporal/test_step_ledger.py            # MODIFY: helper/model coverage for structured checks
tests/unit/workflows/temporal/workflows/test_run_step_ledger.py  # MODIFY: workflow-boundary review/check transition coverage
frontend/src/entrypoints/task-detail.tsx                     # MODIFY: show retry counts and review artifact refs in Checks section
frontend/src/entrypoints/task-detail.test.tsx                # MODIFY: verify review/check rendering in the existing Steps panel
frontend/src/styles/mission-control.css                      # MODIFY: small styling refinements for review metadata in the Checks section
```

**Structure Decision**: Keep review/check production inside `MoonMind.Run` because the step ledger is workflow-owned state. The UI change remains a narrow extension of the Phase 4 Steps panel rather than a new review-specific surface.

## Complexity Tracking

The main implementation risk is mixing review retry logic with existing execution and system retry behavior in a way that obscures the current attempt or bloats workflow state. Mitigation: keep review evidence artifact-backed, isolate check mutation into small step-ledger helpers, and add workflow-boundary tests that assert the final row shape rather than only unit-testing helpers in isolation.

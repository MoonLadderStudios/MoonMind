# Implementation Plan: PR Resolver Child Re-Gating

**Branch**: `187-pr-resolver-regate` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/187-pr-resolver-regate/spec.md`

## Summary

Implement MM-352 by ensuring `MoonMind.MergeAutomation` launches each `pr-resolver` attempt as a child `MoonMind.Run`, consumes an explicit `mergeAutomationDisposition`, and re-enters the merge gate after resolver-generated pushes so stale readiness signals cannot authorize a merge for a new head SHA. The implementation should reuse the parent-owned merge automation child workflow introduced for MM-350, preserve the top-level resolver child `publishMode = none` contract, and validate all resolver dispositions through workflow-boundary tests.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing MoonMind workflow/activity catalog, existing parent-owned merge automation workflow, pr-resolver skill  
**Storage**: Existing Temporal workflow history, Search Attributes, compact workflow state, and existing artifact refs; no new persistent database tables planned  
**Unit Testing**: `pytest` via `./tools/test_unit.sh` with focused targets during iteration  
**Integration Testing**: Workflow-boundary tests under `tests/unit/workflows/temporal/workflows/`; hermetic integration validation via `./tools/test_integration.sh` when compose services are available  
**Target Platform**: Linux worker containers and local Docker Compose Temporal deployment  
**Project Type**: Temporal-backed orchestration service with managed runtime skill execution  
**Performance Goals**: Resolver attempts remain compact in workflow history, re-gating loops avoid duplicate child IDs, and unsupported resolver dispositions fail deterministically  
**Constraints**: Workflow code must remain deterministic; resolver execution must happen through child `MoonMind.Run`; `publishMode` must remain exactly `none` for resolver children; unsupported dispositions must fail through explicit outcome handling; gate freshness must be head-SHA-sensitive  
**Scale/Scope**: One merge automation workflow can run multiple resolver child attempts for one PR, with each attempt tied to a cycle and current head SHA

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan composes `MoonMind.MergeAutomation`, child `MoonMind.Run`, and the existing pr-resolver skill rather than building a new merge engine.
- **II. One-Click Agent Deployment**: PASS. The feature uses existing Temporal workers and runtime surfaces; no new required service is added.
- **III. Avoid Vendor Lock-In**: PASS. Provider-specific review/check semantics remain behind existing gate and resolver contracts.
- **IV. Own Your Data**: PASS. Dispositions, child workflow IDs, blockers, and head-SHA freshness remain in operator-owned Temporal state and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Resolver execution stays on the skill substrate through `task.tool`.
- **VI. Design for Deletion / Scientific Method**: PASS. The behavior is expressed as compact contracts and tests, allowing future resolver internals to change.
- **VII. Powerful Runtime Configurability**: PASS. Merge automation remains explicit runtime configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are isolated to merge automation workflow helpers, contracts, and tests.
- **IX. Resilient by Default**: PASS. Re-gating, deterministic unsupported-disposition handling, and workflow-boundary tests preserve resilient unattended behavior.
- **X. Facilitate Continuous Improvement**: PASS. Outcomes and blockers remain operator-visible and traceable to MM-352.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows `specs/187-pr-resolver-regate/spec.md` and preserves MM-352 traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation tracking stays in MoonSpec artifacts, while source design remains canonical.
- **XIII. Pre-Release Compatibility Policy**: PASS. Unsupported internal resolver disposition values fail fast rather than being translated through compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/187-pr-resolver-regate/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── pr-resolver-regate.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_models.py                       # Merge automation child refs and compact result models
└── workflows/
    └── temporal/
        ├── workflows/
        │   ├── merge_automation.py              # Resolver child loop, disposition handling, re-gating
        │   └── merge_gate.py                    # Shared resolver child request construction and gate semantics helpers
        ├── worker_entrypoint.py                 # Workflow registration
        └── workers.py                           # Workflow type metadata

tests/
└── unit/
    └── workflows/
        └── temporal/
            ├── test_merge_gate_workflow.py
            └── workflows/
                └── test_merge_automation_temporal.py
```

**Structure Decision**: Keep the story inside existing Temporal workflow boundaries. The resolver child run request builder remains shared with merge-gate code, while `MoonMind.MergeAutomation` owns disposition interpretation, successful completion, non-success outcomes, and re-gating loop state.

## Complexity Tracking

No constitution violations.

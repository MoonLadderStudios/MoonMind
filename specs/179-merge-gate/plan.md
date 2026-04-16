# Implementation Plan: Merge Gate

**Branch**: `179-merge-gate` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/179-merge-gate/spec.md`

## Summary

Implement MM-341 by adding a distinct merge-gate lifecycle after successful pull request publication. `MoonMind.Run` continues to own implementation, publishing, proposals, and finalization; after a PR is confirmed and merge automation is enabled, it starts one `MoonMind.MergeGate` workflow carrying compact PR, policy, Jira, and parent-run refs. The merge gate evaluates external readiness through activities, records operator-visible blockers, and creates one resolver follow-up `MoonMind.Run` using pr-resolver with publish mode `none` only when configured conditions are satisfied. Validation focuses on unit tests for contracts and deterministic helpers plus Temporal workflow-boundary tests for parent-to-gate startup, blocker persistence, duplicate resolver prevention, and resolver-side readiness reuse.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing MoonMind workflow/activity catalog, existing GitHub/Jira trusted integration surfaces, pr-resolver skill  
**Storage**: Existing Temporal workflow history, Search Attributes, Memo, and existing execution/projection records; no new persistent database tables planned  
**Unit Testing**: `pytest` via `./tools/test_unit.sh` with focused targets during iteration  
**Integration Testing**: `pytest` Temporal workflow-boundary tests; hermetic integration validation via `./tools/test_integration.sh` when compose services are available  
**Target Platform**: Linux worker containers and local Docker Compose Temporal deployment  
**Project Type**: Temporal-backed orchestration service with API/UI observability surfaces  
**Performance Goals**: Parent implementation run finalizes without waiting on long-lived review latency; merge-gate readiness polling is bounded and does not create duplicate resolver runs under retry or replay  
**Constraints**: Workflow code must remain deterministic; external GitHub/Jira/API reads and resolver execution creation must occur through activities or existing service boundaries; workflow payloads must stay compact and avoid large logs, comments, or provider bodies; pr-resolver follow-up must use publish mode `none`  
**Scale/Scope**: One merge gate per merge-automation-enabled PR publication, tracking one current PR revision and at most one resolver follow-up for that revision

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan composes existing `MoonMind.Run`, Temporal workflow orchestration, GitHub/Jira activities, and pr-resolver instead of rebuilding resolver behavior.
- **II. One-Click Agent Deployment**: PASS. The feature uses existing Docker Compose/Temporal worker infrastructure and does not add required external services beyond already-configured GitHub/Jira integrations.
- **III. Avoid Vendor Lock-In**: PASS. GitHub and Jira readiness are modeled as activity-backed evidence behind merge-gate contracts; provider-specific calls remain outside workflow logic.
- **IV. Own Your Data**: PASS. Gate state, blockers, and resolver refs remain in Temporal state/artifacts/projections under operator-controlled infrastructure, with compact refs rather than external-only storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. The resolver follow-up uses the existing pr-resolver skill contract with publish mode `none`.
- **VI. Design for Deletion / Scientific Method**: PASS. Merge-gate readiness is a narrow contract around observable evidence and tests, so future resolver capabilities can replace parts without changing parent implementation semantics.
- **VII. Powerful Runtime Configurability**: PASS. Merge automation is represented as explicit task/policy input with safe default disabled behavior.
- **VIII. Modular and Extensible Architecture**: PASS. A separate workflow type and activity/service boundaries isolate long-lived gate behavior from parent run execution.
- **IX. Resilient by Default**: PASS. Duplicate resolver prevention, retry-safe launch state, deterministic blockers, and workflow-boundary regression tests are first-class requirements.
- **X. Facilitate Continuous Improvement**: PASS. Gate and resolver outcomes are operator-visible lifecycle states with clear blocked/completed reasons.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan follows `specs/179-merge-gate/spec.md` and preserves MM-341 traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Any implementation tracking stays in MoonSpec artifacts or `docs/tmp` if needed, not in canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. The plan adds explicit new contracts instead of compatibility aliases. Temporal payload changes will be versioned or covered at workflow boundaries where in-flight safety matters.

## Project Structure

### Documentation (this feature)

```text
specs/179-merge-gate/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── merge-gate-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_models.py                  # Compact merge-gate request/result models, if existing schema homes fit
├── workflows/
│   ├── temporal/
│   │   ├── workflows/
│   │   │   ├── run.py                      # Start merge gate after confirmed PR publication
│   │   │   └── merge_gate.py               # New MoonMind.MergeGate workflow
│   │   ├── activities/
│   │   │   └── merge_gate_activities.py    # Readiness evaluation and resolver launch activity wrappers, if split from runtime
│   │   ├── activity_catalog.py             # Register merge-gate activities
│   │   ├── activity_runtime.py             # Activity runtime implementations where existing integration services are bound
│   │   ├── worker_entrypoint.py            # Register the new workflow/activity implementations
│   │   └── workers.py                      # Add MoonMind.MergeGate to registered workflow types
│   └── adapters/
│       └── github_service.py               # Reuse or extend trusted GitHub PR/check helpers as needed

tests/
├── unit/
│   └── workflows/
│       └── temporal/
│           ├── test_merge_gate_models.py
│           ├── test_merge_gate_workflow.py
│           └── test_run_merge_gate_start.py
└── integration/
    └── workflows/
        └── temporal/
            └── test_merge_gate_temporal.py
```

**Structure Decision**: Implement a new Temporal workflow module for long-lived gate state, keep external PR/Jira checks and resolver creation in activities, register the workflow in the existing worker topology, and add focused workflow-boundary tests near existing `MoonMind.Run` and Temporal workflow tests. Data model and contract artifacts are required because the story introduces new workflow payloads, states, and integration boundaries.

## Complexity Tracking

No constitution violations.

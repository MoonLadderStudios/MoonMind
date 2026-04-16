# Implementation Plan: Parent-Owned Merge Automation

**Branch**: `186-parent-owned-merge-automation` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/186-parent-owned-merge-automation/spec.md`

## Summary

Implement MM-350 by changing merge automation from a detached post-publish gate into parent-owned child workflow work for PR-publishing `MoonMind.Run` executions. When `publishMode` is `pr` and merge automation is enabled, the parent must persist a compact publish context, start one `MoonMind.MergeAutomation` child, remain `awaiting_external` while that child is active, and complete only after a successful merge automation result. The implementation should reuse existing merge-gate readiness/resolver activities where they fit, but the workflow type, parent completion behavior, and dependency semantics must follow MM-350 rather than MM-341. Validation focuses on unit tests for request/result contracts and deterministic helper behavior plus workflow-boundary tests for child start, await behavior, duplicate prevention, success completion, and non-success outcome propagation.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing MoonMind workflow/activity catalog, existing merge-gate readiness activities, existing GitHub/Jira trusted integration surfaces, pr-resolver skill  
**Storage**: Existing Temporal workflow history, Search Attributes, Memo, compact publish context refs, and existing execution/projection records; no new persistent database tables planned  
**Unit Testing**: `pytest` via `./tools/test_unit.sh` with focused targets during iteration  
**Integration Testing**: Temporal workflow-boundary tests under `tests/unit/workflows/temporal/workflows/` and hermetic integration validation via `./tools/test_integration.sh` when compose services are available  
**Target Platform**: Linux worker containers and local Docker Compose Temporal deployment  
**Project Type**: Temporal-backed orchestration service with API/UI observability surfaces  
**Performance Goals**: Parent workflow history remains compact during merge automation waiting; duplicate child starts are prevented under retry and replay; waiting uses existing external-wait lifecycle vocabulary  
**Constraints**: Workflow code must remain deterministic; external provider reads and resolver run creation must occur through activities or child workflows; workflow payloads must stay compact; worker-bound `publishMode` remains top-level; no top-level follow-up task or fixed-delay dependency target can satisfy this story; do not add a hidden compatibility alias for workflow type names  
**Scale/Scope**: One parent-owned merge automation child per merge-automation-enabled PR publication, tracking one publish context and one terminal child outcome per parent run

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan composes `MoonMind.Run`, a child workflow boundary, existing readiness activities, and pr-resolver rather than rebuilding resolver behavior.
- **II. One-Click Agent Deployment**: PASS. The feature uses existing Temporal workers and optional GitHub/Jira integrations; no new required external service is added.
- **III. Avoid Vendor Lock-In**: PASS. Provider-specific readiness remains behind activities and trusted integration surfaces.
- **IV. Own Your Data**: PASS. Publish context, child identity, blockers, and outcomes remain in Temporal state, compact metadata, and operator-owned artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Resolver execution continues to use the existing pr-resolver skill path.
- **VI. Design for Deletion / Scientific Method**: PASS. The feature is a narrow workflow contract with boundary tests, allowing future resolver/gate internals to change without altering dependency semantics.
- **VII. Powerful Runtime Configurability**: PASS. Merge automation remains explicit task/policy input with safe disabled behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Parent ownership is isolated at the workflow boundary and reuses existing integration modules.
- **IX. Resilient by Default**: PASS. Duplicate child prevention, compact state, retry/replay safety, and non-success outcome propagation are required.
- **X. Facilitate Continuous Improvement**: PASS. Waiting and failure states are operator-visible and traceable to MM-350.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows `specs/186-parent-owned-merge-automation/spec.md` and preserves MM-350 traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation tracking stays in MoonSpec artifacts and does not rewrite canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. Internal Temporal contract changes will be updated directly with workflow-boundary coverage rather than hidden compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/186-parent-owned-merge-automation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── parent-owned-merge-automation.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_models.py                  # Parent-owned merge automation request/result models if schema consolidation fits
└── workflows/
    └── temporal/
        ├── workflows/
        │   ├── run.py                      # Persist publish context, start and await merge automation child, propagate child outcome
        │   └── merge_automation.py         # MoonMind.MergeAutomation workflow; may move reusable logic from merge_gate.py
        ├── activity_runtime.py             # Existing readiness/resolver activities reused by the child workflow
        ├── activity_catalog.py             # Existing merge-gate activity registrations reused unless names change
        ├── worker_entrypoint.py            # Register any renamed or additional child workflow
        └── workers.py                      # Register workflow type metadata

tests/
├── unit/
│   └── workflows/
│       └── temporal/
│           ├── test_parent_owned_merge_automation_models.py
│           ├── test_run_parent_owned_merge_automation.py
│           └── workflows/
│               └── test_run_parent_owned_merge_automation_boundary.py
└── integration/
    └── workflows/
        └── temporal/
            └── workflows/
                └── test_run.py            # Add hermetic boundary coverage only if local Temporal timing is practical
```

**Structure Decision**: Keep the implementation inside existing Temporal workflow and activity boundaries. Reuse current merge-gate activity/helper code where possible, but expose the source-required `MoonMind.MergeAutomation` child workflow and update parent `MoonMind.Run` behavior so the parent awaits the child and owns the dependency completion signal required by MM-350.

## Complexity Tracking

No constitution violations.

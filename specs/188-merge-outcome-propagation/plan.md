# Implementation Plan: Merge Outcome Propagation

**Branch**: `mm-353-a310aab7` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md) 
**Input**: Single-story feature specification from `/specs/188-merge-outcome-propagation/spec.md`

## Summary

Implement MM-353 by making parent-owned merge automation outcome handling deterministic across parent `MoonMind.Run` completion, dependency satisfaction, and cancellation propagation. Existing merge automation code already returns `merged`, `already_merged`, `blocked`, `failed`, and `expired`; this story completes the contract by treating child `canceled` as parent cancellation, failing missing or unsupported child statuses deterministically, preserving dependency satisfaction only on parent success, and adding workflow-boundary tests for cancellation propagation and all terminal mappings.

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing MoonMind workflow/activity catalog, parent-owned `MoonMind.MergeAutomation`, resolver child `MoonMind.Run` 
**Storage**: Existing Temporal workflow history, Search Attributes, Memo, compact workflow state, and existing execution/projection records; no new persistent database tables planned 
**Unit Testing**: `pytest` via `./tools/test_unit.sh` with focused targets during iteration 
**Integration Testing**: Workflow-boundary tests under `tests/unit/workflows/temporal/workflows/`; hermetic integration validation via `./tools/test_integration.sh` when compose services are available 
**Target Platform**: Linux worker containers and local Docker Compose Temporal deployment 
**Project Type**: Temporal-backed orchestration service with dependency projection and managed runtime child workflows 
**Performance Goals**: Outcome mapping remains constant-time, summaries remain compact, and cancellation propagation does not add polling loops or large workflow payloads 
**Constraints**: Workflow code must remain deterministic; cancellation must use Temporal child workflow cancellation semantics; unsupported merge automation status values must fail fast; dependency satisfaction must stay tied to original parent workflow terminal success; no compatibility aliases for internal status values 
**Scale/Scope**: One parent `MoonMind.Run` awaiting one parent-owned merge automation child, with merge automation optionally supervising one active resolver child at cancellation time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan composes existing parent, merge automation, and resolver child workflow boundaries instead of building a separate dependency engine.
- **II. One-Click Agent Deployment**: PASS. The feature uses existing Temporal workers and does not add services or required external dependencies.
- **III. Avoid Vendor Lock-In**: PASS. Provider-specific merge details remain behind existing resolver and readiness contracts.
- **IV. Own Your Data**: PASS. Outcomes, cancellation state, and dependency signals remain in operator-owned Temporal state and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Resolver work remains child `MoonMind.Run` skill execution.
- **VI. Design for Deletion / Scientific Method**: PASS. The story is expressed as narrow outcome contracts with focused tests.
- **VII. Powerful Runtime Configurability**: PASS. Merge automation remains controlled by existing runtime inputs.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within workflow outcome helpers, cancellation handling, and tests.
- **IX. Resilient by Default**: PASS. Deterministic non-success handling and cancellation propagation improve unattended execution.
- **X. Facilitate Continuous Improvement**: PASS. Operator-readable outcomes remain available for failed, canceled, unsupported-status, and cleanup-incomplete cases.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan follows `specs/188-merge-outcome-propagation/spec.md` and preserves MM-353 traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation notes stay under `specs/` and `local-only handoffs`; canonical docs are not rewritten.
- **XIII. Pre-Release Compatibility Policy**: PASS. Unsupported internal statuses fail deterministically rather than being translated through compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/188-merge-outcome-propagation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── merge-outcome-propagation.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── workflows/
 └── temporal/
 └── workflows/
 ├── run.py # Parent merge automation status mapping, cancellation outcome handling
 └── merge_automation.py # Merge automation canceled status and active resolver cancellation semantics

tests/
└── unit/
 └── workflows/
 └── temporal/
 ├── test_run_parent_owned_merge_automation.py
 └── workflows/
 ├── test_run_parent_owned_merge_automation_boundary.py
 └── test_merge_automation_temporal.py
```

**Structure Decision**: Keep the story inside the existing Temporal workflow boundary. Parent outcome mapping belongs in `MoonMindRunWorkflow`; merge automation cancellation semantics belong in `MoonMindMergeAutomationWorkflow`; dependency satisfaction remains covered by existing dependency signal behavior plus focused parent outcome tests.

## Complexity Tracking

No constitution violations.

# Implementation Plan: Temporal Boundary Models

**Branch**: `177-temporal-boundary-models` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/177-temporal-boundary-models/spec.md`

## Summary

Create a deterministic, testable Temporal boundary inventory for MM-327 that maps covered public workflow, message, query, continuation, and activity boundaries to named Pydantic v2 contract models, approved schema homes, and explicit compatibility status. The implementation adds a lightweight runtime module that owns the inventory, focused schema tests for strict model behavior, an integration-style catalog consistency test to prevent Temporal name drift, and a `docs/tmp` tracker for intentionally incomplete or compatibility-sensitive boundary migration work.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing MoonMind workflow schema modules  
**Storage**: No new persistent storage; inventory is deterministic in-process validation output and docs/tmp tracking  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: pytest integration tests via `./tools/test_integration.sh` where Docker is available; focused non-Docker boundary tests remain in unit suite  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service with Temporal workflows and activity workers  
**Performance Goals**: Inventory construction is constant-size and import-safe for unit tests  
**Constraints**: Preserve all existing Temporal activity, workflow, signal, update, query, and Continue-As-New type names; keep canonical docs desired-state-only; avoid embedding large payload content in workflow histories  
**Scale/Scope**: Covers representative public Temporal boundary families needed to establish STORY-001 contract ownership before broad call-site migration

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Inventory models document orchestration boundaries without replacing Temporal or agent behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites or external services are added.
- **III. Avoid Vendor Lock-In**: PASS. The inventory is provider-neutral and maps MoonMind canonical contracts.
- **IV. Own Your Data**: PASS. No external storage or SaaS dependency is introduced.
- **V. Skills Are First-Class and Easy to Add**: PASS. This change does not alter skill runtime behavior.
- **VI. The Bittersweet Lesson**: PASS. The inventory is a small replaceable contract surface with tests as the anchor.
- **VII. Powerful Runtime Configurability**: PASS. No hardcoded runtime configuration behavior is changed.
- **VIII. Modular and Extensible Architecture**: PASS. Boundary inventory logic is isolated in a Temporal module and schema models.
- **IX. Resilient by Default**: PASS. Temporal name stability and compatibility status are tested.
- **X. Facilitate Continuous Improvement**: PASS. docs/tmp tracking records remaining migration work.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan follows the MM-327 spec and preserves the original Jira brief.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation tracking is placed under `docs/tmp/`.
- **XIII. Pre-release Clean Breaks**: PASS. No compatibility aliases or hidden transforms are added; compatibility-sensitive gaps are tracked explicitly.

## Project Structure

### Documentation (this feature)

```text
specs/177-temporal-boundary-models/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── temporal-boundary-inventory.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_boundary_models.py
└── workflows/
    └── temporal/
        └── boundary_inventory.py

docs/
└── tmp/
    └── 177-TemporalBoundaryModels.md

tests/
├── unit/
│   ├── schemas/
│   │   └── test_temporal_boundary_models.py
│   └── workflows/
│       └── temporal/
│           └── test_boundary_inventory.py
└── integration/
    └── temporal/
        └── test_temporal_boundary_inventory_contract.py
```

**Structure Decision**: Use a small schema module plus a Temporal workflow module so inventory entries are importable by tests and future review gates without forcing workflow code to import test utilities.

## Complexity Tracking

No constitution violations require justification.

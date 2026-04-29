# Implementation Plan: Step Type Authoring Controls

**Branch**: `276-step-type-authoring-controls` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/276-step-type-authoring-controls/spec.md`

## Summary

MM-556 requires the Create page step editor to expose one Step Type discriminator for Tool, Skill, and Preset. Current evidence shows per-step Skill controls and a separate Task Presets authoring section in `frontend/src/entrypoints/task-create.tsx`, so the implementation will add per-step Step Type state, conditionally render type-specific configuration, move preset-use controls into the step editor, and update frontend tests. The validation strategy is Vitest/Testing Library coverage for the Step Type control, type switching, hidden field submission, and canonical section order.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | Step editor shows `Skill (optional)` but no Step Type control | Add one Step Type control per step | frontend unit |
| FR-002 | missing | Type-specific areas are not keyed by a Step Type state | Add conditional Skill/Tool/Preset areas | frontend unit |
| FR-003 | partial | Skill selector and advanced fields exist but are always the default authoring model | Gate Skill controls behind Skill Step Type | frontend unit |
| FR-004 | partial | Preset use exists in separate `Task Presets` section | Render preset selection/apply controls from Preset Step Type in step editor | frontend unit |
| FR-005 | partial | Some copy uses Skill and Task Presets as authoring discriminators | Normalize primary discriminator copy to Step Type | frontend unit |
| FR-006 | partial | Hidden advanced fields are cleared only by advanced toggle, not Step Type | Preserve instructions and clear hidden incompatible fields on type changes/submission | frontend unit |
| SCN-001..006 | missing | No scenario tests for Step Type switching | Add Create page tests for all scenarios | frontend unit |
| DESIGN-REQ-001 | missing | Source model not reflected by Create page | Implement one Step Type per authored step | frontend unit |
| DESIGN-REQ-002 | missing | Preset use is separate from step editor | Move preset use into Step Type area | frontend unit |
| DESIGN-REQ-015 | partial | Internal terms may remain in advanced capability copy but not as discriminator | Ensure discriminator labels use Step Type and canonical values | frontend unit |

## Technical Context

**Language/Version**: TypeScript/React; Python 3.12 remains present but is not expected for this UI story  
**Primary Dependencies**: React, TanStack Query, existing Create page helpers, existing Mission Control stylesheet, Vitest and Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for focused frontend unit coverage, then `./tools/test_unit.sh` for final unit verification when feasible  
**Integration Testing**: Existing frontend render/submission tests are the integration boundary for this Create page slice; compose integration is not required because no backend behavior changes  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend  
**Performance Goals**: Step Type switching should be synchronous and avoid extra network requests except existing preset detail/expand calls  
**Constraints**: Preserve existing submission payload behavior for executable Skill steps; do not introduce docs-only behavior; keep preset expansion deterministic through existing preset endpoint  
**Scale/Scope**: One Create page step-authoring story for MM-556

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing preset expansion and task submission surfaces.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. UI terminology is provider-neutral.
- IV. Own Your Data: PASS. Uses existing local draft state and preset APIs.
- V. Skills Are First-Class and Easy to Add: PASS. Skill remains a first-class Step Type.
- VI. Scientific Method: PASS. Test-first frontend validation is planned.
- VII. Runtime Configurability: PASS. No hardcoded provider behavior added.
- VIII. Modular and Extensible Architecture: PASS. Changes stay within the Create page authoring boundary.
- IX. Resilient by Default: PASS. No workflow/activity contract changes.
- X. Facilitate Continuous Improvement: PASS. Tests and MoonSpec verification document evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, and tasks precede implementation.
- XII. Canonical Documentation Separation: PASS. Runtime work stays in specs and code, not canonical docs migration notes.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms added.

## Project Structure

### Documentation (this feature)

```text
specs/276-step-type-authoring-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-step-type-ui.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/styles/mission-control.css
```

**Structure Decision**: This is a frontend Create page story. Existing backend task submission and preset expansion contracts are reused.

## Complexity Tracking

No constitution violations.

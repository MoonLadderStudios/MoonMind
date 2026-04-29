# Implementation Plan: Preview and Apply Preset Steps

**Branch**: `278-preview-apply-preset-steps` | **Date**: 2026-04-29 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/278-preview-apply-preset-steps/spec.md`

## Summary

MM-558 requires Preset steps to be configured inside the step editor, previewed before application, and then applied into editable Tool and Skill steps with visible warnings and provenance. Current repo evidence shows Step Type `Preset`, per-step preset selection, and direct apply already exist in `frontend/src/entrypoints/task-create.tsx`, while preset expansion happens through the existing task-template expand endpoint. The missing slice is a first-class preview action/state that lists generated steps and warnings before apply, applies exactly the previewed expansion into the selected step position, and blocks unresolved Preset submission. Implementation will add focused frontend state and tests first, then reuse existing expand and mapping helpers for preview/apply.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `task-create.tsx` renders Step Type `Preset` in each step editor | Add focused MM-558 preview/apply tests to lock behavior | frontend unit |
| FR-002 | partial | Detail loading and expand failures surface messages; previewable version is inferred through current detail/expand flow | Verify missing/inactive/unavailable preset errors on preview before apply | frontend unit |
| FR-003 | partial | `resolveTemplateInputs` prepares inputs; expand endpoint validates server-side | Add preview test for invalid preset input failure without draft mutation | frontend unit |
| FR-004 | missing | Current `handleApplyStepPreset` expands and applies in one action; no preview list/warnings before apply | Add preview action/state that calls expand and renders generated steps/warnings | frontend unit |
| FR-005 | missing | No generated-step preview UI exists before apply | Render preview titles and Step Types before apply | frontend unit |
| FR-006 | partial | Direct apply appends generated steps; per-step apply does not replace the temporary Preset step with previewed steps | Apply previewed expansion by replacing selected Preset step | frontend unit |
| FR-007 | implemented_unverified | `mapExpandedStepToState` creates editable step state used by existing apply flow | Verify applied preview steps are editable after apply | frontend unit |
| FR-008 | partial | Applied template metadata exists; per-step visible origin/preview provenance is limited | Preserve existing source/provenance metadata and expose origin text where available | frontend unit |
| FR-009 | partial | Backend expansion validates preset includes and generated steps; frontend handles expand errors | Add tests for generated-step validation failure and warning display | frontend unit |
| FR-010 | partial | Tool step submission is blocked without a selected tool; unresolved Preset submission behavior needs explicit coverage | Block submit while unresolved Preset steps remain | frontend unit |
| FR-011 | implemented_unverified | Preset selection already appears in the step editor; separate Task Presets section still exists for optional management/global apply | Verify step-editor preset use does not require Task Presets section | frontend unit |
| SCN-001..006 | partial | Existing tests cover Step Type and direct preset apply; no preview-before-apply scenarios | Add scenario tests for preview, warnings, apply, unresolved submit block, and no mutation before apply | frontend unit |
| DESIGN-REQ-006 | partial | Preset step is a temporary authoring state but direct apply bypasses preview | Add preview state and default unresolved-blocking behavior | frontend unit |
| DESIGN-REQ-007 | implemented_unverified | Step editor includes Preset controls | Verify in MM-558 tests | frontend unit |
| DESIGN-REQ-009 | missing | No preview step list before apply | Implement preview list and apply-from-preview | frontend unit |
| DESIGN-REQ-010 | partial | Reapply/dirty state exists; undo/detach/compare/origin are not fully visible for per-step preview | Preserve provenance and expose supported origin/warning text; defer unsupported actions from UI | frontend unit |
| DESIGN-REQ-017 | partial | Expand endpoint enforces deterministic expansion and limits; warnings are not rendered before apply | Render warnings in preview and prevent apply on expansion failure | frontend unit |
| DESIGN-REQ-019 | partial | Linked preset mode is not presented, but unresolved Preset submit block needs explicit coverage | Add unresolved Preset submit blocker | frontend unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present for backend validation but is not expected to change for the primary slice  
**Primary Dependencies**: React, TanStack Query, existing task-template catalog/expand endpoints, existing Create page helpers, Vitest and Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: Focused frontend run via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`; final unit suite via `./tools/test_unit.sh` when feasible  
**Integration Testing**: Existing frontend render/submission tests act as the Create page integration boundary; compose integration is not required unless backend expansion contracts change  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend with existing FastAPI-backed task-template expand API  
**Performance Goals**: Preview/apply should reuse existing one expand request per explicit preview and avoid background expansion on selection  
**Constraints**: Preserve existing task-template expansion endpoint, generated step mapping, and optional Task Presets management behavior; unresolved Preset steps must not reach task submission by default  
**Scale/Scope**: One task-authoring story for MM-558 focused on Create page Preset preview/apply behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing preset expansion and step authoring contracts.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Preset behavior is provider-neutral.
- IV. Own Your Data: PASS. Uses local draft state and MoonMind-owned preset APIs.
- V. Skills Are First-Class and Easy to Add: PASS. Generated Skill steps remain first-class editable steps.
- VI. Scientific Method: PASS. TDD with focused frontend tests is planned before implementation.
- VII. Runtime Configurability: PASS. No hardcoded provider/runtime behavior added.
- VIII. Modular and Extensible Architecture: PASS. Changes stay within existing Create page and task-template expansion boundaries.
- IX. Resilient by Default: PASS. Unresolved preset submission is blocked before workflow execution.
- X. Facilitate Continuous Improvement: PASS. Artifacts and tests capture verification evidence.
- XI. Spec-Driven Development: PASS. Spec and plan precede implementation.
- XII. Canonical Documentation Separation: PASS. Runtime implementation details stay under `specs/` and code.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms planned.

## Project Structure

### Documentation (this feature)

```text
specs/278-preview-apply-preset-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-preset-preview.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/styles/mission-control.css
```

**Structure Decision**: This is primarily a frontend Create page story. Existing backend task-template detail/expand services are reused; backend tests are only needed if frontend implementation exposes a backend contract gap.

## Complexity Tracking

No constitution violations.

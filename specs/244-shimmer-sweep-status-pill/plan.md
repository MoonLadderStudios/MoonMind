# Implementation Plan: Shared Executing Shimmer for Status Pills

**Branch**: `244-shimmer-sweep-status-pill` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/244-shimmer-sweep-status-pill/spec.md`

## Summary

Implement MM-488 by extending the existing shared `status-running` pill presentation into a reusable executing shimmer modifier that applies consistently across task list, card, and detail surfaces. The repo already has shared executing-status class mapping and shared status-pill styling, but it does not yet expose the shimmer selector contract, reduced-motion fallback, or verification coverage required by `docs/UI/EffectShimmerSweep.md`. Planned work is focused frontend UI code plus Vitest coverage: add the shared modifier contract, adopt it on executing pills without changing text or layout, add a reduced-motion fallback, and preserve MM-488 traceability.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | Shared executing shimmer contract now exists in `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/styles/mission-control.css`, `frontend/src/entrypoints/tasks-list.tsx`, and `frontend/src/entrypoints/task-detail.tsx`; verified by focused Vitest helper/CSS and entrypoint tests plus `./tools/test_unit.sh`. | complete | unit + integration |
| FR-002 | implemented_verified | Executing pills now expose the preferred `data-state="executing"` and `data-effect="shimmer-sweep"` selectors plus the fallback `.is-executing` marker through `executionStatusPillProps`; verified in helper, list, and detail tests. | complete | unit + integration |
| FR-003 | implemented_verified | Only explicit executing pills opt into shimmer metadata and `.is-executing`; non-executing pills remain plain in helper, list, and detail tests. | complete | unit + integration |
| FR-004 | implemented_verified | List and detail rendering still uses the existing visible status text while shimmer attaches additively through span props only; verified by entrypoint render tests. | complete | integration |
| FR-005 | implemented_verified | Shimmer stays on existing pill spans without wrappers or layout changes, and reduced-motion fallback is CSS-only; verified by CSS contract and entrypoint tests. | complete | unit + integration |
| FR-006 | implemented_verified | `frontend/src/styles/mission-control.css` now defines a reduced-motion non-animated active treatment for executing pills; verified in Mission Control CSS contract tests. | complete | unit + integration |
| FR-007 | implemented_verified | Shared CSS uses bounded active-progress tokens and additive gradient sweep rather than warning/error styling; verified by CSS contract assertions. | complete | unit |
| FR-008 | implemented_verified | Task list table/cards and task detail toolbar/dependency pills reuse the same shared helper contract; verified by entrypoint render tests. | complete | integration |
| FR-009 | implemented_verified | MM-488 is preserved in spec, tasks, plan, helper traceability export, and verification output. | complete | unit |
| SCN-001 | implemented_verified | Executing pills across supported surfaces render the shared shimmer modifier contract; verified by task list and task detail tests. | complete | integration |
| SCN-002 | implemented_verified | Preferred hooks and fallback marker resolve to the same shared modifier contract; verified by helper and CSS contract tests. | complete | unit + integration |
| SCN-003 | implemented_verified | Non-executing pills do not inherit shimmer metadata or selectors; verified by helper, list, and detail tests. | complete | unit + integration |
| SCN-004 | implemented_verified | Text and surrounding layout remain stable while shimmer is active; verified by entrypoint render tests and additive CSS contract checks. | complete | integration |
| SCN-005 | implemented_verified | Reduced-motion path disables animated sweep while preserving executing-state emphasis; verified by Mission Control CSS contract tests. | complete | unit + integration |
| SC-001 | implemented_verified | Focused unit/integration tests confirm shared executing shimmer across supported surfaces. | complete | unit + integration |
| SC-002 | implemented_verified | Focused tests confirm both activation paths land on the same contract. | complete | unit + integration |
| SC-003 | implemented_verified | Focused tests confirm shimmer remains executing-only. | complete | unit + integration |
| SC-004 | implemented_verified | Focused integration tests confirm no text/layout/live-update regressions from the additive modifier. | complete | integration |
| SC-005 | implemented_verified | CSS contract tests confirm reduced-motion fallback remains active and non-animated. | complete | unit + integration |
| SC-006 | implemented_verified | MM-488 appears in spec, plan, tasks, helper traceability export, and `verification.md`. | complete | unit |
| DESIGN-REQ-001 | implemented_verified | Shared shimmer reads as active progress through calm cyan/white sweep tokens, not warning/error visuals; verified by CSS contract assertions. | complete | unit |
| DESIGN-REQ-002 | implemented_verified | Work stayed limited to status-pill modifier activation with no unrelated page behavior changes; verified by entrypoint tests and unchanged surrounding markup. | complete | integration |
| DESIGN-REQ-003 | implemented_verified | Host contract now supports preferred data attributes and fallback `.is-executing`; verified by helper and entrypoint tests. | complete | unit + integration |
| DESIGN-REQ-004 | implemented_verified | Shimmer remains additive on existing pill hosts and preserves layout/text semantics; verified by CSS and entrypoint tests. | complete | unit + integration |
| DESIGN-REQ-011 | implemented_verified | Shared Mission Control status-pill CSS implements the shimmer modifier instead of page-local animation forks; verified by helper/CSS contract tests and surface reuse. | complete | unit + integration |
| DESIGN-REQ-013 | implemented_verified | Reduced-motion replacement treatment is present and verified in CSS contract tests. | complete | unit + integration |
| DESIGN-REQ-016 | implemented_verified | Shimmer activation is constrained to explicit executing status via `executionStatusPillProps`; helper tests cover the future-state non-goal. | complete | unit + integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, existing entrypoint render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet and status-pill rendering refinement  
**Performance Goals**: Preserve status-pill readability, avoid measurable layout shift, keep the effect bounded to existing pill surfaces, and use lightweight pseudo-element animation with reduced-motion fallback  
**Constraints**: Runtime mode only; no page-local animation forks; no wrapper that changes pill dimensions; preserve text/icon/live-update behavior; preserve MM-488 traceability; keep unit and integration strategies explicit  
**Scale/Scope**: Shared status-pill helper/CSS plus task list and task detail surfaces, with focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI behavior remains provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the visual behavior is defined through shared UI contract and tests, not bespoke page logic.
- VII. Runtime Configurability: PASS - behavior remains driven by runtime state and CSS/media-query conditions rather than hardcoded per-page branching.
- VIII. Modular and Extensible Architecture: PASS - work stays inside existing shared status-pill helper, entrypoints, and stylesheet.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - traceability and verification artifacts remain explicit.
- XI. Spec-Driven Development: PASS - spec, plan, and design artifacts are present before task generation.
- XII. Canonical Documentation Separation: PASS - implementation planning stays under `specs/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; the shared status-pill contract will be updated directly.

## Project Structure

### Documentation (this feature)

```text
specs/244-shimmer-sweep-status-pill/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── mission-control.test.tsx
│   ├── task-detail.test.tsx
│   ├── task-detail.tsx
│   ├── tasks-list.test.tsx
│   └── tasks-list.tsx
├── styles/
│   └── mission-control.css
└── utils/
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Extend the existing shared status-pill helper, Mission Control stylesheet, and entrypoint render tests used by task list, card, and detail surfaces. Do not introduce a second status-pill component or a page-local shimmer implementation.

## Complexity Tracking

No constitution violations or added architectural complexity.

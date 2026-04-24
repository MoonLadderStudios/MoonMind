# Implementation Plan: Calm Shimmer Motion and Reduced-Motion Fallback

**Branch**: `246-animate-shimmer-motion` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/246-animate-shimmer-motion/spec.md`

## Summary

MM-490 is the motion-profile refinement on top of the shared executing shimmer already introduced by MM-488 and visually refined by MM-489. The implemented story stays frontend-only and TDD-first: focused CSS/helper tests now prove cadence, no-overlap timing, center-brightness emphasis, reduced-motion active fallback, and MM-490 traceability, while the shared Mission Control shimmer contract now encodes the required cycle timing and verification surfaces without introducing page-local forks.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/styles/mission-control.css` now keeps the executing shimmer clipped with `overflow: hidden`, starts at `--mm-executing-sweep-start-x: -135%`, ends at `--mm-executing-sweep-end-x: 135%`, and the focused CSS plus list/detail tests prove bounded left-to-right travel. | none | unit + integration |
| FR-002 | implemented_verified | `frontend/src/styles/mission-control.css` now defines `--mm-executing-sweep-cycle-duration: 1670ms`, removes standalone `animation-delay`, and uses `65%`, `86.83%`, and `100%` keyframes so the repeat gap is encoded inside each cycle without overlap; focused CSS tests prove the cadence contract. | none | unit + integration |
| FR-003 | implemented_verified | `frontend/src/styles/mission-control.css` now enlarges the shimmer bands at `65%` to emphasize the center of the pill, and the focused CSS tests assert the midpoint-emphasis contract. | none | unit + integration |
| FR-004 | implemented_verified | The reduced-motion block in `frontend/src/styles/mission-control.css` disables animation with `animation: none` while keeping a static highlight, and focused CSS plus list/detail tests verify the fallback semantics. | none | unit + integration |
| FR-005 | implemented_verified | `frontend/src/styles/mission-control.css` and the task list/detail render tests now prove the reduced-motion presentation still reads as active on supported executing-pill surfaces without animation. | none | unit + integration |
| FR-006 | implemented_verified | `executionStatusPillProps` and the existing plus MM-490-focused list/detail tests keep shimmer metadata executing-only. | none | none beyond final verify |
| FR-007 | implemented_verified | `frontend/src/utils/executionStatusPillClasses.ts` now preserves `relatedJiraIssues: ['MM-489', 'MM-490']`, and helper plus render tests assert MM-490 traceability. | none | unit |
| SCN-001 | implemented_verified | Focused CSS and surface tests now prove the executing sweep stays bounded while moving left-to-right across the pill. | none | unit + integration |
| SCN-002 | implemented_verified | The 1670ms cycle token and revised keyframes now encode the calm cadence with an internal idle gap and no overlap, proven by focused CSS assertions. | none | unit + integration |
| SCN-003 | implemented_verified | Center-brightness emphasis is now explicit in the shared keyframes and asserted by the MM-490 CSS contract test. | none | unit + integration |
| SCN-004 | implemented_verified | Reduced-motion fallback semantics are directly verified through the shared CSS contract and supported-surface render tests. | none | unit + integration |
| SCN-005 | implemented_verified | Task list and task detail tests now confirm reduced-motion conditions still communicate executing as active without animation. | none | unit + integration |
| SCN-006 | implemented_verified | Existing helper and render tests continue to keep the shimmer off for non-executing states. | none | none beyond final verify |
| SC-001 | implemented_verified | Focused unit and integration evidence confirm the sweep remains clipped to the rounded pill bounds while traveling left-to-right. | none | unit + integration |
| SC-002 | implemented_verified | Focused CSS evidence confirms the full shimmer cycle stays within the 1.6 to 1.8 second target and does not overlap between cycles. | none | unit + integration |
| SC-003 | implemented_verified | Focused CSS evidence confirms the brightest emphasis occurs near the pill center rather than at either edge. | none | unit + integration |
| SC-004 | implemented_verified | Focused CSS and render evidence confirm reduced motion disables animation and preserves a static active highlight. | none | unit + integration |
| SC-005 | implemented_verified | Focused list/detail render evidence confirms the reduced-motion treatment still communicates executing as active without motion. | none | unit + integration |
| SC-006 | implemented_verified | Existing helper and entrypoint tests continue to prove non-executing states do not activate the MM-490 motion treatment. | none | none beyond final verify |
| SC-007 | implemented_verified | `MM-490` now appears in the helper traceability export, focused tests, Moon Spec artifacts, and final verification output. | none | unit |
| DESIGN-REQ-007 | implemented_verified | The shared executing-state trigger and reduced-motion path are now explicitly covered by helper, CSS, and supported-surface tests. | none | unit + integration |
| DESIGN-REQ-010 | implemented_verified | The motion-profile details are now encoded by the cycle-duration token and revised keyframes, with focused CSS tests proving bounded travel, calm cadence, no overlap, and midpoint emphasis. | none | unit + integration |
| DESIGN-REQ-012 | implemented_verified | Reduced-motion behavior is now directly verified as animation-off plus static active highlight across the shared CSS contract and supported surfaces. | none | unit + integration |
| DESIGN-REQ-014 | implemented_verified | Shimmer activation remains executing-only through helper and render coverage. | none | none beyond final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, shared execution-status helper, existing entrypoint render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet and status-pill rendering refinement  
**Performance Goals**: Preserve pill readability, keep the shimmer bounded to the existing pill footprint, avoid layout shift, and maintain lightweight CSS-only animation with an explicit reduced-motion fallback  
**Constraints**: Runtime mode only; preserve shared shimmer reuse; keep the story scoped to motion profile and reduced-motion fallback; no page-local animation forks; preserve MM-490 traceability; keep unit and integration strategies explicit; managed branch naming does not satisfy the setup-plan helper convention  
**Scale/Scope**: Shared Mission Control shimmer tokens/keyframes plus list/detail status-pill surfaces, with focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI behavior remains provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the story is governed by shared CSS/helper contracts and focused tests rather than page-local logic.
- VII. Runtime Configurability: PASS - behavior remains driven by state selectors, shared tokens, and reduced-motion media queries.
- VIII. Modular and Extensible Architecture: PASS - work stays within existing Mission Control helper, stylesheet, and render-test seams.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - explicit requirement status, traceability, and verification evidence remain planned.
- XI. Spec-Driven Development: PASS - spec and planning artifacts exist before task generation.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/246-animate-shimmer-motion/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; the shared shimmer contract will be updated directly if tests expose gaps.

## Project Structure

### Documentation (this feature)

```text
specs/246-animate-shimmer-motion/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer-motion.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
└── spec.md
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
    ├── executionStatusPillClasses.test.ts
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Reuse the shared Mission Control status-pill helper, stylesheet, and existing task list/detail surfaces. Do not introduce a separate status-pill component or page-local shimmer implementation for MM-490.

## Complexity Tracking

No constitution violations or added architectural complexity.

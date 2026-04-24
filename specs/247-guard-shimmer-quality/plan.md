# Implementation Plan: Shimmer Quality Regression Guardrails

**Branch**: `247-guard-shimmer-quality` | **Date**: 2026-04-24 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/247-guard-shimmer-quality/spec.md`

## Summary

MM-491 is implemented for the selected single-story scope as a verification-first frontend refinement on top of the shared executing shimmer introduced by MM-488 and refined by MM-489/MM-490. Focused CSS/helper and render tests now prove executing-state readability guardrails, state-matrix isolation, reduced-motion fallback behavior, the existing shimmer-model non-goal boundary, and MM-491 runtime-adjacent traceability without introducing any page-local shimmer fork.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/styles/mission-control.css` keeps the executing shimmer clipped with `overflow: hidden` and token-driven background layers, while `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` now verify shared executing-pill guardrails and supported-surface behavior. | none | unit + integration |
| FR-002 | implemented_verified | `frontend/src/utils/executionStatusPillClasses.ts` limits shimmer metadata to `executing`, and helper plus entrypoint tests now cover waiting, awaiting external, finalizing, and other non-executing states remaining plain. | none | unit + integration |
| FR-003 | implemented_verified | The shimmer remains a background-only treatment on the existing pill host, and the focused CSS/render verification proves no wrapper-based or page-local layout mutation was introduced for MM-491. | none | unit + integration |
| FR-004 | implemented_verified | `frontend/src/entrypoints/mission-control.test.tsx` now verifies the executing shimmer block remains tied to shared accent tokens and reduced-motion styling, and list/detail render tests preserve the shared active treatment on supported surfaces. | none | unit + integration |
| FR-005 | implemented_verified | `frontend/src/styles/mission-control.css` preserves `animation: none` plus a static highlight under reduced motion, and focused unit/integration tests now verify that executing still reads as active without animation. | none | unit + integration |
| FR-006 | implemented_verified | The shared shimmer contract remains the effect under test, with `frontend/src/entrypoints/mission-control.test.tsx` and `specs/247-guard-shimmer-quality/quickstart.md` now explicitly rejecting replacement by an unrelated effect family. | none | unit + integration |
| FR-007 | implemented_verified | `frontend/src/utils/executionStatusPillClasses.ts` now preserves `MM-491` in `relatedJiraIssues`, and helper/list/detail tests assert that runtime-adjacent traceability. | none | unit |
| SCN-001 | implemented_verified | Focused CSS and supported-surface tests now verify the executing shimmer contract remains bounded and readable on the existing pill host. | none | unit + integration |
| SCN-002 | implemented_verified | Helper and render tests now verify the listed non-executing states remain plain and never pick up shimmer metadata. | none | unit + integration |
| SCN-003 | implemented_verified | The verification pass confirms the story stays within the existing pill host and shared surfaces without introducing layout-affecting wrappers or page-local variants. | none | unit + integration |
| SCN-004 | implemented_verified | Theme-aware token assertions in `frontend/src/entrypoints/mission-control.test.tsx` keep the executing shimmer tied to the intended active treatment in supported themes. | none | unit + integration |
| SCN-005 | implemented_verified | Focused CSS plus list/detail tests now verify reduced-motion conditions preserve an active fallback without animation. | none | unit + integration |
| SC-001 | implemented_verified | Focused unit and integration evidence now cover executing shimmer readability and bounded behavior on shared surfaces. | none | unit + integration |
| SC-002 | implemented_verified | Focused CSS evidence verifies clipping and reduced-motion positioning/size expectations for the executing shimmer contract. | none | unit + integration |
| SC-003 | implemented_verified | Helper and entrypoint tests now verify the non-executing state matrix remains shimmer-free. | none | unit + integration |
| SC-004 | implemented_verified | The shared shimmer treatment remains attached to the existing pill host without any layout-affecting structural change, and the focused render checks continue to pass on supported surfaces. | none | unit + integration |
| SC-005 | implemented_verified | Theme-aware assertions confirm the executing shimmer remains an intentional active treatment rather than an accidental token combination. | none | unit + integration |
| SC-006 | implemented_verified | Reduced-motion unit and integration coverage now proves animation disables cleanly while preserving a static active fallback. | none | unit + integration |
| SC-007 | implemented_verified | `MM-491` now appears in runtime-adjacent helper/test evidence, Moon Spec artifacts, and final verification output. | none | unit |
| DESIGN-REQ-004 | implemented_verified | Host text preservation and non-layout-changing behavior are now backed by focused CSS/helper and supported-surface tests. | none | unit + integration |
| DESIGN-REQ-009 | implemented_verified | Existing CSS guardrails plus new focused tests now cover clipping, isolation, and supported-surface shimmer behavior. | none | unit + integration |
| DESIGN-REQ-011 | implemented_verified | Reduced-motion fallback behavior is now verified directly through focused CSS and surface tests. | none | unit + integration |
| DESIGN-REQ-014 | implemented_verified | Full state-matrix regression coverage now keeps shimmer activation executing-only. | none | unit + integration |
| DESIGN-REQ-016 | implemented_verified | The MM-491 verification surface now explicitly preserves the existing shimmer model and rejects unrelated substitute effect families. | none | unit + integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, shared execution-status helper, existing task list/detail render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet, helper traceability, and supported status-pill render-surface verification  
**Performance Goals**: Preserve label readability, keep the shimmer bounded to the current pill footprint, avoid layout shift, and keep the regression suite focused on the shared CSS/helper contract rather than introducing page-local forks  
**Constraints**: Runtime mode only; preserve shared shimmer reuse; keep the story scoped to regression guardrails; preserve MM-491 traceability; keep unit and integration strategies explicit; managed branch naming does not satisfy the setup-plan helper convention  
**Scale/Scope**: Shared Mission Control shimmer contract plus list/detail status-pill surfaces and focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI regression guardrails remain provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the story is governed by shared CSS/helper contracts and focused tests rather than page-local logic.
- VII. Runtime Configurability: PASS - behavior remains driven by state selectors, shared tokens, and reduced-motion media queries.
- VIII. Modular and Extensible Architecture: PASS - work stays within existing Mission Control helper, stylesheet, and render-test seams.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - explicit requirement status, traceability, and verification evidence remain captured.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification artifacts now align for the single story.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/247-guard-shimmer-quality/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim was introduced; the shared shimmer contract was updated directly only for MM-491 traceability.

## Project Structure

### Documentation (this feature)

```text
specs/247-guard-shimmer-quality/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer-quality.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── mission-control.test.tsx
│   ├── task-detail.test.tsx
│   └── tasks-list.test.tsx
├── styles/
│   └── mission-control.css
└── utils/
    ├── executionStatusPillClasses.test.ts
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Reuse the shared Mission Control status-pill helper, stylesheet, and existing task list/detail surfaces. Do not introduce a separate component or a page-local shimmer variant for MM-491.

## Complexity Tracking

No constitution violations or added architectural complexity.

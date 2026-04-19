# Implementation Plan: Liquid Glass Publish Panel

**Branch**: `210-liquid-glass-panel` | **Date**: 2026-04-19 | **Spec**: `specs/210-liquid-glass-panel/spec.md`
**Input**: Single-story feature specification from `specs/210-liquid-glass-panel/spec.md`

## Summary

Enhance the existing fixed Create Page bottom publish/action control bar so it reads as an intentional liquid glass surface with blur, refractive depth, and readable controls while preserving repository, branch, publish mode, and create submission behavior. Repo inspection shows the bottom bar already exists in `frontend/src/entrypoints/task-create.tsx` and has baseline blur styling in `frontend/src/styles/mission-control.css`; the remaining work is to refine the visual treatment, add focused UI verification for the surface and responsiveness, and keep task creation request-shape behavior covered.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/styles/mission-control.css` has `.queue-floating-bar` with blur and translucent background | strengthen liquid glass/refraction visual treatment | unit + integration-style UI |
| FR-002 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` renders repo, branch, publish mode, and submit in `.queue-floating-bar` | preserve target panel and add selector/assertion coverage | unit UI |
| FR-003 | partial | existing controls are readable, but no MM-210-specific contrast/readability coverage | preserve/readability-test labels, values, icons, and action states | unit + integration-style UI |
| FR-004 | implemented_unverified | existing Create page controls and submit mapping are already wired | add regression coverage proving interactions still work | integration-style UI |
| FR-005 | implemented_unverified | existing submit payload code maps repository, branch, publish mode, and merge automation | keep payload semantics and verify unchanged request shape | integration-style UI |
| FR-006 | partial | `.queue-floating-bar-row` uses fixed grid tracks and responsive rule | add stability-focused tests or assertions for dynamic states | unit UI |
| FR-007 | partial | mobile media rule exists for `.queue-floating-bar-row` | refine responsive styling if needed and verify mobile-width fit | unit + documented visual verification |
| FR-008 | partial | shared CSS supports light/dark tokens and fallback backdrop behavior | verify treatment remains readable in both theme scopes | unit + documented visual verification |
| FR-009 | missing | no test specifically covers liquid glass panel treatment | add focused style/class and behavior tests plus quickstart visual checks | unit + integration-style UI |
| FR-010 | implemented_unverified | `spec.md` preserves the supplied Jira issue reference and original brief | carry traceability through tasks, verification, commit, and PR metadata | final verify |
| SC-001 | missing | no current MM-210 visual verification evidence | add test/quickstart evidence for liquid glass treatment | unit + visual verification |
| SC-002 | missing | no theme-specific readability evidence for this panel | add light/dark verification steps | unit + visual verification |
| SC-003 | partial | responsive CSS exists, but no MM-210 verification evidence | verify desktop and mobile widths | unit + visual verification |
| SC-004 | implemented_unverified | existing tests cover Create page interactions broadly | preserve and add targeted regression coverage | integration-style UI |
| SC-005 | implemented_unverified | existing submission tests cover valid draft creation | rerun focused Create page tests after styling changes | integration-style UI |
| SC-006 | implemented_unverified | spec preserves supplied reference | preserve through downstream artifacts and final report | final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; CSS for shared Mission Control styling; Python 3.12 remains present but is not expected in this story
**Primary Dependencies**: React, TanStack Query, existing Create page entrypoint, existing Mission Control stylesheet, Vitest and Testing Library
**Storage**: No new persistent storage; existing task draft and submission payload state only
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
**Integration Testing**: Focused Create page request-shape and interaction tests in `frontend/src/entrypoints/task-create.test.tsx`; no compose-backed service integration is required for this visual UI story
**Target Platform**: Mission Control browser UI served by FastAPI
**Project Type**: Web UI
**Performance Goals**: Preserve immediate control interaction and avoid adding network calls or submission latency; visual treatment should rely on existing lightweight CSS surface patterns
**Constraints**: Preserve Create page submission contract, repository branch lookup behavior, publish mode semantics, accessibility labels, and responsive stability
**Scale/Scope**: One Create page bottom control bar, focused Create page tests, and optional desired-state docs alignment

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story stays in the existing Create page UI and does not introduce agent orchestration changes.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. No new provider-specific browser integration is introduced.
- IV. Own Your Data: PASS. Existing task draft and payload data boundaries remain unchanged.
- V. Skills Are First-Class and Easy to Add: PASS. The change does not alter skill contracts or selection.
- VI. Replaceable Scaffolding: PASS. Work is isolated to style and focused tests around the existing UI surface.
- VII. Runtime Configurability: PASS. Existing runtime and publish controls remain configuration-driven through current UI state.
- VIII. Modular Architecture: PASS. Work stays in the existing Create page entrypoint and shared Mission Control style layer.
- IX. Resilient by Default: PASS. Existing validation and submit behavior are preserved and tested.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Planning follows the active single-story spec.
- XII. Canonical Documentation Separation: PASS. Long-lived docs remain desired-state if touched; implementation evidence remains in specs.
- XIII. Pre-Release Velocity: PASS. No compatibility alias or hidden fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/210-liquid-glass-panel/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── liquid-glass-panel.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

frontend/src/styles/
└── mission-control.css

docs/UI/
├── CreatePage.md
└── MissionControlStyleGuide.md
```

**Structure Decision**: Preserve the existing Create page and shared stylesheet surfaces. Use `task-create.test.tsx` for focused UI behavior/request-shape coverage and `mission-control.css` for visual treatment changes.

## Complexity Tracking

No constitution violations.

# Implementation Plan: Surface Hierarchy and liquidGL Fallback Contract

**Branch**: `217-surface-hierarchy-liquidgl` | **Date**: 2026-04-21 | **Spec**: `specs/217-surface-hierarchy-liquidgl/spec.md`
**Input**: Single-story feature specification from `specs/217-surface-hierarchy-liquidgl/spec.md`

## Summary

Implement MM-425 by making Mission Control's shared surface hierarchy explicit in `frontend/src/styles/mission-control.css` and verifying it in focused frontend tests. Existing specs already introduced visual tokens, task-list data slabs, and a Create page liquidGL target; this story fills the remaining design-system contract by adding stable semantic surface modifiers, token-driven glass fallback rules, liquidGL opt-in guardrails, and dense-surface protections without changing runtime submission, routing, or data-fetching behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `.panel--controls`, `.panel--data`, `.queue-floating-bar--liquid-glass` exist; satin/accent/floating utility roles are not explicit | add missing semantic surface selectors and tests | unit UI |
| FR-002 | implemented_unverified | glass tokens and `.panel--controls` exist; not all glass control selectors share fallback contract | add/verify shared glass role styling | unit UI |
| FR-003 | partial | route nav fallback exists; shared glass role fallback is incomplete | add `@supports not` fallback for glass/floating/liquid roles | unit UI |
| FR-004 | implemented_unverified | Create page liquid panel has CSS shell and tests | preserve and extend fallback assertions | unit UI |
| FR-005 | partial | no default liquidGL on `.panel`/`.card`; tests do not assert dense exclusions | add negative CSS tests for dense/default surfaces | unit UI |
| FR-006 | partial | `.panel--data`, table slabs, and inputs are near-opaque; satin/nested dense modifiers are incomplete | add satin and nested dense surface rules | unit UI |
| FR-007 | implemented_unverified | `.queue-floating-bar--liquid-glass` is explicit opt-in | add bounded target and fallback assertions | unit UI |
| FR-008 | implemented_unverified | liquidGL class is explicit; no automated one-hero posture assertion | test that liquidGL is opt-in and not default | unit UI |
| FR-009 | missing | no MM-425 test covers full hierarchy contract | add focused tests in Mission Control/Create page suites | unit UI |
| FR-010 | implemented_unverified | MM-425 preserved in `spec.md` and source input | carry through tasks and verification | final verify |
| DESIGN-REQ-003 | partial | dense task-list slabs exist | verify dense/editing surfaces remain grounded | unit UI |
| DESIGN-REQ-004 | implemented_unverified | liquidGL is opt-in | verify one-hero posture through absence from defaults | unit UI |
| DESIGN-REQ-005 | partial | some roles exist | add complete role selectors | unit UI |
| DESIGN-REQ-007 | partial | glass tokens exist | add fallback coverage | unit UI |
| DESIGN-REQ-008 | partial | default panels/cards are not liquidGL | add explicit CSS tests and fallback rules | unit UI |
| DESIGN-REQ-015 | partial | stable class names exist for controls/data | add satin/floating/utility/accent role names | unit UI |
| DESIGN-REQ-018 | implemented_unverified | Create page target uses bounded selector | add fallback assertion | unit UI |
| DESIGN-REQ-027 | implemented_unverified | task-list data slabs exist | preserve task-list tests | unit UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control tests; CSS for shared Mission Control styling; Python 3.12 remains present but is not expected in this story
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control entrypoints, shared stylesheet
**Storage**: No new persistent storage; visual and interaction state only
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/tasks-list.test.tsx`
**Integration Testing**: Focused frontend integration-style tests in the same Vitest suites; no compose-backed service integration is required for this CSS/UI contract story
**Target Platform**: Mission Control browser UI served by FastAPI
**Project Type**: Web UI
**Performance Goals**: Preserve immediate UI interaction; use CSS-only fallback rules and existing liquidGL initialization behavior without new network work
**Constraints**: Preserve task submission, routing, task-list requests, accessibility labels, responsive stability, and dense content readability
**Scale/Scope**: Shared Mission Control stylesheet plus focused tests for representative task-list and Create page surfaces

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Work extends the existing Mission Control UI surfaces.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. No provider-specific integration is introduced.
- IV. Own Your Data: PASS. No data storage or external data flow changes.
- V. Skills Are First-Class and Easy to Add: PASS. Skill contracts and runtime selection are untouched.
- VI. Replaceable Scaffolding: PASS. Styling contract stays in shared CSS and tests.
- VII. Runtime Configurability: PASS. Existing runtime controls remain unchanged.
- VIII. Modular Architecture: PASS. Changes are scoped to the shared stylesheet and existing frontend tests.
- IX. Resilient by Default: PASS. Existing UI behavior is preserved and regression-tested.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. This plan follows the MM-425 single-story spec.
- XII. Canonical Documentation Separation: PASS. Jira input and implementation evidence stay in `docs/tmp` and `specs/`.
- XIII. Pre-Release Velocity: PASS. No compatibility alias or hidden fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/217-surface-hierarchy-liquidgl/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── surface-hierarchy-contract.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/styles/
└── mission-control.css

frontend/src/entrypoints/
├── mission-control.test.tsx
├── task-create.test.tsx
└── tasks-list.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-425-moonspec-orchestration-input.md
```

**Structure Decision**: Implement the shared runtime surface contract in `mission-control.css` and verify it through existing frontend suites that already cover Mission Control chrome, task-list data slabs, and Create page liquidGL controls.

## Complexity Tracking

No constitution violations.

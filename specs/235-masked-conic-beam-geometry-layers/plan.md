# Implementation Plan: Masked Conic Beam Geometry and Layers

**Branch**: `235-masked-conic-beam-geometry-layers` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md) 
**Input**: Single-story feature specification from `specs/235-masked-conic-beam-geometry-layers/spec.md`

## Summary

Implement MM-466 by completing the MaskedConicBorderBeam geometry contract introduced by MM-465: expose verifiable beam footprint variables, derive the inner border-ring mask from configured border width and radius, keep beam/glow layers separate from content, and add focused Vitest coverage for the source-design geometry requirements. The existing component and Mission Control CSS provide a partial base; this story adds missing verification and small runtime CSS/component refinements rather than creating a second component.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `MaskedConicBorderBeam` active render test verifies separate beam, glow, and content layers | complete | focused unit passed |
| FR-002 | implemented_verified | `frontend/src/components/MaskedConicBorderBeam.tsx` marks beam/glow `aria-hidden`; active layered test verifies separation | complete | focused unit passed |
| FR-003 | implemented_verified | CSS contract test verifies content-box mask and exclude composite for beam/glow layers | complete | focused unit passed |
| FR-004 | implemented_verified | component and CSS tests verify `--beam-inner-inset` derives from borderWidth | complete | focused unit passed |
| FR-005 | implemented_verified | component and CSS tests verify `--beam-inner-radius` derives from borderRadius minus borderWidth | complete | focused unit passed |
| FR-006 | implemented_verified | component and CSS tests verify default `--beam-head-arc: 12deg` and `--beam-tail-arc: 28deg` | complete | focused unit passed |
| FR-007 | implemented_verified | CSS tests verify conic-gradient footprint variables for transparent/tail/head/fade stops | complete | focused unit passed |
| FR-008 | implemented_verified | CSS tests verify glow footprint, lower opacity, blur, and content exclusion | complete | focused unit passed |
| FR-009 | implemented_verified | trail variant tests verify footprint-only changes and shared orbit speed | complete | focused unit passed |
| FR-010 | implemented_verified | active component-level test verifies nested child text/control content remains separate and readable | complete | focused unit passed |
| FR-011 | implemented_verified | traceability constant and tests preserve MM-466 with DESIGN-REQ-004/005/006/011 | complete | focused unit passed |
| SC-001 | implemented_verified | active layered render test verifies static border contract, beam, glow, and content separation | complete | focused unit passed |
| SC-002 | implemented_verified | CSS and component tests verify inner inset/radius variables | complete | focused unit passed |
| SC-003 | implemented_verified | CSS and component tests verify default footprint variables and conic-gradient usage | complete | focused unit passed |
| SC-004 | implemented_verified | CSS tests verify glow opacity, blur, footprint, and content exclusion | complete | focused unit passed |
| SC-005 | implemented_verified | active component-level rendering test verifies child content preservation | complete | focused unit passed |
| SC-006 | implemented_verified | MM-466 preserved in spec, plan, tasks, traceability constant, and verification target | complete | traceability passed |
| DESIGN-REQ-004 | implemented_verified | active layered geometry and glow/trail tests cover visual model | complete | focused unit passed |
| DESIGN-REQ-005 | implemented_verified | border-ring mask tests cover inner inset and adjusted radius | complete | focused unit passed |
| DESIGN-REQ-006 | implemented_verified | beam footprint tests cover head/tail arcs and conic-gradient composition | complete | focused unit passed |
| DESIGN-REQ-011 | implemented_verified | layer/content separation tests cover declarative rendering rules and pseudostructure | complete | focused unit passed |

## Technical Context

**Language/Version**: TypeScript with React 19.2 
**Primary Dependencies**: React, existing Mission Control CSS, Vitest, Testing Library, PostCSS for CSS inspection 
**Storage**: N/A 
**Unit Testing**: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and final `./tools/test_unit.sh` 
**Integration Testing**: Component-level UI integration in Vitest by rendering the React surface around arbitrary child content; no service/database integration is required for this presentation-only runtime UI story 
**Target Platform**: Mission Control web UI 
**Project Type**: Frontend component/runtime UI 
**Performance Goals**: Keep animation on composited rotation/angle-style CSS with bounded decorative layers and modest glow blur 
**Constraints**: Border-only visibility, no content-area animation, no spinner/shimmer/completion treatment, no new package dependency 
**Scale/Scope**: One existing reusable component, one shared stylesheet, one focused test file, one verification report

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - frontend decoration only; no agent behavior replacement.
- II. One-Click Agent Deployment: PASS - no new services, secrets, or external prerequisites.
- III. Avoid Vendor Lock-In: PASS - provider-neutral UI effect.
- IV. Own Your Data: PASS - no data movement or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Design for Deletion / Thick Contracts: PASS - strengthens a compact replaceable component/CSS contract.
- VII. Powerful Runtime Configurability: PASS - geometry remains controlled through props and CSS variables.
- VIII. Modular and Extensible Architecture: PASS - isolated component refinements, no card-specific coupling.
- IX. Resilient by Default: PASS - no workflow or persisted payload changes.
- X. Facilitate Continuous Improvement: PASS - evidence captured in tests and verification.
- XI. Spec-Driven Development: PASS - plan follows the MM-466 spec.
- XII. Canonical Documentation Separation: PASS - temporary orchestration artifacts remain under `specs/` and `local-only handoffs`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases or hidden transforms.

## Project Structure

### Documentation (this feature)

```text
specs/235-masked-conic-beam-geometry-layers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── masked-conic-beam-geometry.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/components/
├── MaskedConicBorderBeam.tsx
└── MaskedConicBorderBeam.test.tsx

frontend/src/styles/
└── mission-control.css
```

**Structure Decision**: Extend the existing `MaskedConicBorderBeam` component, tests, and Mission Control stylesheet created by MM-465. Do not create a second component or adopt the effect on a specific Mission Control surface in this story.

## Complexity Tracking

No constitution violations.

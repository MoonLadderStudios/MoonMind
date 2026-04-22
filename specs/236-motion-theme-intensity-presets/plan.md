# Implementation Plan: Motion, Theme, and Intensity Presets

**Branch**: `236-motion-theme-intensity-presets` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/236-motion-theme-intensity-presets/spec.md`

## Summary

Implement MM-467 by completing tuning behavior on the existing `MaskedConicBorderBeam` runtime surface: preserve established border-only geometry while adding explicit variant presets, verifiable enter/exit timing tokens, theme/intensity token coverage, and MM-467 traceability. The current React component already exposes speed, theme, intensity, direction, trail, glow, and reduced-motion controls; this story adds focused Vitest coverage plus small CSS/component refinements for variant mapping and transition timing.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | default render test verifies 3.6s, clockwise, normal, neutral, soft trail, low glow, and auto reduced motion | complete | focused unit passed |
| FR-002 | implemented_verified | speed matrix test verifies slow/medium/fast map to 4.8s/3.6s/2.8s | complete | focused unit passed |
| FR-003 | implemented_verified | speed matrix test verifies numeric seconds and milliseconds string pass-through | complete | focused unit passed |
| FR-004 | implemented_verified | CSS contract tests verify linear orbit and counterclockwise reverse without speed override | complete | focused unit passed |
| FR-005 | implemented_verified | CSS contract tests verify border/head/tail/glow/opacity token roles | complete | focused unit passed |
| FR-006 | implemented_verified | component test verifies custom theme token pass-through; CSS tests verify neutral/brand/success mappings | complete | focused unit passed |
| FR-007 | implemented_verified | CSS contract tests verify subtle, normal, and vivid opacity token outcomes | complete | focused unit passed |
| FR-008 | implemented_verified | CSS contract tests verify 200ms enter and 140ms exit transition tokens | complete | focused unit passed |
| FR-009 | implemented_verified | component and CSS tests verify precision, energized, and dualPhase variant outcomes | complete | focused unit passed |
| FR-010 | implemented_verified | component tests verify precision is the default variant with soft trail and low glow | complete | focused unit passed |
| FR-011 | implemented_verified | component integration tests verify tuned content remains inside separate content wrapper and variants keep masked decorative layers | complete | focused unit passed |
| FR-012 | implemented_verified | traceability test verifies MM-467 and DESIGN-REQ-007/008/009/012 are exported | complete | focused unit passed |
| SC-001 | implemented_verified | default render test covers default tuning | complete | focused unit passed |
| SC-002 | implemented_verified | speed matrix test covers named and explicit durations | complete | focused unit passed |
| SC-003 | implemented_verified | CSS contract tests cover linear orbit, reverse direction, and speed invariants | complete | focused unit passed |
| SC-004 | implemented_verified | CSS contract tests cover enter/exit transition duration tokens | complete | focused unit passed |
| SC-005 | implemented_verified | component and CSS tests cover theme and intensity token mappings | complete | focused unit passed |
| SC-006 | implemented_verified | component and CSS tests cover precision, energized, and dualPhase variants | complete | focused unit passed |
| SC-007 | implemented_verified | spec, plan, tasks, traceability export, and verification preserve MM-467 | complete | focused unit passed |
| DESIGN-REQ-007 | implemented_verified | motion tests cover speed, direction, linear orbit, and transition timing | complete | focused unit passed |
| DESIGN-REQ-008 | implemented_verified | theme/intensity token tests cover color behavior roles | complete | focused unit passed |
| DESIGN-REQ-009 | implemented_verified | default tuning test covers recommended defaults | complete | focused unit passed |
| DESIGN-REQ-012 | implemented_verified | variant tests cover precision, energized, and dual-phase outcomes | complete | focused unit passed |
| DESIGN-REQ-011 | implemented_verified | component integration tests verify tuned variants preserve border-only/content separation | complete | focused unit passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, PostCSS test parsing, existing Mission Control stylesheet  
**Storage**: No new persistent storage  
**Unit Testing**: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and final `./tools/test_unit.sh`  
**Integration Testing**: Component-level integration in `frontend/src/components/MaskedConicBorderBeam.test.tsx`; required hermetic integration suite is not expected because this is isolated frontend visual behavior  
**Target Platform**: Mission Control web UI in modern browsers  
**Project Type**: Frontend component and stylesheet refinement  
**Performance Goals**: Preserve transform-based linear orbit animation and avoid layout-triggering animation  
**Constraints**: Preserve border-only mask and content readability; no new package or service dependency; no docs-only mode; no old compatibility aliases for internal contracts  
**Scale/Scope**: One reusable component and its focused CSS/test contract

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent orchestration logic changes.
- II. One-Click Agent Deployment: PASS - no deployment dependency changes.
- III. Avoid Vendor Lock-In: PASS - frontend component remains provider-neutral.
- IV. Own Your Data: PASS - no external data flow changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - tests define the visual contract.
- VII. Runtime Configurability: PASS - behavior is controlled through component props and CSS custom properties.
- VIII. Modular and Extensible Architecture: PASS - changes stay inside the existing component boundary.
- IX. Resilient by Default: PASS - no workflow/runtime contract changes.
- X. Facilitate Continuous Improvement: PASS - final verification artifact required.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification artifacts are created.
- XII. Canonical Documentation Separation: PASS - implementation notes stay under `specs/` and `docs/tmp`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shims; the component contract is updated directly.

## Project Structure

```text
specs/236-motion-theme-intensity-presets/
├── checklists/requirements.md
├── contracts/masked-conic-border-beam-presets.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
└── tasks.md

frontend/src/components/
├── MaskedConicBorderBeam.tsx
└── MaskedConicBorderBeam.test.tsx

frontend/src/styles/
└── mission-control.css
```

## Complexity Tracking

No constitution violations or added architectural complexity.

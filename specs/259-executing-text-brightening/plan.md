# Implementation Plan: Executing Text Brightening Sweep

**Branch**: `259-executing-text-brightening`  
**Date**: 2026-04-25  
**Spec**: `specs/259-executing-text-brightening/spec.md`  
**Input**: `specs/259-executing-text-brightening/spec.md`

## Summary

Add a task-list `ExecutionStatusPill` component that keeps status normalization in `executionStatusPillProps()`, renders non-executing states as plain text, and renders executing states as a parent status pill with an accessible label plus hidden per-grapheme visual spans. Replace the task-list table and card status span call sites with the component. Update Mission Control CSS so the existing executing physical sweep remains on the host and the new glyph spans run a CSS-only brightening pulse using the shared sweep duration token as the outer cycle, with a faster active text-sweep window and inactive tail. Validate with focused Vitest coverage, typecheck, lint, and the repo unit runner where available.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `docs/UI/EffectShimmerSweep.md`, `frontend/src/styles/mission-control.css`, `frontend/src/entrypoints/mission-control.test.tsx` | complete | CSS unit test passed |
| FR-002 | implemented_verified | `frontend/src/components/ExecutionStatusPill.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx` | complete | integration test passed |
| FR-003 | implemented_verified | CSS keyframes and no timer/state animation in `ExecutionStatusPill.tsx` | complete | typecheck + lint passed |
| FR-004 | implemented_verified | `.status-letter-wave__glyph` render assertions | complete | integration test passed |
| FR-005 | implemented_verified | `Intl.Segmenter` with `Array.from` fallback in component | complete | typecheck passed |
| FR-006 | implemented_verified | `--mm-executing-sweep-cycle-duration`, active-window ratio CSS, and per-glyph delay assertions | complete | CSS unit + integration tests passed |
| FR-007 | implemented_verified | `aria-label` and `aria-hidden` task-list assertions | complete | integration test passed |
| FR-008 | implemented_verified | reduced-motion glyph suppression assertions | complete | CSS unit test passed |
| FR-009 | implemented_verified | component delegates to `executionStatusPillProps()` | complete | integration test passed |
| FR-010 | implemented_verified | task-list table and card call sites use `ExecutionStatusPill` | complete | integration test passed |
| FR-011 | implemented_verified | non-executing task-list assertions remain plain | complete | integration test passed |
| DESIGN-REQ-001..008 | implemented_verified | `verification.md` maps all source requirements to implementation and tests | complete | unit + integration tests passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story.  
**Primary Dependencies**: React, existing `executionStatusPillProps`, Mission Control CSS, Vitest and Testing Library.  
**Storage**: No persistent storage.  
**Unit Testing Tool**: Vitest through `npm run ui:test` or `./tools/test_unit.sh --ui-args ...`.  
**Integration Testing Tool**: Vitest/Testing Library task-list render tests.  
**Target Platform**: Browser Mission Control task list.  
**Project Type**: Frontend UI component and stylesheet update.  
**Performance Goals**: No JavaScript animation loop; CSS animation should scale across many task rows.  
**Constraints**: Preserve existing status source precedence, non-executing state isolation, reduced-motion behavior, and centralized status metadata.  
**Scale/Scope**: One small component, two task-list render call sites, shared stylesheet, focused frontend tests, and MoonSpec artifacts.

## Test Strategy

**Unit Strategy**: Mission Control CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` verify the host physical sweep remains, glyph-wave styles use the shared duration, brightening keyframes are present, and reduced-motion disables glyph animation, shadow, and filter.

**Integration Strategy**: Task-list render tests in `frontend/src/entrypoints/tasks-list.test.tsx` verify table and card executing pills receive the glyph layer, accessible parent label, hidden visual spans, per-glyph delays, and existing executing metadata while non-executing statuses remain plain.

**Final Validation Strategy**: Run the focused UI tests, the full frontend Vitest suite, TypeScript typecheck, ESLint, and `./tools/test_unit.sh` where feasible. In this managed workspace, local binaries under `./node_modules/.bin` are the reliable equivalent for `npm run` commands when colon-containing paths break npm's PATH-based bin lookup.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - UI-only change does not alter agent orchestration.
- II One-Click Agent Deployment: PASS - no deployment prerequisites added.
- III Avoid Vendor Lock-In: PASS - no provider coupling.
- IV Own Your Data: PASS - no data flow changes.
- V Skills Are First-Class: PASS - no executable skill contract changes.
- VI Replaceability and Tests: PASS - behavior is covered by focused tests and remains isolated.
- VII Runtime Configurability: PASS - uses existing CSS tokens rather than hardcoded runtime behavior.
- VIII Modular Architecture: PASS - adds a small component at the UI boundary.
- IX Resilient by Default: PASS - no workflow/activity contract changes.
- X Continuous Improvement: PASS - spec, tasks, verification evidence are recorded.
- XI Spec-Driven Development: PASS - spec, plan, tasks, and verification artifacts accompany implementation.
- XII Canonical Documentation Separation: PASS - implementation notes stay in feature artifacts, not canonical docs.
- XIII Pre-Release Compatibility: PASS - no compatibility aliases or internal contract shims.

## Project Structure

```text
frontend/src/components/ExecutionStatusPill.tsx
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/entrypoints/mission-control.test.tsx
frontend/src/styles/mission-control.css
specs/259-executing-text-brightening/
```

## Complexity Tracking

No constitution violations or complexity exceptions.

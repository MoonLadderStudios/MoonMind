# Implementation Plan: Mission Control Shared Interaction Language

**Branch**: `run-jira-orchestrate-for-mm-427-align-mi-00e0a46d` | **Date**: 2026-04-21 | **Spec**: `specs/218-mission-control-interaction-language/spec.md`  
**Input**: Single-story feature specification from `specs/218-mission-control-interaction-language/spec.md`

## Summary

Implement MM-427 by aligning Mission Control's routine controls with the interaction rules in `docs/UI/MissionControlDesignSystem.md`. Repo inspection shows shared tokens and prior visual/layout work exist, but button hover rules still use translate-based lift and compact utility controls/chips use scattered one-off styling. The implementation keeps behavior unchanged while adding interaction tokens, wiring primary/secondary/action/icon/button-link controls to scale-based glow/grow states, and giving inline toggles, filters, and filter chips a shared compact-control shell.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | no `--mm-control-*` interaction token contract exists | add interaction/focus/disabled tokens to shared CSS | UI unit / CSS |
| FR-002 | partial | shared button and icon hover rules exist but use `translateY` lift | replace routine lift with tokenized scale-only hover/press | UI unit / CSS |
| FR-003 | partial | `.button` mirrors some button styling but keeps duplicate one-off motion | align `.button` with shared interaction tokens | UI unit / CSS |
| FR-004 | partial | `.queue-inline-toggle` and `.queue-inline-filter` are layout-only shells | add shared compact-control shell, hover, focus, disabled posture | UI unit / CSS |
| FR-005 | partial | filter chips exist and wrap values but use standalone styling | move chips onto compact-control tokens while preserving wrapping | UI unit / CSS |
| FR-006 | partial | focus-visible styles exist but repeat direct colors | use shared focus ring token for buttons, button links, fields, and icon controls | UI unit / CSS |
| FR-007 | partial | disabled rules exist for some controls only and use one-off opacity | add shared disabled opacity and suppress motion/glow broadly | UI unit / CSS |
| FR-008 | partial | reduced-motion rules exist for some features but not shared control scale | add reduced-motion control interaction guard | UI unit / CSS |
| FR-009 | implemented_unverified | existing app-shell and task-list tests cover behavior | rerun focused tests after CSS changes | UI unit |
| FR-010 | missing | no MM-427-specific interaction tests | add CSS contract assertions | UI unit / CSS |
| FR-011 | verified | `spec.md` preserves MM-427 and the trusted Jira preset brief | preserve through tasks, verification, and commit/PR metadata when those outputs are requested | final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control tests; CSS for shared Mission Control styling  
**Primary Dependencies**: React, Vite/Vitest, existing Mission Control shared stylesheet  
**Storage**: No new persistent storage  
**Data Model**: Not required; this story changes shared UI interaction styling and exposes no new persisted data or state transition model  
**Unit Testing**: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx`; direct `./node_modules/.bin/vitest` if the npm script cannot resolve `vitest`; final wrapper via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration Testing**: Existing UI render tests exercise shared app shell and task-list request behavior; no compose-backed integration is required because backend contracts are unchanged  
**Target Platform**: Browser-hosted Mission Control UI served by FastAPI  
**Project Type**: Web UI design-system story  
**Performance Goals**: Keep interaction effects CSS-only; avoid new JavaScript, network calls, or heavy rendering effects  
**Constraints**: Preserve task-list request parameters, sorting, pagination, mobile cards, Create page payload behavior, and route ownership  
**Scale/Scope**: Shared Mission Control stylesheet, shared app-shell tests, task-list regression tests, MoonSpec artifacts

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story uses the existing Jira Orchestrate/MoonSpec lifecycle and changes only the shared UI layer.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. No provider-specific dependency is introduced.
- IV. Own Your Data: PASS. No external data movement or browser-direct provider calls.
- V. Skills Are First-Class and Easy to Add: PASS. Skill runtime and materialization paths are untouched.
- VI. Replaceable Scaffolding: PASS. The work is token/CSS based and covered by focused contract tests.
- VII. Runtime Configurability: PASS. Existing runtime config behavior remains unchanged.
- VIII. Modular Architecture: PASS. Changes stay in shared Mission Control CSS and tests.
- IX. Resilient by Default: PASS. No workflow or side-effect contract changes; existing behavior tests continue to run.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Implementation proceeds from this single-story spec.
- XII. Documentation Separation: PASS. Desired-state docs remain canonical; runtime traceability input stays under `docs/tmp`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden fallback contract is introduced.

## Project Structure

```text
specs/218-mission-control-interaction-language/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── interaction-language.md
├── tasks.md
└── verification.md

frontend/src/
├── entrypoints/mission-control.test.tsx
├── entrypoints/tasks-list.test.tsx
└── styles/mission-control.css

docs/tmp/jira-orchestration-inputs/
└── MM-427-moonspec-orchestration-input.md
```

**Structure Decision**: Keep runtime changes in the shared stylesheet and validate the contract from existing Mission Control UI test files. Do not add a new component abstraction until repeated consumers need stronger typed structure.

## Complexity Tracking

No constitution violations.

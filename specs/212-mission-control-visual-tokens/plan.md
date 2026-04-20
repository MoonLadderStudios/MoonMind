# Implementation Plan: Mission Control Visual Tokens and Atmosphere

**Branch**: `run-jira-orchestrate-for-mm-424-establis-342df6cf` | **Date**: 2026-04-20 | **Spec**: `specs/212-mission-control-visual-tokens/spec.md`  
**Input**: Single-story feature specification from `specs/212-mission-control-visual-tokens/spec.md`

## Summary

Establish MM-424 by promoting Mission Control's atmospheric and glass styling into explicit reusable CSS tokens, then consume those tokens in the shared application background and chrome. Repo inspection shows `docs/UI/MissionControlDesignSystem.md` already defines the desired product expression and core token table, while `frontend/src/styles/mission-control.css` currently has core `--mm-*` tokens plus direct gradient and glass values. The implementation keeps the work scoped to the shared stylesheet and shared entry tests: add token names for atmosphere layers, glass surfaces, input wells, and elevation, wire body, masthead, panel, and floating rail styling to those tokens, then add focused CSS contract tests without changing route/runtime behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | core tokens exist in `mission-control.css`; atmosphere/glass tokens are implicit | add explicit atmosphere, glass, input, and elevation tokens | unit UI |
| FR-002 | partial | `.dark` overrides core tokens only | add matching dark overrides for new token names | unit UI |
| FR-003 | partial | body has layered gradients with direct semantic-token alpha usage | route body gradient through atmosphere layer tokens | unit UI |
| FR-004 | partial | masthead/panel/floating bar use a mix of semantic tokens and one-off alpha values | consume shared surface/elevation tokens for chrome posture | unit UI |
| FR-005 | implemented_unverified | existing text and border semantic tokens remain in use | preserve text/border token usage and add contract coverage | unit UI |
| FR-006 | implemented_unverified | planned surface is CSS-only | rerun shared app-shell tests to prove behavior unchanged | unit UI |
| FR-007 | missing | no MM-424-specific tests | add CSS contract tests in `mission-control.test.tsx` | unit UI |
| FR-008 | implemented_unverified | `spec.md` preserves MM-424 and the supplied brief | preserve through tasks, verification, commit, and final report | final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control tests; CSS for shared Mission Control styling  
**Primary Dependencies**: React, Vite/Vitest, existing Mission Control shared app shell, existing shared stylesheet  
**Storage**: No new persistent storage  
**Unit Testing**: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx` for focused validation; `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx` when final wrapper validation is feasible  
**Integration Testing**: Shared app-shell rendering in `frontend/src/entrypoints/mission-control.test.tsx`; no compose-backed integration is required for this CSS-only design-system story  
**Target Platform**: Browser-hosted Mission Control UI served by FastAPI  
**Project Type**: Web UI design-system foundation  
**Performance Goals**: Avoid adding JavaScript, network calls, heavy runtime effects, or additional rendering loops  
**Constraints**: Preserve existing routes, lazy-loaded pages, dashboard alerts, task payload semantics, and tokenized readability  
**Scale/Scope**: One shared CSS file, one shared app-shell test file, MoonSpec artifacts, and traceability input under `docs/tmp`

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The Jira Orchestrate-selected story runs through existing MoonSpec artifacts and changes only the shared UI layer.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. No provider-specific code or third-party runtime dependency is introduced.
- IV. Own Your Data: PASS. No external data movement or browser-direct provider calls.
- V. Skills Are First-Class and Easy to Add: PASS. Skill contracts and runtime materialization are untouched.
- VI. Replaceable Scaffolding: PASS. The work creates a small token contract with focused tests.
- VII. Runtime Configurability: PASS. Existing runtime configuration behavior remains unchanged.
- VIII. Modular Architecture: PASS. Changes stay in shared Mission Control CSS and tests.
- IX. Resilient by Default: PASS. CSS-only change has no workflow side effects and is covered by app-shell regression tests.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Implementation proceeds from this single-story spec.
- XII. Documentation Separation: PASS. Desired-state docs remain canonical; runtime traceability input stays under `docs/tmp`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden fallbacks are introduced.

## Project Structure

```text
specs/212-mission-control-visual-tokens/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── visual-token-contract.md
├── tasks.md
└── verification.md

frontend/src/
├── entrypoints/mission-control.test.tsx
└── styles/mission-control.css

docs/tmp/jira-orchestration-inputs/
└── MM-424-moonspec-orchestration-input.md
```

**Structure Decision**: Keep runtime changes in the shared stylesheet and validate the contract from the existing shared Mission Control test file. Do not add a separate design-token build step or runtime provider.

## Complexity Tracking

No constitution violations.

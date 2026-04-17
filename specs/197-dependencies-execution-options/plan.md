# Implementation Plan: Dependencies and Execution Options

**Branch**: `197-dependencies-execution-options` | **Date**: 2026-04-17 | **Spec**: `specs/197-dependencies-execution-options/spec.md`  
**Input**: Single-story feature specification from `specs/197-dependencies-execution-options/spec.md`

## Summary

Implement MM-379 by hardening the existing Create page dependency picker and execution context controls around bounded run dependencies, runtime-specific provider profiles, PR-only merge automation, resolver-style publish restrictions, and validation invariants that Jira import or image upload must not bypass. The technical approach is to extend the focused Create page Vitest coverage first, then patch the existing React entrypoint only where the tests expose gaps.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI  
**Primary Dependencies**: React, existing Create page entrypoint, TanStack Query, existing MoonMind REST endpoints for executions, provider profiles, task templates, Jira import, and artifact uploads; Vitest and Testing Library for validation  
**Storage**: Existing execution payload storage and artifact metadata only; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Focused Create page request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` using mocked MoonMind REST endpoints; no compose-backed integration dependency is required for this UI state story  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web UI  
**Performance Goals**: Dependency filtering, runtime/profile option updates, and publish-mode visibility changes remain immediate for ordinary drafts and at most 10 selected dependencies  
**Constraints**: Preserve server-provided runtime defaults, keep dependency selection independent from Jira/images/presets, reject duplicate and over-limit dependencies, preserve repository and publish validation, and preserve Jira issue key MM-379 in artifacts  
**Scale/Scope**: One Create page entrypoint and focused tests covering dependency and execution option behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing MoonMind task submission and runtime configuration surfaces.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Runtime options remain MoonMind configuration, not provider-specific hardcoding.
- IV. Own Your Data: PASS. Dependencies and image inputs remain MoonMind execution/artifact references.
- V. Skills Are First-Class and Easy to Add: PASS. Resolver-style skill restrictions remain explicit and skill-based.
- VI. Replaceable Scaffolding: PASS. Adds focused UI contract tests without new orchestration scaffolding.
- VII. Runtime Configurability: PASS. Runtime defaults and provider profiles come from server-provided configuration.
- VIII. Modular Architecture: PASS. Work stays in the existing Create page entrypoint and tests.
- IX. Resilient by Default: PASS. Dependency fetch failures are recoverable and do not discard draft state.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in `verification.md`.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story Moon Spec.
- XII. Canonical Documentation Separation: PASS. Runtime implementation artifacts live under `specs/` and source; canonical docs are unchanged.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden fallback semantics are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/197-dependencies-execution-options/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-dependencies-execution-options.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-379-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Create page implementation surface. Extend local state validation, payload normalization, and focused tests in the existing entrypoint instead of introducing a new module for this narrow UI contract story.

## Complexity Tracking

No constitution violations.

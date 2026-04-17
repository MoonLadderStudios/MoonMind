# Implementation Plan: Preset Application and Reapply State

**Branch**: `196-preset-application-reapply-state` | **Date**: 2026-04-17 | **Spec**: `specs/196-preset-application-reapply-state/spec.md`  
**Input**: Single-story feature specification from `specs/196-preset-application-reapply-state/spec.md`

## Summary

Implement MM-378 by tightening the existing Create page preset state model around explicit Apply/Reapply behavior, objective-scoped attachments, and template-bound step detachment. The technical approach is to extend the existing React draft state and focused Vitest coverage without introducing new backend storage or service dependencies.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI  
**Primary Dependencies**: React, existing Create page entrypoint, existing task template catalog endpoints, existing artifact upload/link helpers, Vitest, Testing Library  
**Storage**: No new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Focused UI request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` validate task payloads and artifact attachment behavior through mocked MoonMind REST endpoints; no compose-backed integration dependency is required for this UI state story  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web UI  
**Performance Goals**: No additional preset or artifact network requests except when the user explicitly applies a preset, imports Jira images, selects files, or submits the draft  
**Constraints**: Preserve explicit Apply/Reapply semantics, preserve manual step customizations, keep attachments structured rather than embedded in instruction text, keep browser calls behind MoonMind REST endpoints, and preserve Jira issue key MM-378 in artifacts  
**Scale/Scope**: One Create page entrypoint and its focused tests

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing MoonMind preset and task orchestration surfaces.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment requirements.
- III. Avoid Vendor Lock-In: PASS. UI continues to use MoonMind REST surfaces rather than direct provider APIs.
- IV. Own Your Data: PASS. Attachments remain MoonMind artifacts and task draft state.
- V. Skills Are First-Class and Easy to Add: PASS. Preset behavior remains template-driven and skill-compatible.
- VI. Replaceable Scaffolding: PASS. Adds focused tests around the UI contract rather than new orchestration scaffolding.
- VII. Runtime Configurability: PASS. Attachment availability remains governed by server-provided runtime policy.
- VIII. Modular Architecture: PASS. Changes stay in the existing Create page entrypoint and tests.
- IX. Resilient by Default: PASS. Manual customizations are preserved and optional integrations remain explicit.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in `verification.md`.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story Moon Spec.
- XII. Canonical Documentation Separation: PASS. Runtime work stays in specs and source; canonical docs are unchanged.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility alias or silent transform is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/196-preset-application-reapply-state/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-preset-state.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx
```

**Structure Decision**: Preserve the existing Create page implementation surface. Extend local draft state, payload normalization, and tests in the existing entrypoint instead of introducing a new state module for this narrowly scoped story.

## Complexity Tracking

No constitution violations.

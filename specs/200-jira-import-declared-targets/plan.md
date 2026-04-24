# Implementation Plan: Jira Import Into Declared Targets

**Branch**: `mm-381-a453f798` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md) 
**Input**: Single-story feature specification from `specs/200-jira-import-declared-targets/spec.md`

## Summary

Implement MM-381 by tightening the Create page Jira browser around declared import targets. The approach uses the existing MoonMind Jira browser API, Create page draft state, attachment policy, artifact upload path, preset reapply tracking, and template-bound step detection. Validation focuses on Vitest coverage in the Create page harness plus final repository unit validation.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains relevant for final repository tests but is not expected to change for this story 
**Primary Dependencies**: React, Vite/Vitest, Testing Library, existing FastAPI Jira browser and artifact APIs 
**Storage**: Existing browser draft state, artifact metadata, and execution task snapshots only; no new persistent storage 
**Unit Testing**: Vitest through `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and final `./tools/test_unit.sh` 
**Integration Testing**: Existing Create page browser-to-API test harness in `frontend/src/entrypoints/task-create.test.tsx`; no Docker-backed integration is planned for this UI story 
**Target Platform**: Mission Control browser UI served by FastAPI 
**Project Type**: Web application UI backed by existing API contracts 
**Performance Goals**: Jira target switching and local draft updates remain immediate for ordinary drafts and issue details 
**Constraints**: Browser code must use MoonMind-owned Jira endpoints only; imported images must remain target-bound structured attachment candidates; Jira import must not create tasks or bypass create/edit/rerun validation 
**Scale/Scope**: One Create page story covering declared Jira import targets, text append/replace, image target mapping, template detachment, preset reapply signaling, and Jira failure isolation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Jira remains an input source for MoonMind task authoring rather than a replacement workflow.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Browser Jira access remains behind MoonMind APIs and imported images become MoonMind artifact inputs.
- **IV. Own Your Data**: PASS. Imported Jira text and images become local draft state and MoonMind artifact refs.
- **V. Skills Are First-Class and Easy to Add**: PASS. Task presets and skills remain compatible with existing Create page behavior.
- **VII. Powerful Runtime Configurability**: PASS. Jira and attachment entry points remain gated by server-provided runtime configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the existing Create page entrypoint and tests.
- **IX. Resilient by Default**: PASS. Jira errors remain local and do not corrupt the draft or submit partial data.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. MM-381 input is preserved in spec artifacts and tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime implementation artifacts live under `specs/` and `local-only handoffs`; canonical docs are source requirements.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or hidden Jira/attachment retargeting are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/200-jira-import-declared-targets/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── create-page-jira-import-targets.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

docs/UI/
└── CreatePage.md

└── MM-381-moonspec-orchestration-input.md
```

**Structure Decision**: Use the existing Mission Control Create page entrypoint and colocated Vitest coverage. Backend Jira browser and artifact APIs already expose normalized issue details and upload refs, so backend code changes are not planned unless tests expose a contract gap.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

## Managed Setup Note

`.specify/scripts/bash/setup-plan.sh --json` is not used for this managed branch because the helper expects a branch like `001-feature-name`. Planning uses `.specify/feature.json` and direct artifact inspection for `specs/200-jira-import-declared-targets`.

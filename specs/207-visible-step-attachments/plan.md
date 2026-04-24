# Implementation Plan: Visible Step Attachments

**Branch**: `207-visible-step-attachments` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime feature specification from `specs/207-visible-step-attachments/spec.md`

## Summary

Implement MM-410 by replacing the current visible generic step file input with a compact per-step + attachment button, preserving the existing artifact-backed Create-page attachment pipeline, and changing step file selection to append/dedupe rather than replace. Repo gap analysis shows most attachment ownership, preview, validation, retry/remove, upload-before-submit, and payload behavior already exists in `frontend/src/entrypoints/task-create.tsx` and its Vitest coverage; the missing runtime behavior is the visible + affordance, append semantics, exact-duplicate dedupe, and tests for image-only/mixed-content copy and the new accessible control.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | policy-gated rendering in `task-create.tsx`; existing disabled-policy tests | preserve behavior | final regression |
| FR-002 | partial | step controls render as visible `input[type=file]`, not compact + button | add accessible + control and hidden input | unit + integration-style UI |
| FR-003 | partial | current file picker is step-scoped and preserves accept filtering | preserve scope while changing affordance | unit |
| FR-004 | missing | `updateStepAttachments` replaces selected files | add append/dedupe helper | unit |
| FR-005 | implemented_unverified | selected files are keyed by step `localId`; existing reorder tests cover payload | add MM-410 coverage for new control path | integration-style UI |
| FR-006 | implemented_unverified | selected/persisted attachment summaries already render under steps | verify no regression with new control | unit |
| FR-007 | implemented_unverified | target-specific errors and preview failures exist | verify new control uses same paths | unit |
| FR-008 | implemented_unverified | remove failed/invalid actions exist | verify unrelated state preserved after append/remove path | unit |
| FR-009 | implemented_unverified | submit blocks invalid/uploading and uploads before create | preserve behavior | integration-style UI |
| FR-010 | implemented_unverified | payload refs use `task.steps[n].inputAttachments`; no markdown insertion | preserve behavior | integration-style UI |
| FR-011 | implemented_unverified | persisted refs render and removal serializes empty lists | preserve behavior | unit/integration-style UI |
| FR-012 | missing | no tests for compact + button, image/mixed copy, append/dedupe | add focused tests | unit + integration-style UI |
| DESIGN-REQ-001 | implemented_unverified | step local IDs own attachment arrays | preserve and verify | integration-style UI |
| DESIGN-REQ-002 | partial | label exists but generic input is visible | add correct affordance/copy | unit |
| DESIGN-REQ-003 | implemented_unverified | summaries, remove, preview/error state exist | preserve and verify | unit |
| DESIGN-REQ-004 | implemented_unverified | objective and step refs are separate | preserve behavior | final regression |
| DESIGN-REQ-005 | partial | policy and validation exist; copy/affordance incomplete | add copy and control tests | unit |
| DESIGN-REQ-006 | implemented_unverified | artifact-first submit path exists | preserve behavior | integration-style UI |
| DESIGN-REQ-007 | implemented_unverified | failure behavior exists | preserve behavior | unit |
| DESIGN-REQ-008 | partial | remove/retry accessible; open action is generic input label | add accessible + open action | unit |
| DESIGN-REQ-009 | partial | many tests exist; missing new affordance/append cases | add tests | unit + integration-style UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 only for existing backend suite if final unit runner reaches Python tests
**Primary Dependencies**: React, Vite/Vitest, Testing Library, existing FastAPI execution/artifact APIs
**Storage**: Existing artifact metadata and execution payload snapshots only; no new persistent storage
**Unit Testing**: Vitest focused on `frontend/src/entrypoints/task-create.test.tsx`
**Integration Testing**: Existing Create-page test harness exercises browser upload and `/api/executions` payload behavior without Docker-backed services
**Target Platform**: Mission Control browser UI
**Project Type**: Web application UI with existing API contracts
**Performance Goals**: File append, dedupe, validation, and render updates remain immediate within configured attachment limits
**Constraints**: Runtime policy controls visibility and limits; no raw binary/base64/data URLs in execution payloads; target identity must not derive from filenames
**Scale/Scope**: One Create-page story covering step-scoped visible attachment controls and append behavior

## Constitution Check

*GATE: PASS before Phase 0 research. PASS after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change preserves existing MoonMind control-plane and runtime adapter contracts.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, or deployment prerequisites.
- **III. Avoid Vendor Lock-In**: PASS. Attachments remain MoonMind artifact refs, not provider-specific payloads.
- **IV. Own Your Data**: PASS. File bytes stay in MoonMind artifact storage and refs stay local to task payloads.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill system changes.
- **VII. Powerful Runtime Configurability**: PASS. Existing server-provided attachment policy remains authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the Create page entrypoint, styles, and tests.
- **IX. Resilient by Default**: PASS. Invalid, failed, incomplete, or uploading attachments continue to block create/edit/rerun.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. MM-410 is preserved in spec artifacts and planned tests.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime artifacts live under `specs/` and local-only handoffs.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or fallback contract changes.

## Project Structure

### Documentation (this feature)

```text
specs/207-visible-step-attachments/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── create-page-visible-step-attachments.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

frontend/src/styles/
└── mission-control.css

└── MM-410-moonspec-orchestration-input.md
```

**Structure Decision**: Use the existing Create page entrypoint and colocated Vitest coverage. No backend schema, artifact API, execution API, or worker materialization change is planned.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

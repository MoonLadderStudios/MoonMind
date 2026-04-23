# Implementation Plan: Surface Canonical Reports in Mission Control

**Branch**: `run-jira-orchestrate-for-mm-462-mission-67b1d4e7` | **Date**: 2026-04-22 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/230-mission-control-report-presentation/spec.md`

## Summary

The existing Mission Control report-presentation implementation already satisfies the MM-494 runtime story. This resumed plan preserves MM-494 as the canonical Jira source brief, reuses the verified implementation in `frontend/src/entrypoints/task-detail.tsx` and its tests, and limits current work to source-traceability alignment rather than reopening implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx` queries `link_type=report.primary&latest_only=true`; `tests/contract/test_temporal_artifact_api.py` verifies the existing artifact endpoint contract. | No additional plan work; preserve existing verified behavior. | unit + contract |
| FR-002 | implemented_verified | `ReportPresentationSection` renders before Timeline and Artifacts; `frontend/src/entrypoints/task-detail.test.tsx` asserts Report appears before Artifacts. | No additional plan work; preserve existing verified behavior. | frontend unit |
| FR-003 | implemented_verified | Frontend artifact normalization preserves links and renders related report content with open actions. | No additional plan work; preserve existing verified behavior. | frontend unit |
| FR-004 | implemented_verified | Generic Artifacts and observability surfaces remain rendered; fallback tests verify generic artifacts still show without report fabrication. | No additional plan work; preserve existing verified behavior. | frontend unit |
| FR-005 | implemented_verified | `reportOpenHref` and `reportViewerLabel` honor `default_read_ref`, `download_url`, `render_hint`, `content_type`, and metadata title/name. | No additional plan work; preserve existing verified behavior. | frontend unit |
| FR-006 | implemented_verified | Implementation consumes the existing artifact endpoint/read model only; no new storage or mutation route was added. | No additional plan work; preserve existing verified behavior. | unit + contract |
| FR-007 | implemented_verified | Report section renders only when latest report response contains an actual `report.primary` link. | No additional plan work; preserve existing verified behavior. | frontend unit |
| FR-008 | implemented_verified | MM-494 is preserved in `spec.md`, `plan.md`, `tasks.md`, `quickstart.md`, `verification.md`, and the orchestration input. | No additional plan work; preserve traceability across resumed artifacts. | traceability check |
| DESIGN-REQ-011 | implemented_verified | Latest report query remains server-side through `link_type=report.primary&latest_only=true`. | No additional plan work; preserve existing verified behavior. | frontend unit |
| DESIGN-REQ-012 | implemented_verified | Report section includes canonical report and related report content while preserving generic artifacts and observability. | No additional plan work; preserve existing verified behavior. | frontend unit |
| DESIGN-REQ-013 | implemented_verified | Viewer/open helpers honor artifact presentation fields. | No additional plan work; preserve existing verified behavior. | frontend unit |
| DESIGN-REQ-014 | implemented_verified | Report-first UI appears before generic artifact inspection. | No additional plan work; preserve existing verified behavior. | frontend unit |
| DESIGN-REQ-020 | implemented_verified | Related evidence remains individually openable and generic observability remains separate. | No additional plan work; preserve existing verified behavior. | frontend unit |
| DESIGN-REQ-022 | implemented_verified | Existing artifact endpoint remains a read model over artifacts; no report-specific storage plane was introduced. | No additional plan work; preserve existing verified behavior. | contract |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for API contract regression  
**Primary Dependencies**: React, TanStack Query, Zod, existing FastAPI artifact routes, Pydantic v2 response models  
**Storage**: Existing temporal artifact tables and artifact store only; no new persistent storage  
**Unit Testing**: Focused frontend unit coverage through `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx`; API contract coverage through `./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py`; final full unit verification through `./tools/test_unit.sh`  
**Integration Testing**: Existing required hermetic integration runner `./tools/test_integration.sh`; no compose-backed integration is required for this story because the planned backend coverage is a contract regression over an existing read-only endpoint, but the integration runner remains the escalation path if artifact service behavior changes beyond serialization/query plumbing  
**Target Platform**: Mission Control browser UI backed by MoonMind API service  
**Project Type**: Frontend application with existing backend API read model  
**Performance Goals**: Add one focused latest-report artifact request and reuse the existing artifact list request; avoid client-side sorting over arbitrary artifact collections  
**Constraints**: Do not fabricate report status locally; do not hide generic artifact or observability surfaces; keep report identity server-driven  
**Scale/Scope**: One runtime story for task detail report presentation of one execution

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Uses existing artifact/read model APIs.
- II. One-Click Agent Deployment: PASS. No new service or external dependency.
- III. Avoid Vendor Lock-In: PASS. Report presentation is provider-neutral artifact metadata.
- IV. Own Your Data: PASS. Reports remain artifact-backed in operator-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime changes.
- VI. Replaceable AI Scaffolding: PASS. UI consumes stable artifact contracts instead of provider prompts.
- VII. Runtime Configurability: PASS. Uses existing configured API endpoints.
- VIII. Modular and Extensible Architecture: PASS. Changes stay in task detail UI and artifact API contract.
- IX. Resilient by Default: PASS. Report identity comes from durable artifact links, not browser heuristics.
- X. Facilitate Continuous Improvement: PASS. Verification preserves traceable MM-494 evidence for the resumed feature artifacts.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification drive work.
- XII. Canonical Documentation Separation: PASS. Orchestration input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or internal semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/230-mission-control-report-presentation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── mission-control-report-presentation.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

api_service/api/routers/
└── temporal_artifacts.py

tests/contract/
└── test_temporal_artifact_api.py
```

**Structure Decision**: Implement report presentation in the existing task detail entrypoint, use the existing execution artifact endpoint for `report.primary` latest lookup and related artifact reads, and add focused tests in the existing task-detail and artifact API suites.

## Complexity Tracking

No constitution violations.

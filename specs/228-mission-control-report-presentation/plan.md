# Implementation Plan: Mission Control Report Presentation

**Branch**: `run-jira-orchestrate-for-mm-462-mission-67b1d4e7` | **Date**: 2026-04-22 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/228-mission-control-report-presentation/spec.md`

## Summary

Implement MM-462 by extending Mission Control's task detail surface to query the server for the latest `report.primary` artifact, present that canonical report before the generic artifact list, show related report summary/structured/evidence artifacts as report content, and choose open/download targets from artifact presentation metadata. Backend artifact APIs already expose execution-scoped filtering with `link_type` and `latest_only`; the primary implementation work is frontend parsing, report presentation, and focused UI tests with an API contract regression.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `api_service/api/routers/temporal_artifacts.py` accepts `link_type` and `latest_only`; `TemporalArtifactService.list_for_execution` uses `latest_for_execution_link`. | Use this query from Mission Control and add contract/UI coverage. | unit + contract |
| FR-002 | missing | `frontend/src/entrypoints/task-detail.tsx` shows Summary, Steps, Timeline, then Artifacts; no report-first panel exists. | Add report-first panel before Timeline/Artifacts. | frontend unit |
| FR-003 | partial | Artifact list payload includes links/metadata, but frontend schema discards links/default read refs and does not group report content. | Parse links/default read refs and render related report content. | frontend unit |
| FR-004 | implemented_unverified | Existing sections preserve Artifacts, Timeline, stdout/stderr/diagnostics, run summary, and session panels. | Ensure report panel is additive and tests assert generic artifacts remain visible. | frontend unit |
| FR-005 | partial | `artifactDownloadHref` uses download URL or artifact ID only; no report viewer target helper reads `default_read_ref`, `render_hint`, or content metadata. | Add viewer target/label helper based on artifact presentation fields. | frontend unit |
| FR-006 | implemented_unverified | Artifact list endpoint serializes metadata and links from normal artifact rows; no separate report store exists. | Keep report UI as read model over existing artifact responses. | contract + frontend unit |
| FR-007 | missing | UI currently fetches all artifacts and would need local filtering for report identity. | Query `link_type=report.primary&latest_only=true`; do not infer canonical report from arbitrary artifacts. | frontend unit |
| FR-008 | implemented_unverified | MM-462 preserved in `spec.md` and orchestration input. | Preserve through plan, tasks, verification, and code/test names where practical. | traceability check |
| DESIGN-REQ-011 | implemented_unverified | Server latest-report query exists for execution/link type. | Consume latest query from UI and test query URL. | frontend unit |
| DESIGN-REQ-012 | partial | Generic surfaces exist; report panel and related evidence section do not. | Add report panel and related content. | frontend unit |
| DESIGN-REQ-013 | partial | API serializes `default_read_ref`; UI does not parse/use it. | Parse presentation fields and choose open target/label. | frontend unit |
| DESIGN-REQ-014 | missing | No report-first UX in task detail. | Render report before generic artifacts. | frontend unit |
| DESIGN-REQ-020 | partial | Generic observability surfaces exist; evidence is not report-related. | Keep evidence individually openable while preserving observability sections. | frontend unit |
| DESIGN-REQ-022 | implemented_unverified | Existing endpoint is a read model over artifacts; optional projection not needed for this slice. | Use existing endpoint only. | contract |

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
- X. Facilitate Continuous Improvement: PASS. Verification will produce traceable MM-462 evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification drive work.
- XII. Canonical Documentation Separation: PASS. Orchestration input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or internal semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/228-mission-control-report-presentation/
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

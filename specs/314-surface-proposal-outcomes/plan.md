# Implementation Plan: Surface Proposal Outcomes

**Branch**: `314-surface-proposal-outcomes` | **Date**: 2026-05-07 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:0c314657-113e-4d5e-951f-7149150d8b9e/repo/specs/314-surface-proposal-outcomes/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `change-jira-issue-mm-600-to-status-in-pr-39787ea8` because it is not in `001-feature-name` form. Planning continued manually from `.specify/feature.json` and the active feature directory.

## Summary

Deliver MM-600 by turning existing proposal-generation, proposal-delivery, and provider-decision records into operator-visible finish-summary, exported-summary, API, execution-detail, and Mission Control outcome surfaces. Current code already enters a `proposals` workflow state, records generated/submitted counts and errors in Temporal and Codex finish summaries, exposes proposal delivery-record fields through `/api/proposals`, computes dedup identities, records provider decision metadata and promoted execution IDs, and maps `proposals` to running for dashboard compatibility. The missing behavior is the complete visibility contract: delivered counts, issue links, dedup new-or-updated status, provider-specific failures, redacted validation errors, compact task summaries, promotion links, and proof that GitHub/Jira remain the normal proposal review path instead of a standalone MoonMind queue. The plan uses TDD with focused unit coverage for summary assembly, proposal-service serialization, API schemas, and UI rendering, plus integration/boundary coverage for a proposal-capable run that produces delivered, duplicate, malformed, failed-delivery, and promoted outcomes.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/temporal/workflows/run.py` and `moonmind/agents/codex_worker/worker.py` include proposal `requested` in finish summaries | keep requested visibility and add coverage across Temporal/Codex summary paths | unit + integration |
| FR-002 | partial | summaries include generated/submitted counts; delivered count is absent from both inspected summary builders | add delivered count to proposal-stage result and summaries | unit + integration |
| FR-003 | partial | summaries include generic errors and summary payloads are redacted, but provider-specific delivery failures and validation-error categories are not exposed consistently | add structured redacted validation and provider-failure output | unit + integration |
| FR-004 | partial | `/api/proposals` serializes `externalUrl`; finish summaries and execution detail do not yet expose delivered issue links | propagate external links into summaries and execution-detail/Mission Control surfaces | unit + integration |
| FR-005 | partial | proposal service computes dedup fields and delivery metadata can carry `created`/`duplicateSource`; finish summaries and detail surfaces do not show dedup updates | surface new-or-updated/dedup attachment status in summaries and detail UI | unit + integration |
| FR-006 | implemented_unverified | `MoonMindRunWorkflow._run_proposals_stage()` calls `_set_state(STATE_PROPOSALS)`; API status vocabulary includes `proposals` | add direct state exposure verification for proposal-stage execution | unit + integration |
| FR-007 | implemented_verified | `tests/unit/api/routers/test_task_dashboard_view_model.py` proves Temporal `proposals` normalizes to `running`; task-list tests include `proposals` filter option | preserve dashboard compatibility mapping | final verify |
| FR-008 | partial | `/api/proposals` serializes provider, external key/url, delivery status, delivered/synced timestamps, review delivery, and task snapshot refs; execution detail and Mission Control do not consume this as run outcome visibility | add proposal outcome payload to execution detail boot/API and render it in Mission Control/detail views | unit + integration |
| FR-009 | partial | `_build_task_preview()` derives runtime, repository, publish mode, skills, and preset provenance for proposal records; max attempts/priority and run-detail visibility are incomplete | complete compact task summary fields and render them on run detail/Mission Control outcome surfaces | unit |
| FR-010 | implemented_unverified | proposal submission skips malformed records such as missing title/task payload and records errors; provider-decision safety tests prevent edited text from replacing stored snapshots | add redacted visible-error tests for malformed candidates and no-promotion guarantee in outcome surfaces | unit + integration |
| FR-011 | partial | delivery adapter and provider decision tests cover some retry/idempotency paths; summaries and diagnostics do not show external delivery failures end to end | add retry-safe delivery-failure metadata and visible diagnostics | unit + integration |
| FR-012 | partial | external GitHub/Jira issue review path exists in delivery design, but `frontend/src/entrypoints/proposals.tsx` still presents a proposal queue-like page | ensure normal navigation and feature wording treat external trackers as primary; keep any MoonMind proposal page as admin/recovery only or remove normal review affordance | frontend unit |
| FR-013 | partial | provider decision responses and delivery recovery expose `promotedExecutionId`; execution detail/Mission Control do not show promotion result links as proposal outcomes | surface promoted execution links in run detail and Mission Control | unit + integration |
| FR-014 | implemented_unverified | `spec.md` preserves MM-600 and the original preset brief; this plan preserves MM-600 | preserve traceability through research, design artifacts, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | generated/submitted proposal counts exist in summaries | add delivered counts and external links to summary scenario | unit + integration |
| SCN-002 | partial | dedup identity and duplicate-source metadata exist; run summary/detail visibility is missing | add duplicate delivery scenario coverage | unit + integration |
| SCN-003 | partial | malformed candidates and provider failures are handled in service/worker paths; visible redacted outcome evidence is incomplete | add malformed and provider-failure scenario coverage | unit + integration |
| SCN-004 | implemented_unverified | `STATE_PROPOSALS`, status vocabulary, and dashboard mapping exist | add proposal-stage state boundary test | unit + integration |
| SCN-005 | partial | provider decision metadata can store promoted execution IDs; Mission Control/detail links are missing | add promoted proposal outcome scenario | unit + integration |
| SCN-006 | partial | external tracker issue delivery is modeled; standalone proposal page remains visible as queue-like UI | adjust/reframe normal review path and add UI assertions | frontend unit |
| SC-001 | partial | generated/submitted counts covered; requested/delivered full set not covered | add summary contract tests for all count fields | unit + integration |
| SC-002 | partial | proposal API serializes external URLs; summary/detail parity missing | add parity test between delivered links and summary/detail outcome links | unit + integration |
| SC-003 | implemented_unverified | malformed candidate handling exists but is not proven through visible outcome surfaces | add visible skipped/error and zero-promotion tests | unit + integration |
| SC-004 | partial | redaction helpers and delivery/provider decision tests exist; provider-failure summary diagnostics not complete | add redacted provider-failure diagnostics tests | unit + integration |
| SC-005 | implemented_verified | dashboard mapping test proves `proposals -> running`; status options include `proposals` | preserve existing coverage and add state exposure boundary if needed | final verify + integration |
| SC-006 | partial | proposal API has most delivery/task-preview fields; UI run detail/Mission Control visibility missing | add API and frontend rendering tests for complete compact outcome fields | unit + frontend unit |
| SC-007 | partial | existing `ProposalsPage` appears to be a normal queue page | add tests/changes so external trackers are primary and MoonMind proposal page is not the normal review path | frontend unit |
| SC-008 | implemented_unverified | `spec.md` and this plan preserve MM-600 and source mappings | preserve through tasks and final verification | final verify |
| DESIGN-REQ-009 | partial | finish summaries include requested/generated/submitted/errors but not delivered links/dedup updates | same as FR-001 through FR-005 | unit + integration |
| DESIGN-REQ-028 | partial | `mm_state=proposals` and proposal record fields exist; execution detail/Mission Control outcome visibility incomplete | same as FR-006 through FR-009 and FR-013 | unit + integration + frontend unit |
| DESIGN-REQ-029 | partial | malformed and delivery failure handling exists but visible redacted partial-success reporting is incomplete | same as FR-003, FR-010, and FR-011 | unit + integration |
| DESIGN-REQ-030 | partial | external issue delivery exists, but a queue-like MoonMind proposals page remains exposed | same as FR-012 | frontend unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: Pydantic v2, FastAPI, SQLAlchemy async ORM, Temporal Python SDK, existing proposal service/repository, existing Temporal workflow/activity catalog, React, TanStack Query, Zod, Vitest, pytest  
**Storage**: Existing `task_proposals` table and provider metadata fields; no new persistent table planned unless summary/detail visibility cannot safely derive from existing proposal delivery records and workflow artifacts  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused Python iteration with `python -m pytest tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/agents/codex_worker/test_worker.py -q`; focused UI iteration with `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/proposals.test.tsx` after JS deps are prepared  
**Integration Testing**: pytest integration/boundary coverage, preferably extending `tests/integration/temporal/test_proposal_review_delivery.py`; run `./tools/test_integration.sh` when adding or changing `integration_ci` tests  
**Target Platform**: MoonMind backend control plane, Temporal workflow/worker runtime, and Mission Control web UI on Linux  
**Project Type**: Backend workflow/control-plane service plus frontend operational dashboard  
**Performance Goals**: Proposal outcome rendering remains bounded by the delivered proposal count for one run; summary payloads stay compact and avoid embedding large task snapshots or external issue bodies  
**Constraints**: Provider credentials stay inside trusted integration boundaries; errors and diagnostics must be redacted; proposal promotion uses stored snapshots only; no standalone proposal queue as the normal review path; workflow/activity payload changes need boundary regression coverage; no compatibility aliases for internal pre-release contracts  
**Scale/Scope**: One runtime story for a proposal-capable run's outcome visibility across summaries, API state, execution detail, Mission Control, and external tracker links

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan surfaces proposal outcomes from existing orchestration and provider records without changing agent cognition.
- II. One-Click Agent Deployment: PASS. Required tests stay hermetic; external provider credentials are not required for required CI.
- III. Avoid Vendor Lock-In: PASS. GitHub/Jira links are represented through provider-neutral outcome fields and provider metadata.
- IV. Own Your Data: PASS. Proposal snapshots, summaries, delivery records, and diagnostics remain in MoonMind-controlled storage/artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. Compact task summaries preserve skill context and preset provenance when present.
- VI. Design for Deletion / Thick Contracts: PASS. Summary, API, and UI contracts are explicit and provider-specific details stay behind existing delivery boundaries.
- VII. Runtime Configurability: PASS. Proposal generation and delivery continue to use existing workflow settings and task proposal policy.
- VIII. Modular and Extensible Architecture: PASS. Planned changes are scoped to proposal workflow summaries, proposal service/API serialization, and Mission Control/detail rendering.
- IX. Resilient by Default: PASS. Partial success, retry-safe delivery failures, dedup updates, and redacted diagnostics are explicit plan requirements.
- X. Facilitate Continuous Improvement: PASS. Proposal outcomes become part of structured run outcomes and operator diagnostics.
- XI. Spec-Driven Development: PASS. `spec.md`, this `plan.md`, and design artifacts preserve MM-600 before implementation.
- XII. Canonical Documentation Separation: PASS. Planning and rollout details remain under `specs/314-*`; canonical docs remain desired-state source requirements.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases are planned; internal proposal outcome payloads should be updated consistently with tests.

## Project Structure

### Documentation (this feature)

```text
specs/314-surface-proposal-outcomes/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-outcome-visibility-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by moonspec-tasks, not this step
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/worker.py                    # Codex finish summary/report payload
├── schemas/task_proposal_models.py                  # proposal API/output schemas
└── workflows/
    ├── task_proposals/
    │   ├── delivery.py                              # delivery result/decision metadata
    │   ├── models.py                                # proposal delivery record fields
    │   ├── repositories.py                          # proposal lookup/update persistence
    │   └── service.py                               # dedup, validation, promotion, failure metadata
    └── temporal/workflows/run.py                    # Temporal proposal stage state and finish summary

api_service/
└── api/routers/
    ├── executions.py                                # execution detail/action/state payloads
    └── task_proposals.py                            # proposal serialization and delivery recovery

frontend/src/
├── entrypoints/
│   ├── task-detail.tsx                              # execution detail proposal outcome rendering
│   ├── mission-control.test.tsx                     # Mission Control rendering tests
│   ├── task-detail.test.tsx                         # detail rendering tests
│   ├── tasks-list.tsx                               # status option and navigation behavior
│   ├── proposals.test.tsx                           # proposal admin/recovery path guardrail tests
│   └── proposals.tsx                                # admin/recovery-only proposal surface if retained
└── styles/mission-control.css                       # any compact outcome card/status styling

tests/
├── unit/
│   ├── agents/codex_worker/test_worker.py
│   ├── api/routers/test_task_proposals.py
│   ├── api/routers/test_task_dashboard_view_model.py
│   └── workflows/
│       ├── task_proposals/test_service.py
│       └── temporal/workflows/test_run_proposals.py
└── integration/
    └── temporal/test_proposal_review_delivery.py
```

**Structure Decision**: Reuse the existing proposal delivery record, proposal service, Temporal proposal stage, Codex finish summary builder, executions router, and Mission Control task-detail surfaces. Add fields and rendering at existing boundaries rather than introducing a new proposal queue or storage table.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

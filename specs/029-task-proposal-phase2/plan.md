# Implementation Plan: Task Proposal Queue Phase 2

**Branch**: `025-task-proposal-phase2` | **Date**: 2026-02-18 | **Spec**: specs/025-task-proposal-phase2/spec.md  
**Input**: Feature specification from `/specs/025-task-proposal-phase2/spec.md`

## Summary
Phase 2 extends the Task Proposal Queue with deduplicated proposal surfacing, edit-before-promote workflows, snooze and triage priority controls, and high-signal notifications. The backend (FastAPI + SQLAlchemy) gains new columns and endpoints, the worker keeps emitting the same payloads, and the dashboard adds UI affordances for similar proposals, edits, snoozing, and notifications.

## Technical Context

**Language/Version**: Python 3.11 (backend + worker), TypeScript-less vanilla JS dashboard  
**Primary Dependencies**: FastAPI, SQLAlchemy, Alembic, Celery worker clients, browser fetch APIs  
**Storage**: PostgreSQL (task_proposals + new notification table)  
**Testing**: pytest via `./tools/test_unit.sh`  
**Target Platform**: Linux services (Docker compose)  
**Project Type**: Backend services + lightweight dashboard UI  
**Performance Goals**: Proposal list/detail remain <200ms server-side, notification dispatch within 60s  
**Constraints**: No new infrastructure dependencies; stay compatible with existing queue auth flows; feature-flagged rollout  
**Scale/Scope**: Up to hundreds of proposals/day, <10 notification targets initially

## Constitution Check
The constitution file is still placeholder text; no enforceable principles beyond default quality bars. Proceed with standard testing + documentation commitments. Re-check after design (no new violations introduced).

## Project Structure

```text
specs/025-task-proposal-phase2/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/task_proposals_phase2.yaml

api_service/
├── api/routers/task_proposals.py        # REST updates
├── migrations/versions/*.py             # Alembic migration for new columns/table
├── schemas/task_proposal_models.py      # Response models w/ new fields
├── static/task_dashboard/dashboard.js   # UI updates

moonmind/
├── workflows/task_proposals/            # Models, repo, service logic
└── agents/codex_worker/worker.py        # (no Phase 2 change expected beyond metadata pass-through)

tests/
├── unit/api/routers/test_task_proposals.py
├── unit/workflows/task_proposals/test_service.py
└── unit/agents/codex_worker/test_worker.py (assert metadata unaffected)
```

**Structure Decision**: Backend + dashboard code already co-reside in repo; no new packages introduced. DB migration plus UI assets live with existing modules.

## Phase 0 – Research & Clarifications
Refer to `research.md` for resolved unknowns: dedup key computation, override promotion path, snooze semantics, and notification triggers. No open clarifications remain.

## Phase 1 – Design & Contracts
- Data model adjustments defined in `data-model.md` (dedup columns, snooze/priority fields, notification audit table, indexes).
- API contract for new/updated endpoints captured in `contracts/task_proposals_phase2.yaml`.
- Quickstart outlines enablement steps and config flags.

## Phase 2 – Implementation Steps
1. **Database Migration**: Add new columns, enums, indexes, and notification table. Ensure backfill of dedup values for existing proposals.
2. **Model Layer**: Update SQLAlchemy models + repository/service to compute/store dedup fields, manage priority, snooze metadata, and fetch similar proposals.
3. **Service Logic**: Extend create/list/get/promote/dismiss flows with dedup + override validation; add snooze, unsnooze, priority, and similar responses; emit notification events.
4. **API Schemas & Routers**: Reflect new fields in Pydantic models; add new endpoints + query params; update router helpers for includeSimilars/inclusion of dedup metadata.
5. **Dashboard UI**: Enhance list + detail views for similar proposals, dedup badges, priority filter/badge, snooze + edit modals; add edit-before-promote form.
6. **Notifications**: Implement Slack/webhook dispatch in service layer with metric/log coverage.
7. **Tests**: Expand unit tests covering dedup storage, override promotion path, snooze/priority endpoints, and notification behavior. Add dashboard tests if feasible (JS not unit-tested; rely on manual coverage + lint?).
8. **Docs**: Update `docs/TaskProposalQueue` with Phase 2 "implemented" status plus usage notes (later in implementation step).

## Complexity Tracking
No constitution violations identified; Phase 2 leverages existing components.

# Implementation Plan: Persist Authoritative Task Snapshots

**Branch**: `323-persist-task-snapshots` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime spec from MM-629 Jira preset brief.

## Summary

MoonMind already persists original task input snapshot artifacts for direct task submissions, rerun-created executions, and Jira Orchestrate child runs, and it exposes `taskInputSnapshot` status in execution detail responses. The remaining MM-629 delivery gap is that terminal edit/rerun actions still allow a parameter-derived fallback when `task_input_snapshot_ref` is missing. This plan removes that fallback for edit/rerun capability decisions so attachment-aware or preset-derived executions without an authoritative snapshot are explicitly degraded, while preserving existing snapshot creation, descriptor, rerun, and resume behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `api_service/api/routers/executions.py` persists snapshot artifacts; `moonmind/workflows/temporal/worker_runtime.py` persists Jira Orchestrate child snapshots | add/confirm focused coverage for submitted task snapshot contents | unit |
| FR-002 | partial | `_task_input_snapshot_descriptor_from_record()` exposes authoritative/degraded state, but `_build_action_capabilities()` permits parameter fallback for edit/full retry | remove parameter fallback from edit/rerun eligibility | unit |
| FR-003 | implemented_unverified | `_create_fresh_rerun_execution()` carries source identity and parameters | retain existing behavior, verify no fallback enables rerun without snapshot | unit |
| FR-004 | implemented_unverified | snapshot payload carries `attachmentRefs`; related attachment policy tests exist | confirm no silent action enablement when snapshot missing | unit |
| FR-005 | implemented_unverified | snapshot stores full task payload and child task payloads with template metadata when present | preserve payload shape; no live catalog dependency introduced | unit |
| FR-006 | partial | descriptor reports `degraded_read_only`, but actions may still allow edit/rerun from parameter fallback | make missing snapshot disable edit/rerun consistently | unit |
| FR-007 | implemented_unverified | `create_failed_step_resume_execution()` requires source snapshot and checkpoint match | keep resume snapshot requirement intact | unit |
| FR-008 | partial | `taskInputSnapshot` descriptor reports degraded/unavailable; actions reasons are inconsistent when fallback exists | align action disabled reasons with descriptor | unit |
| FR-009 | missing | new spec exists and preserves MM-629 | preserve traceability through plan, tasks, implementation, and verification | review |
| DESIGN-REQ-001..011 | partial | source behavior is mostly present; fallback gap conflicts with authoritative snapshot requirement | close fallback gap and verify boundary | unit + final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React present but not expected for this narrow boundary change.
**Primary Dependencies**: FastAPI route models, Pydantic v2 schemas, SQLAlchemy async execution records, Temporal execution service models.
**Storage**: Existing Temporal artifact metadata/content store and execution memo/artifact refs; no new persistent tables.
**Unit Testing**: `./tools/test_unit.sh` with targeted pytest paths during iteration.
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration if broader behavior changes; not expected for this route/model boundary.
**Target Platform**: MoonMind API service and Mission Control execution detail consumers.
**Project Type**: Python backend with existing React frontend consumers.
**Performance Goals**: No additional database or artifact reads in action capability serialization.
**Constraints**: Do not mutate checked-in skills. Preserve Temporal payload compatibility. Keep large task bodies out of workflow history. Do not introduce compatibility aliases for internal contracts.
**Scale/Scope**: One execution detail/action capability boundary and its tests.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The change preserves MoonMind orchestration boundaries and does not alter agent cognition.
- II. One-Click Agent Deployment: PASS. No new deployment dependency.
- III. Avoid Vendor Lock-In: PASS. No vendor-specific coupling.
- IV. Own Your Data: PASS. Snapshot artifacts remain MoonMind-owned data.
- V. Skills Are First-Class: PASS. No skill runtime mutation.
- VI. Replaceable Scaffolding: PASS. The change tightens contract behavior behind existing route/model boundaries.
- VII. Runtime Configurability: PASS. Existing task editing feature flag behavior remains.
- VIII. Modular Architecture: PASS. Work stays in API serialization/action capability logic and tests.
- IX. Resilient by Default: PASS. Missing authoritative data fails explicitly instead of synthesizing unsafe recovery state.
- X. Continuous Improvement: PASS. Verification evidence will be captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, implementation, and verification are traceable to MM-629.
- XII. Canonical Docs vs Tmp: PASS. Runtime implementation notes live under `specs/323-persist-task-snapshots/`; canonical docs are read-only sources.
- XIII. Pre-release Compatibility: PASS. Removes fallback behavior rather than adding compatibility aliases.

## Project Structure

```text
api_service/api/routers/executions.py        # execution detail serialization and action capability policy
tests/unit/api/routers/test_executions.py    # API route/action capability unit coverage
moonmind/schemas/temporal_models.py          # taskInputSnapshot descriptor model
moonmind/workflows/temporal/service.py       # rerun/resume execution creation boundaries
moonmind/workflows/temporal/worker_runtime.py# Jira Orchestrate child snapshot persistence
specs/323-persist-task-snapshots/            # MoonSpec artifacts for MM-629
```

## Complexity Tracking

No constitution violations or extra complexity accepted.

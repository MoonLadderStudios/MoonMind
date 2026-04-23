# Implementation Plan: Finish Task Remediation Desired-State Implementation

**Branch**: `242-finish-task-remediation` | **Date**: 2026-04-23 | **Spec**: `specs/242-finish-task-remediation/spec.md`
**Input**: Single-story feature specification from `/specs/242-finish-task-remediation/spec.md`

## Summary

Implement MM-483 in runtime mode by completing the remaining Task Remediation desired-state contract around canonical action coverage, safe execution boundaries, durable mutation coordination, lifecycle/read-model evidence, policy-bounded self-healing, and Mission Control presentation. Repo gap analysis shows substantial existing foundations: remediation links and pinned targets, context artifacts, evidence tools, lifecycle artifact helpers from MM-456, target-side relationship rendering, authority decisions, and mutation guard tests. The first missing runtime surface is canonical action registry coverage: `remediation_actions.py` still exposes legacy aliases instead of the documented action kinds. Tests will start there and preserve broader tasks for the remaining runtime gaps.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/remediation_actions.py` exposes the documented canonical dotted action set; focused tests passed | no further registry work | final verify |
| FR-002 | implemented_verified | raw access denial exists in `RemediationActionAuthorityService` and mutation guard; canonical registry tests exclude raw actions | no further registry work | final verify |
| FR-003 | implemented_verified | action metadata now includes target type, inputs, risk, preconditions, idempotency, verification, and audit shape | no further registry work | final verify |
| FR-004 | partial | authority service validates but does not execute actions; safe execution adapters not yet wired | keep execution out of authority and plan adapter boundary work | unit + integration |
| FR-005-FR-007 | partial | action request/result payloads exist; MM-456 artifact helpers exist | integrate authority output with artifact publication in later tasks | unit + integration |
| FR-008-FR-009 | implemented_verified | service validates `taskRunIds` shape and rejects foreign IDs when target task-run evidence is available; focused tests passed | no further create-time validation work in this slice | final verify |
| FR-010-FR-011 | partial | mutation guard has bounded nesting; no automatic self-healing creation policy path found | add policy gate before any automatic creation path | unit |
| FR-012-FR-015 | partial | mutation guard has in-memory lock/ledger and tests; durable DB-backed guard not complete | move lock/ledger state to durable boundary or document cutover | unit + integration |
| FR-016-FR-020 | partial | target control artifacts and compatibility constraints exist in adjacent runtime paths | add adapter-boundary tests before wiring actions | unit + integration |
| FR-021-FR-027 | implemented_unverified | MM-456 lifecycle helpers and read-model fields exist | verify runtime publication and target-side summary coverage | unit + integration |
| FR-028-FR-035 | partial | cancellation/degraded outcomes exist in pieces across context/action services and UI | consolidate bounded outcomes and UI exposure | unit + UI |
| FR-036 | missing | no MM-483-specific coverage matrix exists | add workflow/activity, adapter, API, and UI tests | unit + integration |
| FR-037 | implemented_verified | `spec.md` and Jira input preserve MM-483 | preserve in tasks, verification, commit/PR metadata | final verify |
| FR-038 | implemented_unverified | compatibility policy is in constitution and existing code avoids raw execution | verify no compatibility shims or raw execution bypasses are added | final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control if UI behavior changes  
**Primary Dependencies**: Pydantic v2, SQLAlchemy async ORM, FastAPI, Temporal Python SDK, existing Temporal artifact and remediation services, React/Vitest  
**Storage**: Existing `execution_remediation_links`, Temporal execution records, artifact metadata/content store, and existing control/summary projections; no new persistent table planned unless durable lock/ledger cannot reuse existing records  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and targeted router/UI unit commands as files change  
**Integration Testing**: `./tools/test_integration.sh` when artifact lifecycle, API routes, or compose-backed runtime behavior changes  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal workflow/service boundary and Mission Control UI  
**Performance Goals**: Remediation metadata remains compact; logs, snapshots, and evidence bodies stay behind artifact refs and out of workflow history  
**Constraints**: Runtime mode; preserve MM-483 traceability; no raw host/Docker/SQL/storage/secret access; compatibility-sensitive Temporal shapes require explicit compatibility or cutover evidence  
**Scale/Scope**: One remediation run targets one logical execution and one pinned run snapshot, with bounded action attempts and compact read-model summaries

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Typed actions orchestrate owning subsystems instead of giving the model raw control.
- II. One-Click Agent Deployment: PASS. No required external provider dependency is added.
- III. Avoid Vendor Lock-In: PASS. Action kinds are MoonMind control-plane semantics, not provider-specific cognitive behavior.
- IV. Own Your Data: PASS. Evidence stays in local artifacts/read models.
- V. Skills Are First-Class: PASS. This story does not mutate runtime skill sources.
- VI. Tests Are the Anchor: PASS. Workflow/activity, adapter, API, and UI boundaries require tests.
- VII. Runtime Configurability: PASS. Policy-gated self-healing and action policies remain explicit runtime configuration.
- VIII. Modular Architecture: PASS. Work stays at remediation service, adapter, API, and UI boundaries.
- IX. Resilient by Default: PASS. Locking, idempotency, degraded outcomes, cancellation, and continuation are explicit.
- XII. Canonical Docs vs Tmp: PASS. Jira input remains under `docs/tmp`; canonical docs remain desired state.
- XIII. Pre-release Compatibility Policy: PASS. Superseded legacy action aliases must be removed rather than kept as compatibility shims.

## Project Structure

### Documentation (this feature)

```text
specs/242-finish-task-remediation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-runtime.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
├── remediation_actions.py
├── remediation_context.py
├── remediation_tools.py
└── service.py

api_service/api/routers/
└── executions.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

tests/unit/workflows/temporal/
└── test_remediation_context.py

tests/unit/api/routers/
└── test_executions.py
```

**Structure Decision**: Keep deep evidence artifact-backed, keep query/read-model state compact, and keep action execution behind owning control-plane or subsystem adapter boundaries.

## Complexity Tracking

None.

# Implementation Plan: Bounded Remediation Evidence Context

**Branch**: `318-bounded-remediation-evidence` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:a6d63116-cfbf-4474-90db-6af6f461079b/repo/specs/318-bounded-remediation-evidence/spec.md`

## Summary

Deliver MM-618 by completing the remediation evidence story around a bounded `reports/remediation_context.json` artifact, typed remediation evidence tools, policy-gated live follow, durable fallbacks, and Mission Control evidence presentation. Repo analysis found substantial existing implementation in `moonmind/workflows/temporal/remediation_context.py`, `moonmind/workflows/temporal/remediation_tools.py`, task-run observability routes, and Mission Control remediation panels. Remaining work should focus on closing partial behavior and proof gaps: real task-run log/live-follow adapter binding, live-follow capability state generation, explicit unavailable-evidence/degraded-evidence recording in context payloads, and hermetic integration coverage across remediation creation, context artifact publication, typed evidence reads, and UI evidence presentation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `RemediationContextBuilder.build_context()` creates and links `reports/remediation_context.json`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_context_builder_creates_bounded_linked_artifact` | preserve behavior; add end-to-end integration proof | integration |
| FR-002 | partial | Context payload includes target, selected steps, task run IDs, target artifact refs, policies, and liveFollow stub in `remediation_context.py`; observability refs/compact summaries are not fully resolved from task-run surfaces | extend builder to resolve observability summary/log/diagnostic refs and compact availability metadata | unit + integration |
| FR-003 | implemented_verified | Builder stores refs and `boundedness.rawLogBodiesIncluded = False`; unit assertions cover bounded payload | preserve behavior; include regression in integration artifact payload check | integration |
| FR-004 | implemented_verified | Sanitization helpers and tests reject presigned URLs, local paths, secret-like policy keys, and secret-bearing payloads | preserve behavior | none beyond final verify |
| FR-005 | implemented_verified | `RemediationEvidenceToolService` gates context, artifact, log, live-follow, and action-prep access through typed methods and context membership checks | preserve behavior; expose through worker/activity boundary if not already bound | integration |
| FR-006 | partial | `read_target_artifact()` and `read_target_logs()` exist and unit tests use injected readers; real task-run log reader adapter is not proven in repo evidence | bind remediation log reader to existing task-run observability/log services without raw storage exposure | unit + integration |
| FR-007 | partial | `follow_target_logs()` enforces context support/policy, but builder currently emits `supported: False` and does not compute live capability from target activity/task run state | add live-follow capability resolution from target task-run support and policy | unit + integration |
| FR-008 | partial | Live follower supports resume cursors and cursor recorder in service-level tests; durable fallback remains separate from live-follow state generation | persist/resume live cursor through remediation context or compact runtime state and document fallback path | unit + integration |
| FR-009 | partial | Task-run routes expose stdout/stderr/merged/diagnostics and Mission Control shows fallback messaging; remediation tool fallback orchestration is not fully proven | ensure non-live paths return durable merged/stdout/stderr/diagnostics refs or read results when live follow is unavailable | unit + integration |
| FR-010 | partial | Summary helpers can record `evidenceDegraded`, unavailable evidence, and fallbacks; builder does not yet compute missing evidence classes consistently | compute and persist evidence availability records in context payload and tool responses | unit + integration |
| FR-011 | partial | Summary helpers model degraded evidence and UI has degraded-state coverage; historical target context generation with merged-log-only evidence is not proven | add historical target case with merged logs and partial artifacts | unit + integration |
| FR-012 | partial | Mission Control renders remediation evidence bundle, degraded state, approval, and links; referenced target logs/diagnostics/decision/action/verification artifacts are only partially exercised | expand UI/API contract evidence to cover all remediation evidence artifact classes and live observation state | unit + integration |
| FR-013 | implemented_verified | `prepare_action_request()` rereads target execution state before action and unit test covers target run changes | preserve behavior; include in action-boundary integration test if action execution is touched | integration |
| FR-014 | implemented_verified | `spec.md` preserves MM-618 and canonical preset brief; this plan preserves traceability | preserve MM-618 in all downstream artifacts and PR metadata | final verify |
| Scenario 1 | partial | Context builder publishes linked artifact after remediation link exists | ensure artifact is generated before diagnostic work starts in real run lifecycle | integration |
| Scenario 2 | implemented_verified | Unit tests prove context contains refs/summaries and excludes raw logs/secrets | preserve with integration payload assertion | integration |
| Scenario 3 | partial | Live-follow service can follow only when context says supported; builder does not yet set support dynamically | implement capability resolver and test active supported target | unit + integration |
| Scenario 4 | partial | UI and task-run routes support durable fallback messaging/reads; remediation service fallback flow needs proof | add unavailable/denied/disconnected live-follow cases | unit + integration |
| Scenario 5 | partial | Degraded evidence summary helpers and UI messages exist; context builder does not compute degraded evidence for historical/partial targets | add evidence availability/degraded fields and tests | unit + integration |
| Scenario 6 | partial | Mission Control renders context artifact link and degraded state; all evidence classes are not yet covered | expand API/UI tests for decision, action, verification, diagnostics, and live observation links | unit + integration |
| SC-001 | partial | Builder creates artifact when called; lifecycle timing before diagnostics is not proven | wire or verify builder invocation at remediation startup | integration |
| SC-002 | implemented_verified | Unit tests assert no raw logs, presigned URLs, local paths, or secrets in context/lifecycle payloads | preserve behavior | none beyond final verify |
| SC-003 | partial | Live-follow states are not fully normalized in context builder | add explicit active/unavailable/unsupported/policy_denied state contract | unit + integration |
| SC-004 | partial | Degraded state helpers exist; builder/tool outputs do not consistently list missing classes | add availability records and historical partial-evidence tests | unit + integration |
| SC-005 | partial | UI links context artifact; all referenced evidence classes need coverage | expand Mission Control evidence rendering tests | unit + integration |
| SC-006 | implemented_verified | Spec and plan preserve MM-618 and source IDs | preserve in tasks, verification, commit, and PR metadata | final verify |
| DESIGN-REQ-008 | partial | Context builder exists but needs richer evidence refs and compact summaries | complete evidence resolution and availability metadata | unit + integration |
| DESIGN-REQ-009 | implemented_verified | Artifact/log access remains server-mediated and restricted; typed tools enforce context membership | preserve; add adapter-boundary coverage | integration |
| DESIGN-REQ-010 | partial | Live-follow tool exists but capability/policy state generation is incomplete | implement live-follow state resolution and cursor persistence proof | unit + integration |
| DESIGN-REQ-025 | partial | UI/degraded helpers exist but historical/partial evidence path is incomplete | add degraded evidence and presentation coverage | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control evidence presentation  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK activity/workflow boundaries, existing Temporal artifact service, existing task-run observability/log routes, React, TanStack Query, Zod  
**Storage**: Existing `execution_remediation_links`, `temporal_execution_sources`/canonical execution records, Temporal artifact metadata/content store, managed-run observability artifacts; no new persistent database table planned  
**Unit Testing**: `pytest` through `./tools/test_unit.sh`; focused Python tests under `tests/unit/workflows/temporal/test_remediation_context.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/api/routers/test_executions.py`; focused frontend tests through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` or `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` after dependencies are prepared  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; add targeted integration coverage under `tests/integration/temporal/` or `tests/integration/workflows/temporal/` only when it can run within CI constraints  
**Target Platform**: Linux server/container deployment with FastAPI API service, Temporal workers, local artifact store, and Mission Control web UI  
**Project Type**: Full-stack orchestration feature touching backend services, Temporal activity/service boundaries, artifact/log contracts, and Mission Control task detail UI  
**Performance Goals**: Context artifacts remain bounded; default log tail stays capped at 2000 lines; no unbounded log bodies or rich diagnostics enter workflow history  
**Constraints**: Server-mediated artifact/log access only; no presigned URLs, storage keys, local paths, or secrets in durable context; live follow is best effort and never authoritative; workflow history carries refs and compact metadata only  
**Scale/Scope**: One remediation task targets one logical execution and selected task-run evidence; cap selected task run IDs and evidence summaries to bounded payload size

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses MoonMind orchestration services and existing agent/runtime observability, not a new agent engine. |
| II. One-Click Agent Deployment | PASS | Plans reuse existing FastAPI/Temporal/artifact services and local test paths; no new mandatory external SaaS. |
| III. Avoid Vendor Lock-In | PASS | Evidence contracts are MoonMind-owned refs and typed surfaces, not provider-specific storage URLs. |
| IV. Own Your Data | PASS | Evidence remains in MoonMind artifacts/log stores on operator-controlled infrastructure. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill runtime mutation is planned; remediation evidence surfaces remain adapter/service boundaries. |
| VI. Scientific Method / Replaceable Scaffolding | PASS | Plan requires unit and integration proof before implementation claims. |
| VII. Runtime Configurability | PASS | Live-follow and policy decisions derive from request/runtime policy rather than hardcoded unrestricted access. |
| VIII. Modular and Extensible Architecture | PASS | Work stays in remediation context/tool services, Temporal artifact boundary, task-run observability, and UI contracts. |
| IX. Resilient by Default | PASS | Context and cursor state are durable/bounded; missing evidence degrades explicitly instead of deadlocking. |
| X. Continuous Improvement | PASS | Remediation evidence and decision artifacts support structured outcomes and follow-up analysis. |
| XI. Spec-Driven Development | PASS | `spec.md` is present and this plan preserves MM-618 traceability. |
| XII. Canonical Docs vs Migration Backlog | PASS | Implementation details remain in this feature plan/artifacts, not canonical docs. |
| XIII. Pre-Release Velocity | PASS | Plan avoids compatibility aliases for internal remediation contracts; unsupported values should fail fast. |

Post-design re-check: PASS. The Phase 1 artifacts below preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/318-bounded-remediation-evidence/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-evidence.md
└── tasks.md              # Phase 2 output; not created by this step
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_context.py
├── remediation_tools.py
├── remediation_actions.py
├── artifacts.py
└── runtime/

api_service/
├── api/routers/
│   ├── executions.py
│   ├── task_runs.py
│   └── temporal_artifacts.py
├── db/models.py
└── migrations/versions/

frontend/src/
├── entrypoints/task-detail.tsx
├── entrypoints/task-detail.test.tsx
└── styles/mission-control.css

tests/
├── unit/workflows/temporal/
├── unit/api/routers/
├── unit/api/
└── integration/temporal/
```

**Structure Decision**: Use the existing full-stack MoonMind structure. Backend remediation evidence logic belongs at service/activity boundaries under `moonmind/workflows/temporal/`, API read/projection contracts stay under `api_service/api/routers/`, persistence remains in existing SQLAlchemy models and migrations, Mission Control evidence presentation stays in `frontend/src/entrypoints/task-detail.tsx`, and tests follow the existing unit plus hermetic integration taxonomy.

## Complexity Tracking

No constitution violations or complexity exceptions are planned.

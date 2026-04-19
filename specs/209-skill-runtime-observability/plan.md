# Implementation Plan: Skill Runtime Observability and Verification

**Branch**: `[209-skill-runtime-observability]` | **Date**: 2026-04-19 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/209-skill-runtime-observability/spec.md`

## Summary

MM-408 requires operator-visible evidence for skill-enabled executions: selected skills, provenance, materialization mode, visible and backing paths, artifact refs, collision diagnostics, and lifecycle intent for proposal, schedule, rerun, retry, and replay paths. The repo already has resolved skill models, materialization metadata, projection diagnostics, task skill selector pass-through, and a basic task-detail skill badge, but the execution detail contract and UI only expose `resolvedSkillsetRef` and `taskSkills`. This story will add a compact `skillRuntime` execution-detail payload, render richer task-detail provenance, preserve full-body redaction, and add unit plus focused UI/API tests. Lifecycle intent surfaces will be verified through existing proposal/schedule/rerun payload paths first, with implementation contingency where metadata is missing.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `tests/unit/api/routers/test_executions.py` preserves `task.skills` selectors on create | add focused verification in final story tests if touched | unit |
| FR-002 | partial | `ExecutionModel` exposes only `resolvedSkillsetRef` and `taskSkills`; `SkillProvenanceBadge` renders only explicit selection, delegated skill, and snapshot ref | add `skillRuntime` model/API fields and task-detail rendering for versions/provenance/mode/path refs | unit + UI |
| FR-003 | partial | `RuntimeSkillMaterialization.metadata` includes `manifestPath`; execution detail does not expose manifest or prompt-index refs as skill refs | include manifest/prompt-index refs in compact `skillRuntime` payload | unit + UI |
| FR-004 | partial | Standard views currently avoid bodies by omission, but no structured safe skill payload exists | ensure `skillRuntime` contains metadata/refs only and tests reject body leakage | unit + UI |
| FR-005 | partial | Automation debug surfaces expose selected skill, adapter id, execution path, and flags; task debug surfaces lack raw skill materialization details | keep raw fields out of standard view and expose only safe compact debug-ready metadata | unit |
| FR-006 | implemented_unverified | `AgentSkillMaterializer` records backing path, visible path, active skills, manifest path, and prompt index ref | wire existing materialization-shaped metadata into execution detail when present | unit |
| FR-007 | implemented_verified | `AgentSkillMaterializer._projection_error_message` and unit tests include path, object kind, action, remediation, and preserve body redaction | no new implementation unless regression appears | final regression |
| FR-008 | partial | `TaskProposalCreateRequest` includes `taskSkills`; proposal service tests cover task preview skills, but explicit selector/default-inheritance semantics are not fully documented in the execution detail contract | add lifecycle-intent contract and targeted test coverage where payload already supports it | unit |
| FR-009 | missing | Schedule mapping tests do not show skill selector or snapshot intent metadata | add schedule/lifecycle metadata planning and focused tests or document unavailable source if blocked | unit |
| FR-010 | partial | `resolvedSkillsetRef` exists in runtime request models; materializer consumes supplied snapshots; rerun/edit helpers pass task skills but not explicit reuse semantics | add rerun/replay metadata verification or minimal payload support for explicit snapshot reuse | unit |
| FR-011 | partial | `tests/unit/services/test_skill_materialization.py` covers several boundary cases; exact replay and repo input without mutation need story-level verification across boundary surfaces | add targeted service/activity/API tests for remaining MM-408 evidence | unit |
| FR-012 | implemented_verified | `spec.md` preserves MM-408 brief and this plan preserves traceability | no code work | final verification |
| DESIGN-REQ-010 | partial | Task detail has a basic skill badge, create API preserves selectors | add richer operator-visible task detail fields | unit + UI |
| DESIGN-REQ-018 | partial | Materialization records required evidence, but execution detail does not surface it | expose compact skill runtime evidence | unit + UI |
| DESIGN-REQ-019 | partial | Existing models carry `resolvedSkillsetRef` and task skills; lifecycle semantics are not explicit enough | add lifecycle intent contract/tests for proposal, schedule, rerun/replay | unit |
| DESIGN-REQ-020 | partial | Materializer tests cover projection, multi-skill, collision, and no-body behavior; exact-snapshot replay and repo input lifecycle evidence need targeted coverage | add boundary/lifecycle tests | unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for task-detail UI  
**Primary Dependencies**: Pydantic v2, FastAPI, SQLAlchemy async ORM, existing Temporal execution router/service, React, Vitest, pytest  
**Storage**: Existing Temporal execution records, input parameters, artifact refs, and materialization metadata only; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh`; focused Python tests with `python -m pytest`; focused UI tests with `npm run ui:test -- <path>` or `./tools/test_unit.sh --ui-args <path>`  
**Integration Testing**: Existing hermetic suite through `./tools/test_integration.sh`; no new compose service dependency expected  
**Target Platform**: MoonMind API service, Mission Control task detail, and managed runtime execution metadata surfaces  
**Project Type**: Web service plus frontend dashboard behavior  
**Performance Goals**: Skill runtime metadata remains compact and does not require reading full skill bodies or dereferencing artifacts during task-detail render  
**Constraints**: Preserve artifact-backed payload discipline; do not dump full skill bodies; preserve MM-408 traceability; add workflow/activity or adapter-boundary tests where runtime materialization semantics are affected  
**Scale/Scope**: One execution detail payload extension, one task-detail UI component extension, lifecycle metadata tests, and focused boundary verification

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I, Orchestrate Don't Recreate: PASS. The story exposes orchestration evidence and does not recreate agent behavior.
- Principle IV, Own Your Data: PASS. Metadata and artifact refs remain operator-controlled and local to MoonMind surfaces.
- Principle V, Skills Are First-Class: PASS. The story makes resolved skill state first-class in operator inspection and tests.
- Principle VII, Powerful Runtime Configurability: PASS. Skill selector and lifecycle intent remains payload/config driven.
- Principle VIII, Modular and Extensible Architecture: PASS. Changes are scoped to existing execution schemas, router serialization, task-detail UI, and boundary tests.
- Principle IX, Resilient by Default: PASS. Projection diagnostics remain actionable and replay/rerun semantics are explicit.
- Principle XII, Canonical Documentation Separates Desired State From Backlog: PASS. This feature uses `docs/tmp` planning artifacts for implementation details and does not rewrite canonical docs as migration trackers.
- Principle XIII, Pre-Release Compatibility Policy: PASS. No compatibility aliases are planned; the internal execution-detail contract is extended directly.

## Project Structure

### Documentation (this feature)

```text
specs/209-skill-runtime-observability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skill-runtime-observability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── schemas/
    └── temporal_models.py

api_service/
└── api/
    └── routers/
        └── executions.py

frontend/
└── src/
    ├── components/
    │   └── skills/
    │       └── SkillProvenanceBadge.tsx
    └── entrypoints/
        ├── task-detail.tsx
        └── task-detail.test.tsx

tests/
└── unit/
    ├── api/
    │   └── routers/
    │       └── test_executions.py
    ├── services/
    │   └── test_skill_materialization.py
    └── workflows/
        └── temporal/
            └── test_run_artifacts.py
```

**Structure Decision**: Extend existing execution detail and task-detail skill UI surfaces rather than creating a separate skill observability endpoint. Keep runtime materialization behavior in the existing service and tests unless lifecycle verification exposes a gap.

## Complexity Tracking

No constitution violations.

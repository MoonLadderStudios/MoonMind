# Implementation Plan: Remediation Mission Control Panels

**Branch**: `run-jira-orchestrate-for-mm-624-expose-m-c86d6a33` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/324-remediation-mission-control-panels/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed Jira branch name because it does not match the `###-feature-name` feature-branch convention. Planning continues from the active `.specify/feature.json` directory.

## Summary

MM-624 adds the operator-facing Mission Control layer for task remediation: create remediation from problem surfaces, show bidirectional remediation relationships, expose bounded evidence and live-observation state, and support approval handoff decisions. Repo inspection shows backend remediation link, approval, artifact, and validation foundations plus task-detail UI panels already exist, but several story requirements remain partial: creation is only on task detail, selected step scope/current target state/allowed actions/live-follow cursor details are not fully surfaced, and approval detail metadata is not fully serialized from backend link state. The implementation should therefore be verification-first against existing task-detail behavior, then fill backend contract and frontend gaps with focused unit tests and hermetic API/UI integration coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/task-detail.tsx` exposes creation for eligible task detail targets only | add or wire remediation creation affordances for specified banners/problem surfaces, or document unsupported surfaces with explicit UI fallback if no surface exists yet | frontend unit + integration |
| FR-002 | partial | task detail supports mode, authority, action policy, pinned run, and fixed evidence preview; no selected step scope control found | add selected step/all-step control and richer evidence preview state | frontend unit |
| FR-003 | implemented_unverified | `create_remediation_execution` injects `task.remediation.target.workflowId`; task-detail test asserts payload shape | add API/route verification for canonical `task.remediation` payload from Mission Control submission | unit + integration |
| FR-004 | implemented_unverified | inbound panel shows remediation link, status, authority, latest action, resolution, and lock scope | add coverage for active lock holder/badge semantics and degraded relationship fetch state | frontend unit |
| FR-005 | partial | outbound panel shows target link, pinned run, mode, authority, status, evidence bundle, approval; selected steps/current target state/allowed actions/lock state not found | extend response/UI model to include and render selected steps, current target state, allowed actions, and lock state | API contract + frontend unit |
| FR-006 | partial | remediation evidence panel lists remediation artifacts by `remediation.*` type | ensure target logs, diagnostics, decision logs, action request/result, verification artifacts are visible with safe labels and missing-evidence states | frontend unit + integration |
| FR-007 | partial | UI labels live-follow unavailable fallback but not active live observation state | add active live-observation labeling separate from authoritative evidence | frontend unit |
| FR-008 | missing | no UI fields found for live-follow sequence position, reconnect state, or epoch boundaries | extend backend/UI contract and render live-follow cursor, reconnect, and epoch metadata | API contract + frontend unit |
| FR-009 | partial | durable fallback message appears when context artifact is missing in follow mode | verify fallbacks for logs, diagnostics, summaries, and artifacts rather than only missing context artifact | frontend unit + integration |
| FR-010 | partial | approval controls and read-only state exist; backend serializer currently emits pending/not_required only | include action, preconditions, blast radius, risk, audit ref, and persisted decision details when available | backend unit + frontend unit |
| FR-011 | implemented_unverified | `TemporalExecutionService._validate_remediation_link` rejects missing/not found/unauthorized targets | add route-level Mission Control create validation assertion for structured error display | backend unit + frontend unit |
| FR-012 | implemented_unverified | service pins target run and rejects mismatched requested run | add UI/API verification that pinned run remains displayed after current target changes | unit + integration |
| FR-013 | implemented_unverified | `RemediationContextBuilder` records historical merged-log degraded evidence | ensure Mission Control renders historical degraded evidence from link/artifact metadata | frontend unit |
| FR-014 | partial | context builder records unavailable evidence classes; UI only has broad missing artifact messages | expose unavailable evidence classes in Mission Control panels | API contract + frontend unit |
| FR-015 | partial | link model has active lock scope/holder; UI renders scope but not clear owner/permitted outcome | render lock owner and permitted outcome/downgrade state | frontend unit |
| FR-016 | missing | forced termination policy is backend remediation action behavior, not exposed in handoff panel evidence | ensure high-risk/forced-termination proposals show approval/rejection policy details when presented | backend unit + frontend unit |
| FR-017 | partial | context/lifecycle helpers normalize no-op/precondition/verification states; UI does not clearly render all outcome variants | map and render no-op, precondition_failed, verification_failed outcomes with evidence refs | frontend unit |
| FR-018 | partial | final summary and lock release are generated by remediation lifecycle helpers; task detail does not specifically assert failed remediator summary/lock release state | add UI rendering and tests for failed remediation final summary and lock-release state | frontend unit |
| FR-019 | implemented_verified | `spec.md` preserves `MM-624`, original preset brief, linked issue context, and source mappings | preserve traceability in downstream artifacts, commits, PR, and verification | final verify |
| SC-001 | partial | task-detail creation path covered; other specified entry surfaces not covered | add entry-surface coverage or explicit bounded scope decision | frontend unit + integration |
| SC-002 | implemented_unverified | existing task-detail tests cover representative inbound link display | strengthen lock/owner assertions | frontend unit |
| SC-003 | partial | existing outbound panel lacks selected steps/current target/allowed actions/lock state | complete UI/API contract | API contract + frontend unit |
| SC-004 | missing | no active live-follow cursor/reconnect/epoch UI evidence found | add live observation state contract and tests | frontend unit + integration |
| SC-005 | partial | approval controls/read-only tests exist; metadata details are incomplete | add complete approval handoff metadata tests | backend unit + frontend unit |
| SC-006 | partial | degraded missing evidence/live-follow tests exist; lock/precondition variants incomplete | add degraded-state matrix tests | frontend unit |
| SC-007 | implemented_verified | `spec.md` and checklist preserve traceability | continue preserving MM-624 and source IDs through plan/tasks/verification | final verify |
| DESIGN-REQ-010 | partial | task-detail UI covers many section 15 panels but not all fields/surfaces | complete Mission Control remediation UX contract | unit + integration |
| DESIGN-REQ-024 | partial | backend validates target and evidence degradation; UI does not cover all cases | add UI/API degraded-state propagation | unit + integration |
| DESIGN-REQ-025 | partial | mutation guard and link state exist; UI only partially presents lock/failure states | expose lock/precondition/failure outcomes | unit + integration |
| DESIGN-REQ-028 | partial | approval/audit/final summary foundations exist; UI serialization/display incomplete | complete approval and summary handoff surfaces | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Zod, generated OpenAPI types  
**Storage**: Existing Temporal execution records, `execution_remediation_links`, Temporal artifact metadata/content store, and existing audit/control-event data; no new persistent table planned  
**Unit Testing**: `./tools/test_unit.sh`; focused frontend iteration with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` or `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` after dependencies are prepared  
**Integration Testing**: `./tools/test_integration.sh` for required `integration_ci`; add hermetic FastAPI/API route or Temporal service tests if backend contract changes cross service boundaries  
**Target Platform**: Browser Mission Control UI backed by local-first MoonMind API/Temporal services  
**Project Type**: Web application plus FastAPI/Temporal backend  
**Performance Goals**: Task detail remains responsive while polling task detail, artifacts, remediation links, and live-observation metadata; no unbounded logs or evidence bodies are fetched into UI state  
**Constraints**: Preserve canonical `task.remediation`; keep live follow observational and non-authoritative; use artifact refs and bounded metadata, not raw storage paths, presigned URLs, or secrets; keep workflow/activity compatibility if payloads change  
**Scale/Scope**: One single-story Mission Control remediation UX over existing remediation links, artifacts, approval decisions, and task detail surfaces

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - the plan exposes existing remediation orchestration and action surfaces rather than inventing a new agent runtime.
- II. One-Click Agent Deployment: PASS - no mandatory external service or new deployment prerequisite is introduced.
- III. Avoid Vendor Lock-In: PASS - remediation UI uses MoonMind execution/artifact contracts, not a provider-specific interface.
- IV. Own Your Data: PASS - evidence remains MoonMind-owned artifact refs and bounded metadata.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime behavior changes are planned.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - plan focuses on API/UI contracts and tests around volatile UI scaffolding.
- VII. Runtime Configurability: PASS - no hardcoded provider or operator configuration is introduced.
- VIII. Modular and Extensible Architecture: PASS - work stays in task detail UI, execution router/service models, and existing remediation services.
- IX. Resilient by Default: PASS - degraded states, retries/fallbacks, lock conflicts, and compatibility-sensitive payloads require boundary coverage.
- X. Facilitate Continuous Improvement: PASS - run evidence and final verification remain artifact-backed and traceable.
- XI. Spec-Driven Development: PASS - this plan follows the existing one-story `spec.md`; tasks and verification will follow.
- XII. Canonical Docs vs Migration Backlog: PASS - planning details stay under `specs/324-remediation-mission-control-panels/`, not canonical docs.

## Project Structure

### Documentation (this feature)

```text
specs/324-remediation-mission-control-panels/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-mission-control-contract.md
└── tasks.md                  # Created by moonspec-tasks, not this step
```

### Source Code (repository root)

```text
api_service/
└── api/routers/executions.py          # remediation create/list/approval route models and serialization

moonmind/
└── workflows/temporal/
    ├── service.py                     # remediation link validation, lookups, approval decisions
    ├── remediation_context.py          # evidence/live-follow/degraded metadata builders
    └── remediation_actions.py          # action authority, policy, and mutation guard support

frontend/src/
├── entrypoints/task-detail.tsx         # Mission Control task detail remediation panels/actions
├── entrypoints/task-detail.test.tsx    # focused UI behavior tests
├── generated/openapi.ts                # regenerated if API contract changes
└── styles/mission-control.css          # remediation panel accessibility/responsiveness

tests/
├── unit/workflows/temporal/test_temporal_service.py
├── unit/workflows/temporal/test_remediation_context.py
└── integration/temporal/               # add integration_ci route/service boundary coverage when backend contract changes
```

**Structure Decision**: Use the existing web application plus FastAPI/Temporal service structure. The feature is a Mission Control UI and execution API contract story; no new package, service, or persistent table is planned.

## Complexity Tracking

No constitution violations identified.

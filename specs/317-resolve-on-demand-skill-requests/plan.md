# Implementation Plan: Resolve On-Demand Skill Requests

**Branch**: `run-jira-orchestrate-for-mm-614-resolve-a-7bdf3ad6` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/317-resolve-on-demand-skill-requests/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` is not used in this managed run because the orchestrator already selected the feature directory and branch naming is managed externally. Artifacts are maintained for the feature directory recorded in `.specify/feature.json`.

## Summary

MM-614 is a single-story runtime feature for enabled-mode `moonmind.skills.request`. Current code already has disabled request denial, query support, existing `ResolvedSkillSet` and materialization primitives, and an activity entrypoint, but enabled request mode still returns `enabled_mode_not_implemented`. The implementation will extend typed request/result contracts, service validation and derivation behavior, and the activity boundary so allowed requests produce a compact derived snapshot result while failures and no-change cases preserve the active snapshot.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `agent_skill.request_on_demand`; service currently denies enabled mode | implement governed enabled request path | unit + activity integration |
| FR-002 | partial | request model exists but enabled validation is incomplete | validate snapshot, skills, versions, reason, runtime_id, step_id | unit |
| FR-003 | missing | no enabled-mode shape validation beyond disabled path | add invalid request denials | unit |
| FR-004 | partial | disabled path preserves snapshot; enabled failures not implemented | preserve active snapshot on all denials/errors | unit + activity integration |
| FR-005 | partial | resolver enforces normal selection gates when called | route requested additions through resolver with combined selector | unit + activity integration |
| FR-006 | missing | enabled request always denied as not implemented | add no_change result | unit |
| FR-007 | missing | no derived snapshot creation for enabled request | add derived immutable snapshot result | unit + activity integration |
| FR-008 | partial | current status literal only supports `ok`/`denied`; request uses denied placeholder | expand request status contract to activated/denied/no_change | unit |
| FR-009 | missing | result lacks activation/materialization metadata | add compact refs and activation guidance | unit + activity integration |
| FR-010 | partial | `ResolvedSkillSet.source_trace` can carry metadata but request path does not populate lineage | add compact lineage metadata | unit |
| FR-011 | implemented_unverified | models are compact today; derived result must stay compact | add serialization guard for no Skill bodies/body refs | unit |
| FR-012 | partial | disabled and invalid placeholders exist; other codes absent | add safe failure code vocabulary and mapping | unit |
| FR-013 | implemented_verified | `MM-614` preserved in `spec.md` and this plan | preserve through tasks/verification | final verify |
| SCN-001 | missing | no no_change enabled test | add no_change test | unit |
| SCN-002 | missing | no activated enabled test | add activated test | unit + activity integration |
| SCN-003 | missing | no invalid enabled tests | add validation tests | unit |
| SCN-004 | missing | no policy/version/runtime denial tests | add resolver failure mapping tests | unit |
| SCN-005 | missing | no lineage/activation metadata tests | add result metadata tests | unit + activity integration |
| DESIGN-REQ-002 | missing | enabled request path not implemented | add request resolution to derived snapshot | unit + activity integration |
| DESIGN-REQ-004 | partial | request model has fields | add enabled validation behavior | unit |
| DESIGN-REQ-005 | missing | activated/no_change statuses absent from request result | update contract/result behavior | unit |
| DESIGN-REQ-008 | partial | resolver/materializer primitives exist; activity does not orchestrate enabled request | integrate resolve + persist + materialize boundary | activity integration |
| DESIGN-REQ-013 | partial | lineage can live in `source_trace`; not populated | add source_trace lineage | unit |
| DESIGN-REQ-014 | partial | disabled denial only | map common failure codes without snapshot mutation | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing agent skill resolver and materializer services  
**Storage**: Existing artifact-backed skill body/manifest storage only; no new persistent tables  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest activity-boundary coverage through `ActivityEnvironment`; hermetic integration via `./tools/test_integration.sh` for broader CI if needed  
**Target Platform**: MoonMind managed runtime workers on Linux containers  
**Project Type**: Python service/workflow runtime boundary  
**Performance Goals**: Request/result payloads remain compact and carry refs/metadata rather than Skill bodies  
**Constraints**: Preserve active snapshots on failure; do not expose hidden Skill bodies, arbitrary content refs, or secrets; do not mutate `.agents/skills` mid-turn beyond existing materialization boundary  
**Scale/Scope**: One enabled-mode request story; adapter-specific live refresh remains scoped to MM-615

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. MoonMind, not the agent, validates and resolves requested Skills.
- II. One-Click Agent Deployment: PASS. No new required external service or secret is introduced.
- III. Avoid Vendor Lock-In: PASS. Behavior lives in runtime-neutral schema/service/activity boundaries.
- IV. Own Your Data: PASS. Skill snapshots and manifests remain operator-controlled artifacts/paths.
- V. Skills Are First-Class and Easy to Add: PASS. Requests use Skill selectors and existing resolver mechanics.
- VI. Replaceable Scaffolding: PASS. Thin contracts preserve future approval and refresh evolution.
- VII. Runtime Configurability: PASS. Existing Skills On Demand feature flag continues to control request availability.
- VIII. Modular and Extensible Architecture: PASS. Changes stay in existing schema, service, resolver, materializer, and activity modules.
- IX. Resilient by Default: PASS. Failure paths are explicit and preserve prior active snapshots.
- X. Facilitate Continuous Improvement: PASS. Results carry compact denial/activation metadata suitable for summaries.
- XI. Spec-Driven Development: PASS. Implementation follows this feature's spec/plan/tasks.
- XII. Canonical Docs vs Migration Backlog: PASS. Canonical docs are treated as source requirements; rollout notes stay in feature artifacts.
- XIII. Pre-release Compatibility: PASS. Internal request contracts can be updated directly without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/317-resolve-on-demand-skill-requests/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skills-on-demand-request-contract.md
└── tasks.md
```

### Source Code

```text
moonmind/
├── schemas/
│   ├── __init__.py
│   └── agent_skill_models.py
├── services/
│   └── skills_on_demand.py
└── workflows/agent_skills/
    └── agent_skills_activities.py

tests/
├── unit/workflows/agent_skills/test_skills_on_demand_controls.py
└── integration/temporal/test_skills_on_demand_request_activation.py
```

**Structure Decision**: Keep request activation in the existing Skills On Demand service and Temporal activity boundary, using focused tests for service logic plus activity invocation shape.

## Complexity Tracking

No constitution violations are planned.

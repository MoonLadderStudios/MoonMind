# Implementation Plan: Policy-Aware Skill Query

**Branch**: `run-jira-orchestrate-for-mm-613-expose-p-c2a84b3f` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/316-policy-aware-skill-query/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not run because the managed branch name is not numeric. Artifacts are generated manually for the resolved feature directory recorded in `.specify/feature.json`.

## Summary

Implement MM-613 by turning the existing disabled-first Skills On Demand query placeholder into a policy-aware metadata query for managed runtimes. The current repository already has settings, activities, disabled behavior, and basic query/request models, but enabled query mode returns `enabled_mode_not_implemented` and the query result shape is too generic. The technical approach is to extend the typed query contracts, add a service-level catalog search over resolver-backed Skill metadata, preserve disabled semantics, keep request activation out of scope, and verify through unit tests plus a Temporal activity-boundary integration-style test.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/agent_skills/agent_skills_activities.py` exposes `agent_skill.query_on_demand`; service denies when enabled | implement enabled metadata query behind the existing governed activity/service path | unit + activity boundary |
| FR-002 | partial | `SkillsOnDemandQueryRequest.max_results` has bounds, but query/runtime/snapshot validation is incomplete | validate blank query, runtime id, snapshot ref format/presence rules, and result limits | unit |
| FR-003 | missing | `SkillsOnDemandQueryResult.results` is untyped `dict[str, Any]` and enabled query returns empty denial | add typed metadata result model and populate bounded results | unit + activity boundary |
| FR-004 | partial | `ResolvedSkillEntry` includes body refs, and disabled query returns none; enabled safe projection is missing | project only safe metadata and exclude content refs/body data | unit |
| FR-005 | missing | no enabled query implementation checks active snapshot membership | compare result names with provided active snapshot data or snapshot context where available | unit |
| FR-006 | partial | `AgentSkillResolver` enforces source policy for resolution, but query does not use it | reuse resolver/catalog loaders or resolver-backed candidates with allowed source controls | unit + activity boundary |
| FR-007 | missing | no enabled ineligible match behavior exists | return eligible false diagnostics for runtime/source mismatches or filter where appropriate | unit |
| FR-008 | implemented_unverified | query path currently does not call materializer, but enabled implementation is absent | add tests proving query does not materialize or mutate snapshots | unit + activity boundary |
| FR-009 | partial | max_results is bounded, but result payload fields are not constrained | enforce max result count and typed compact result fields | unit |
| FR-010 | missing | no query observability metadata is recorded for enabled query | add compact outcome metadata or deterministic result metadata suitable for audit integration | unit |
| FR-011 | implemented_unverified | `spec.md` preserves MM-613 and the canonical brief | preserve traceability in plan, tasks, implementation notes, verification, and delivery metadata | final verify |
| SCN-001 | missing | enabled query returns not implemented | add success-path query scenario | unit + activity boundary |
| SCN-002 | partial | pydantic bounds cover some max_results cases | add invalid query shape tests | unit |
| SCN-003 | missing | no ineligible result path | add ineligible/filter diagnostic tests | unit |
| SCN-004 | missing | no enabled result projection | add no body/content-ref exposure tests | unit |
| SCN-005 | missing | no active-snapshot membership logic | add current snapshot membership tests | unit |
| DESIGN-REQ-002 | partial | disabled behavior prevents body exposure, enabled behavior absent | implement metadata-only query | unit + activity boundary |
| DESIGN-REQ-003 | partial | query currently does not mutate snapshots, but no enabled behavior | preserve immutable snapshot/no materialization behavior in enabled query | unit + activity boundary |
| DESIGN-REQ-010 | partial | request model exists but result contract and enabled behavior incomplete | add typed query/result contracts and validation | unit |
| DESIGN-REQ-013 | missing | no enabled search or audit outcome | implement enabled search and compact outcome metadata | unit + activity boundary |
| DESIGN-REQ-014 | partial | disabled path is safe; enabled path absent | enforce metadata-only and policy-safe output | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing agent skill resolver/materializer services  
**Storage**: No new persistent storage; query outputs and compact audit metadata are deterministic runtime/activity results  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest activity-boundary coverage using existing Temporal `ActivityEnvironment`; hermetic integration runner available through `./tools/test_integration.sh` if a broader worker boundary is added  
**Target Platform**: MoonMind managed runtime workers on Linux containers  
**Project Type**: Python service/workflow runtime boundary  
**Performance Goals**: Query results are bounded by accepted `max_results` and contain metadata-only payloads suitable for workflow/activity transport  
**Constraints**: No Skill bodies, content refs, secrets, unchecked local-only access, or snapshot mutation from query calls; unsupported runtime/source values fail safely  
**Scale/Scope**: One managed-runtime query story for Skills On Demand; Skill activation requests remain out of scope

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Query remains MoonMind-mediated and does not ask agents to resolve Skills locally.
- II. One-Click Agent Deployment: PASS. No new external service or required secret is planned.
- III. Avoid Vendor Lock-In: PASS. Behavior lives in MoonMind Skill service/activity contracts, not a provider-specific adapter.
- IV. Own Your Data: PASS. Query uses operator-controlled Skill catalog data and returns portable typed metadata.
- V. Skills Are First-Class and Easy to Add: PASS. The query exposes discoverable metadata without changing Skill authoring mechanics.
- VI. Replaceable Scaffolding: PASS. Thin service contracts and tests isolate likely future request/activation expansion.
- VII. Runtime Configurability: PASS. Existing `skills_on_demand_enabled` setting remains the controlling flag.
- VIII. Modular and Extensible Architecture: PASS. Changes stay in schema/service/activity boundaries.
- IX. Resilient by Default: PASS. Query is side-effect free and activity-boundary tests cover workflow-facing behavior.
- X. Facilitate Continuous Improvement: PASS. Query outcome metadata is planned for operator-visible diagnostics.
- XI. Spec-Driven Development: PASS. Work starts from this spec, plan, tasks, and verification flow.
- XII. Canonical Docs vs Migration Backlog: PASS. Canonical docs are source requirements; implementation notes stay in `specs/316-policy-aware-skill-query`.
- XIII. Pre-release Compatibility: PASS. Internal enabled-mode placeholder contracts can be replaced directly; no compatibility aliases are planned.

## Project Structure

### Documentation (this feature)

```text
specs/316-policy-aware-skill-query/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skills-on-demand-query-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/agent_skill_models.py
├── services/
│   ├── skill_resolution.py
│   └── skills_on_demand.py
└── workflows/agent_skills/agent_skills_activities.py

tests/
├── unit/services/test_skill_resolution.py
└── unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

**Structure Decision**: Implement the query contract in the existing Python schema/service/workflow activity modules, with focused unit and activity-boundary tests under the existing agent skill test areas.

## Complexity Tracking

No constitution violations are planned.

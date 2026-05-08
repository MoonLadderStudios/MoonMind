# Implementation Plan: Policy-Aware Skill Query

**Branch**: `run-jira-orchestrate-for-mm-613-expose-p-c2a84b3f` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/316-policy-aware-skill-query/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not run because the managed branch name is not numeric. Artifacts are maintained manually for the resolved feature directory recorded in `.specify/feature.json`.

## Summary

MM-613 is a single-story runtime feature for policy-aware Skill metadata discovery. The current repository now has typed Skills On Demand query contracts, enabled metadata search, safe metadata projection, active-snapshot membership detection, source-policy eligibility summaries, and a Temporal activity-boundary path. The plan remains scoped to the existing schema/service/activity boundary and preserves explicit unit and integration-style activity test strategy for continued verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/agent_skills/agent_skills_activities.py`; `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` | no new implementation | final verify |
| FR-002 | implemented_verified | `moonmind/schemas/agent_skill_models.py`; `moonmind/services/skills_on_demand.py`; focused validation tests | no new implementation | final verify |
| FR-003 | implemented_verified | `SkillCatalogSearchResult` in `moonmind/schemas/agent_skill_models.py`; enabled metadata tests | no new implementation | final verify |
| FR-004 | implemented_verified | safe projection in `moonmind/services/skills_on_demand.py`; serialization tests omit body refs, digests, and source paths | no new implementation | final verify |
| FR-005 | implemented_verified | active snapshot membership logic in `moonmind/services/skills_on_demand.py`; membership unit test | no new implementation | final verify |
| FR-006 | implemented_verified | `AgentSkillResolver.query_catalog`; source eligibility tests | no new implementation | final verify |
| FR-007 | implemented_verified | repo/local eligibility summaries in `moonmind/services/skills_on_demand.py`; ineligible match test | no new implementation | final verify |
| FR-008 | implemented_verified | query path has no materialization call; activity test patches materializer and asserts it is not called | no new implementation | final verify |
| FR-009 | implemented_verified | `max_results` contract and bounded query tests | no new implementation | final verify |
| FR-010 | implemented_verified | compact result metadata includes result count, denial state, and query hash | no new implementation | final verify |
| FR-011 | implemented_verified | MM-613 preserved in `spec.md`, `plan.md`, `tasks.md`, and `verification.md` | no new implementation | final verify |
| SCN-001 | implemented_verified | enabled query returns `ok` with metadata-only result | no new implementation | final verify |
| SCN-002 | implemented_verified | blank query and blank context validation tests | no new implementation | final verify |
| SCN-003 | implemented_verified | ineligible source match test | no new implementation | final verify |
| SCN-004 | implemented_verified | unsafe serialization omissions test | no new implementation | final verify |
| SCN-005 | implemented_verified | current snapshot membership test | no new implementation | final verify |
| DESIGN-REQ-002 | implemented_verified | metadata-only query behavior and no body exposure tests | no new implementation | final verify |
| DESIGN-REQ-003 | implemented_verified | query side-effect test and no materialization assertion | no new implementation | final verify |
| DESIGN-REQ-010 | implemented_verified | typed query/result contract and validation tests | no new implementation | final verify |
| DESIGN-REQ-013 | implemented_verified | activity-boundary query test and compact metadata | no new implementation | final verify |
| DESIGN-REQ-014 | implemented_verified | policy-safe output projection and ineligible diagnostics | no new implementation | final verify |

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
- X. Facilitate Continuous Improvement: PASS. Query outcome metadata is compact and suitable for operator-visible diagnostics.
- XI. Spec-Driven Development: PASS. Work is traced through spec, plan, tasks, and verification artifacts.
- XII. Canonical Docs vs Migration Backlog: PASS. Canonical docs are source requirements; implementation notes stay in `specs/316-policy-aware-skill-query`.
- XIII. Pre-release Compatibility: PASS. Internal enabled-mode placeholder contracts were replaced directly; no compatibility aliases were introduced.

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
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   ├── __init__.py
│   └── agent_skill_models.py
├── services/
│   ├── skill_resolution.py
│   └── skills_on_demand.py
└── workflows/agent_skills/agent_skills_activities.py

tests/
└── unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

**Structure Decision**: Keep the query contract in the existing Python schema/service/workflow activity modules, with focused unit and activity-boundary tests under the existing agent skill test area.

## Complexity Tracking

No constitution violations are planned.

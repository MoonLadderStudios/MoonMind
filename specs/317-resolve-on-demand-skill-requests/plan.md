# Implementation Plan: Resolve On-Demand Skill Requests

**Branch**: `run-jira-orchestrate-for-mm-614-resolve-a-7bdf3ad6` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/317-resolve-on-demand-skill-requests/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` is not used in this managed run because the orchestrator already selected the feature directory and branch naming is managed externally. Artifacts are maintained for the feature directory recorded in `.specify/feature.json`.

## Summary

MM-614 is a single-story runtime feature for enabled-mode `moonmind.skills.request`. The implementation extends typed request/result contracts, service validation and derivation behavior, and the activity boundary so already-active requests return `no_change`, allowed requests produce compact derived snapshot results, and failures preserve the active snapshot with structured denial data. Focused unit and activity-boundary integration tests now cover validation, no-change, activation metadata, lineage, compact serialization, and materialization failure preservation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `agent_skill.request_on_demand`; `tests/integration/temporal/test_skills_on_demand_request_activation.py` | no new implementation | final verify |
| FR-002 | implemented_verified | `SkillsOnDemandService._validate_activation_request`; enabled validation unit tests | no new implementation | final verify |
| FR-003 | implemented_verified | invalid snapshot/requested skill validation tests in `test_skills_on_demand_controls.py` | no new implementation | final verify |
| FR-004 | implemented_verified | denial helpers and materialization failure activity test preserve parent snapshot | no new implementation | final verify |
| FR-005 | implemented_verified | activity route calls `AgentSkillResolver.resolve` for requested additions; activity-boundary test asserts invocation | no new implementation | final verify |
| FR-006 | implemented_verified | `test_enabled_request_returns_no_change_for_already_active_skills` | no new implementation | final verify |
| FR-007 | implemented_verified | activated result and activity-boundary derived snapshot tests | no new implementation | final verify |
| FR-008 | implemented_verified | `SkillsOnDemandRequestStatus` supports `activated`, `denied`, and `no_change`; unit tests assert outcomes | no new implementation | final verify |
| FR-009 | implemented_verified | request result fields and materialization summary assertions in unit/activity tests | no new implementation | final verify |
| FR-010 | implemented_verified | `skillsOnDemandLineage` source trace and metadata tests | no new implementation | final verify |
| FR-011 | implemented_verified | compact serialization test asserts Skill body refs are omitted from request results | no new implementation | final verify |
| FR-012 | implemented_verified | structured validation, resolver, and materialization failure code coverage | no new implementation | final verify |
| FR-013 | implemented_verified | `MM-614` preserved in `spec.md` and this plan | preserve through tasks/verification | final verify |
| SCN-001 | implemented_verified | no-change unit test | no new implementation | final verify |
| SCN-002 | implemented_verified | activated service and activity-boundary tests | no new implementation | final verify |
| SCN-003 | implemented_verified | invalid request shape unit tests | no new implementation | final verify |
| SCN-004 | implemented_verified | resolver/materialization failure mapping tests | no new implementation | final verify |
| SCN-005 | implemented_verified | lineage and activation metadata assertions | no new implementation | final verify |
| DESIGN-REQ-002 | implemented_verified | enabled request path resolves additions into derived snapshot metadata | no new implementation | final verify |
| DESIGN-REQ-004 | implemented_verified | request contract fields and validation tests | no new implementation | final verify |
| DESIGN-REQ-005 | implemented_verified | request result statuses and compact result fields | no new implementation | final verify |
| DESIGN-REQ-008 | implemented_verified | activity integrates resolve, manifest persistence path, materialization, and compact summary | no new implementation | final verify |
| DESIGN-REQ-013 | implemented_verified | compact `skillsOnDemandLineage` metadata in derived snapshots | no new implementation | final verify |
| DESIGN-REQ-014 | implemented_verified | denial helpers preserve parent snapshot and safe codes | no new implementation | final verify |

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

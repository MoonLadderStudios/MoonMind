# Implementation Plan: Disabled Skills On Demand Controls

**Branch**: `315-disabled-skills-on-demand-controls` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:dbba0e4a-6d65-495d-935e-d128cd7379e3/repo/specs/315-disabled-skills-on-demand-controls/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` failed on the managed branch name `run-jira-orchestrate-for-mm-612-add-disa-1222f797`. It was rerun successfully with `SPECIFY_FEATURE=315-disabled-skills-on-demand-controls`, producing this feature directory and plan path.

## Summary

MM-612 requires Skills On Demand to be a disabled-by-default managed-runtime capability. Current repo evidence shows the normal initial `ResolvedSkillSet` resolution, materialization, and activation-summary paths already exist, but no explicit Skills On Demand configuration, query/request contract, disabled response model, or disabled activation messaging exists. The implementation approach is to add one safe global feature gate, route all on-demand query/request entrypoints through a single disabled-first service boundary before catalog lookup or resolution, and update runtime activation preparation so disabled deployments either hide commands or state that Skills On Demand is disabled. Tests are planned test-first: settings alias/unit coverage, service contract unit tests, activity/runtime activation boundary tests, and hermetic integration coverage for a managed runtime attempting query/request while disabled.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `moonmind/config/settings.py` has workflow Skill settings but no `skills_on_demand_enabled` field | Add one global disabled-by-default setting | unit |
| FR-002 | missing | No `MOONMIND_SKILLS_ON_DEMAND_ENABLED` or `WORKFLOW_SKILLS_ON_DEMAND_ENABLED` references in code/tests | Add both aliases for the same setting | unit |
| FR-003 | missing | No on-demand query command or disabled query response exists | Add disabled-first query handler returning `denied`, `feature_disabled`, and no results | unit + integration |
| FR-004 | missing | No on-demand request command or derived snapshot guard exists | Add disabled-first request handler that does not invoke resolution or materialization | unit + integration |
| FR-005 | partial | Existing runtime instruction text for selected Skills does not expose on-demand commands, but this is incidental because no on-demand commands exist | Make disabled command exposure explicit in runtime activation/control-surface preparation | unit |
| FR-006 | missing | No disabled activation message for Skills On Demand is emitted | Add disabled activation text when commands cannot be hidden | unit |
| FR-007 | implemented_unverified | `AgentSkillsActivities.resolve_skills`, `AgentSkillResolver`, `AgentSkillMaterializer`, and runtime activation paths already support initial selected Skill snapshots | Preserve existing initial resolution/materialization behavior and add regression coverage proving disabled on-demand does not change it | unit + integration |
| FR-008 | implemented_unverified | Existing resolver enforces source and policy inputs; no on-demand path exists to bypass them | Ensure enabled-capable contract remains policy-aware and disabled gate is only a callability gate | unit |
| FR-009 | implemented_unverified | `spec.md` preserves MM-612 and the original preset brief | Preserve MM-612 through plan, research, contracts, quickstart, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | missing | No default-disabled runtime behavior test exists | Add unset-setting coverage | unit + integration |
| SCN-002 | missing | No alias coverage for the two required setting names exists | Add explicit false-value coverage for both names | unit |
| SCN-003 | missing | No disabled query/request command behavior exists | Add query and request denial tests | unit + integration |
| SCN-004 | partial | No on-demand commands are exposed today, but there is no explicit disabled activation contract | Add activation text/control-surface tests | unit |
| SCN-005 | implemented_unverified | Initial selected Skill activation path exists | Add regression proving initial Skill snapshot remains available while on-demand is disabled | unit + integration |
| DESIGN-REQ-001 | implemented_unverified | Existing initial `ResolvedSkillSet` flow and compact refs exist in `moonmind/schemas/agent_skill_models.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, and materialization services | Preserve initial path and add disabled on-demand sidecar without replacing the initial flow | unit + integration |
| DESIGN-REQ-011 | missing | Source doc states disabled behavior; no runtime contract implements `feature_disabled` denial | Implement disabled query/request denial and no-snapshot guard | unit + integration |
| DESIGN-REQ-012 | missing | No global feature gate with required aliases exists | Add setting, validation, and policy-aware contract | unit |
| SC-001 | missing | No unset/default-disabled automated evidence exists | Add default-disabled query/request proof | unit + integration |
| SC-002 | missing | No alias-specific automated evidence exists | Add both alias tests | unit |
| SC-003 | missing | No disabled query results contract exists | Assert zero results | unit + integration |
| SC-004 | missing | No derived snapshot guard exists | Assert resolver/materializer are not invoked while disabled | unit + integration |
| SC-005 | implemented_unverified | Initial Skill flow exists but is not tested with disabled on-demand attempts | Add regression around selected Skill availability | unit + integration |
| SC-006 | implemented_unverified | `spec.md` preserves source traceability | Keep traceability in downstream artifacts | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Pydantic Settings, Temporal Python SDK activity boundaries, existing MoonMind agent-skill resolver/materializer services, pytest  
**Storage**: No new persistent storage; disabled on-demand results and activation metadata are deterministic runtime outputs only  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets under `tests/unit/config`, `tests/unit/workflows/agent_skills`, `tests/unit/workflows/temporal`, and existing skill-resolution/materialization tests  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage when exercising worker/runtime boundaries; otherwise focused Temporal/activity boundary tests in the required unit suite  
**Target Platform**: Linux managed-agent worker containers and local Docker Compose deployments  
**Project Type**: Python service/runtime orchestration with managed-agent control surfaces  
**Performance Goals**: Disabled query/request checks complete before catalog lookup or skill resolution and do not scan Skill sources; activation text changes are constant-size  
**Constraints**: Default must be false; support both required setting aliases; do not create derived `ResolvedSkillSet` when disabled; keep full Skill bodies out of workflow history; preserve existing initial Skill resolution and materialization semantics; keep MM-612 traceability  
**Scale/Scope**: One story covering configuration, disabled query/request command contracts, activation messaging, no-snapshot guard, initial Skill flow regression tests, and traceability

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan adds a controlled MoonMind runtime gate and does not replace provider agent behavior.
- **II. One-Click Agent Deployment**: PASS. The new capability is disabled by default and needs no new external dependency or secret.
- **III. Avoid Vendor Lock-In**: PASS. The disabled control is managed-runtime contract behavior, not provider-specific behavior.
- **IV. Own Your Data**: PASS. No external data store is introduced; outputs are local runtime metadata and responses.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skills remain resolved through existing Skill models; on-demand commands are not added to `ResolvedSkillSet`.
- **VI. Evolving Scaffolds**: PASS. Work is scoped behind settings and runtime command/service boundaries with contract tests.
- **VII. Runtime Configurability**: PASS. The feature is explicitly governed by namespaced operator configuration with safe defaults.
- **VIII. Modular Architecture**: PASS. Planned work is scoped to settings, agent-skill runtime services, activation preparation, and tests.
- **IX. Resilient by Default**: PASS. Disabled calls fail deterministically before expensive or state-changing work.
- **X. Continuous Improvement**: PASS. Clear denial results and final evidence are planned.
- **XI. Spec-Driven Development**: PASS. The plan follows `spec.md` and preserves MM-612 traceability.
- **XII. Canonical Docs vs Migration Backlog**: PASS. Implementation planning stays under `specs/315-disabled-skills-on-demand-controls/`; canonical docs remain desired-state references.
- **XIII. Compatibility Policy**: PASS. No compatibility alias or hidden fallback is introduced beyond the two required operator-facing setting names from the source brief.

Post-design re-check: PASS. The design artifacts keep the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/315-disabled-skills-on-demand-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skills-on-demand-disabled-contract.md
└── tasks.md              # Generated later by moonspec-tasks
```

### Source Code (repository root)

```text
moonmind/
├── config/
│   └── settings.py
├── schemas/
│   └── agent_skill_models.py
├── services/
│   ├── skill_resolution.py
│   └── skill_materialization.py
├── workflows/
│   ├── agent_skills/
│   │   └── agent_skills_activities.py
│   └── temporal/
│       └── activity_runtime.py
└── agents/
    └── codex_worker/
        └── worker.py

tests/
├── unit/
│   ├── config/
│   ├── services/
│   └── workflows/
│       ├── agent_skills/
│       └── temporal/
└── integration/
    └── temporal/         # only if unit activity-boundary coverage is insufficient
```

**Structure Decision**: Use existing Python configuration, service, and Temporal activity/runtime boundaries. No frontend, database migration, or new persistent service is planned.

## Complexity Tracking

No constitution violations require justification.

## Agent Context Update

`.specify/scripts/bash/update-agent-context.sh` was run successfully with `SPECIFY_FEATURE=315-disabled-skills-on-demand-controls`. It updated existing agent context files from this plan while preserving managed markers.

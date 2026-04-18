# Implementation Plan: Skill Selection and Snapshot Resolution

**Branch**: `207-skill-selection-snapshot-resolution` | **Date**: 2026-04-18 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story runtime feature specification from `specs/207-skill-selection-snapshot-resolution/spec.md`

## Summary

Implement MM-406 by wiring task-wide and step-specific agent skill intent into the runtime preparation path so MoonMind resolves one immutable, artifact-backed skill snapshot before managed or external runtime launch. Repo inspection shows schemas, resolver services, source policy gates, materialization, activity registration, manifest artifacts, and adapter propagation already exist. The remaining work is to merge task and step selectors deterministically, call `agent_skill.resolve` before launch, thread the compact `ResolvedSkillSet` ref to `MoonMind.AgentRun`, and add boundary tests proving inheritance, pinned failures, artifact discipline, and retry/rerun snapshot reuse.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TaskExecutionSpec.skills`, `TaskStepSpec.skills`; no runtime pre-launch resolution from task/step selectors found | collect effective selector during run preparation and resolve before launch | unit + workflow boundary |
| FR-002 | missing | task and step selector schemas exist; no selector merge helper found | add deterministic task/step selector merge with exclusions preserving task intent | unit |
| FR-003 | implemented_unverified | `AgentSkillResolver.resolve()` returns `ResolvedSkillSet`; pinned mismatch test exists | verify through pre-launch workflow boundary and pinned failure path | unit + workflow boundary |
| FR-004 | implemented_verified | `SkillResolutionContext.allow_repo_skills`; `allow_local_skills`; resolver policy summary tests | no new implementation | final verify |
| FR-005 | implemented_unverified | resolver rejects duplicate same-source entries and pinned mismatches | add or extend pre-launch error propagation coverage | unit + workflow boundary |
| FR-006 | implemented_unverified | `ResolvedSkillSet`; `agent_skill.resolve`; manifest ref persistence | thread manifest/ref through runtime request from task/step selectors | workflow boundary |
| FR-007 | implemented_unverified | `agent_skill.resolve` writes manifest artifact; materializer writes active manifest | assert compact payload and artifact ref behavior at activity/workflow boundary | unit + workflow boundary |
| FR-008 | implemented_verified | `AgentSkillsActivities.resolve_skills`; `AgentSkillResolver`; materializer service | no new implementation beyond workflow call site | final verify |
| FR-009 | implemented_unverified | integration tests cover plan registry snapshot retry/rerun reuse; not task/step resolved snapshots | extend boundary coverage for resolved task/step snapshot reuse | workflow boundary |
| FR-010 | implemented_unverified | `_build_agent_execution_request` propagates `resolved_skillset_ref`; adapter metadata propagation exists | verify launch path receives resolved ref from new pre-launch resolution | unit + workflow boundary |
| FR-011 | implemented_verified | `AgentSkillMaterializer` writes `.agents/skills_active`; test asserts `.agents/skills` is not mutated | no new implementation | final verify |
| FR-012 | implemented_verified | `spec.md`; `docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md` | preserve through tasks, verification, commit, and PR metadata | traceability check |
| DESIGN-REQ-006 | partial | resolver creates immutable snapshot; pre-launch task/step call missing | add pre-launch resolution | unit + workflow boundary |
| DESIGN-REQ-007 | implemented_unverified | manifest artifact persistence exists | assert manifest ref remains compact in launch path | unit + workflow boundary |
| DESIGN-REQ-008 | missing | task and step selectors parse; merge semantics absent | add merge helper and tests | unit |
| DESIGN-REQ-009 | partial | activity/service boundary exists; workflow does not yet invoke it for task/step intent | invoke activity from workflow preparation | workflow boundary |
| DESIGN-REQ-010 | implemented_unverified | resolver fail-fast and snapshot reuse evidence exists in adjacent plan registry path | add selected story coverage | unit + workflow boundary |
| DESIGN-REQ-019 | implemented_unverified | `MoonMind.AgentRun` request carries `resolvedSkillsetRef` | prove new resolved snapshot ref reaches AgentRun without re-resolution | workflow boundary |
| SC-001 | missing | no effective selector merge test | add unit test | unit |
| SC-002 | implemented_unverified | pinned mismatch unit test exists; not pre-launch | add workflow-boundary failure coverage | unit + workflow boundary |
| SC-003 | implemented_unverified | artifact manifest creation exists | add boundary assertion for compact ref | workflow boundary |
| SC-004 | implemented_unverified | existing integration tests cover plan registry snapshot reuse | extend or add focused coverage for resolved task/step snapshot | workflow boundary |
| SC-005 | implemented_unverified | request and adapter propagation tests exist | add launch-path test from selector to request ref | unit + workflow boundary |
| SC-006 | implemented_verified | spec and Jira input preserve MM-406 and source IDs | preserve through tasks and verification | traceability check |

## Technical Context

**Language/Version**: Python 3.12 with Pydantic v2 models and Temporal Python SDK activity boundaries  
**Primary Dependencies**: Pydantic v2, Temporal activity wrappers, existing agent-skill resolver/materializer services, existing task contract models  
**Storage**: Existing artifact-backed skill snapshot manifests; no new persistent database tables planned  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` for focused iteration; final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration checks when Docker is available; targeted Temporal workflow-boundary tests should be unit or local integration where practical  
**Target Platform**: MoonMind API service, Temporal orchestration workflow, `MoonMind.AgentRun`, managed and external runtime adapters  
**Project Type**: Python web service and Temporal workflow system  
**Performance Goals**: Resolve at most once per task step preparation unless explicit re-resolution is requested; workflow payload carries compact refs only  
**Constraints**: Runtime mode; no raw credentials; do not mutate checked-in skill folders; keep large skill bodies and source traces out of workflow history; preserve MM-406 traceability; source loading stays outside deterministic workflow code  
**Scale/Scope**: One runtime story covering task/step selector inheritance, pre-launch snapshot resolution, artifact discipline, and runtime launch ref propagation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The work strengthens orchestration around existing agent runtimes.
- **II. One-Click Agent Deployment**: PASS. No new required external service or secret is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Skill snapshot refs are runtime-neutral and adapter-consumed.
- **IV. Own Your Data**: PASS. Resolved skill manifests stay in operator-controlled artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. The story completes first-class selector and snapshot behavior.
- **VI. Replaceability and Scientific Method**: PASS. Work is test-first with boundary verification.
- **VII. Runtime Configurability**: PASS. Existing source policy flags remain explicit inputs.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay in task contract, resolver, workflow, and adapter boundaries.
- **IX. Resilient by Default**: PASS. Fail-fast resolution prevents runtime launch with invalid skill intent.
- **X. Facilitate Continuous Improvement**: PASS. Final verification will preserve structured outcome evidence.
- **XI. Spec-Driven Development**: PASS. Implementation proceeds from spec, plan, and tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Jira input remains under `docs/tmp/`; canonical docs are source requirements only.
- **XIII. Pre-release Compatibility Policy**: PASS. Internal contract changes should update all callers without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/207-skill-selection-snapshot-resolution/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skill-snapshot-resolution.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/task_contract.py
moonmind/services/skill_resolution.py
moonmind/workflows/agent_skills/agent_skills_activities.py
moonmind/workflows/temporal/workflows/run.py
moonmind/workflows/temporal/workflows/agent_run.py
moonmind/workflows/adapters/codex_session_adapter.py
tests/unit/workflows/tasks/test_task_contract.py
tests/unit/services/test_skill_resolution.py
tests/unit/workflows/agent_skills/test_agent_skills_activities.py
tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py
tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py
docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md
```

**Structure Decision**: Use existing task selector contracts, resolver services, activities, and AgentRun request fields. Add a narrow selector-merge helper and workflow call-site coverage rather than introducing a new persistent model or parallel skill-resolution path.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

## Phase 0: Research Summary

Research classifies MM-406 as a single-story runtime feature. Existing code already validates task and step skill selector shapes, resolves skills through services and activities, writes manifest artifacts, materializes to `.agents/skills_active`, and propagates `resolvedSkillsetRef` into AgentRun requests. The missing runtime behavior is the deterministic merge of task and step selectors plus a workflow preparation call that resolves the effective selector before launch and reuses that compact snapshot ref across retries and reruns.

## Phase 1: Design Artifact Summary

- `research.md`: requirement-by-requirement repo evidence, current gaps, and planned test-first work.
- `data-model.md`: selector, source policy, `ResolvedSkillSet`, and materialization entities.
- `contracts/skill-snapshot-resolution.md`: observable merge, resolution, failure, artifact, and launch-ref behavior.
- `quickstart.md`: focused red-first tests, unit suite, integration command, and traceability checks.

## Post-Design Constitution Re-Check

PASS. The planned design preserves runtime mode, keeps skill content out of workflow history, keeps source loading at activity/service boundaries, and keeps `.agents/skills` as the runtime-visible active path after snapshot materialization.

## Managed Setup Note

The standard `.specify/scripts/bash/setup-plan.sh --json` helper could not be used because the managed runtime branch is `mm-406-29677dad`, not a numbered Moon Spec branch. The feature directory is resolved through `.specify/feature.json` as `specs/207-skill-selection-snapshot-resolution`.

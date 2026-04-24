# Implementation Plan: Agent Skill Catalog and Source Policy

**Branch**: `206-agent-skill-catalog-source-policy` | **Date**: 2026-04-18 | **Spec**: [spec.md](spec.md) 
**Input**: Single-story feature specification from `specs/206-agent-skill-catalog-source-policy/spec.md`

## Summary

Implement MM-405 by closing the remaining policy gap in agent-skill source resolution while preserving the existing deployment-backed skill catalog, immutable skill versions, resolved skill set contracts, and runtime materialization behavior. Repo inspection shows the project already has AgentSkillDefinition, AgentSkillVersion, SkillSet, ResolvedSkillSet, source provenance, local-source gating, and materialization tests. The missing runtime behavior is an explicit repo-source policy gate before selection and materialization. The implementation approach is test-first: add focused resolver tests proving repo sources are excluded when policy forbids them and still participate in precedence when allowed, then update the resolver context and call sites to carry explicit repo-source policy.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/schemas/agent_skill_models.py`; `docs/Tasks/SkillAndPlanContracts.md`; existing schema separation | no new implementation | final verify |
| FR-002 | implemented_verified | `docs/Tasks/SkillAndPlanContracts.md` runtime command boundary; `moonmind/workflows/agent_skills/selection.py` | no new implementation | final verify |
| FR-003 | implemented_verified | `api_service/db/models.py` AgentSkillDefinition and AgentSkillVersion models; `tests/unit/api/test_agent_skills_service.py` | no new implementation | final verify |
| FR-004 | implemented_unverified | `AgentSkillsService.create_version()` creates distinct version rows and duplicate protection exists | add a focused immutability assertion that a second version preserves the first | unit |
| FR-005 | implemented_verified | `AgentSkillSourceKind`; BuiltIn, Deployment, Repo, and Local loaders; resolver tests | no new implementation | final verify |
| FR-006 | implemented_verified | `AgentSkillResolver` precedence order and `test_resolver_respects_precedence` | no new implementation | final verify |
| FR-007 | implemented_verified | `ResolvedSkillEntry.provenance`; resolver and materializer tests assert source kind | no new implementation | final verify |
| FR-008 | partial | local source policy exists through `allow_local_skills`; repo source policy is absent | add explicit repo-source policy flag and honor it before repo loading | unit + integration-style boundary |
| FR-009 | partial | local denied candidates are excluded; repo denied candidates are not explicitly excluded | add repo-denied test and resolver behavior | unit |
| FR-010 | partial | source kind and materialization boundaries exist, but repo/local validation is shallow | preserve fail-closed source gating and add coverage for denied untrusted repo input | unit |
| FR-011 | implemented_verified | `ResolvedSkillSet`; `AgentSkillMaterializer`; `tests/unit/services/test_skill_materialization.py` | no new implementation | final verify |
| FR-012 | implemented_verified | `spec.md`; Jira brief artifact | preserve through tasks, verification, commit, and PR metadata | traceability check |
| DESIGN-REQ-001 | implemented_verified | source docs and schema separation | no new implementation | final verify |
| DESIGN-REQ-002 | implemented_unverified | version rows are immutable by insert-only service path | add focused unit assertion for multi-version preservation | unit |
| DESIGN-REQ-003 | implemented_verified | resolver source precedence and provenance tests | no new implementation | final verify |
| DESIGN-REQ-004 | partial | local policy gate present; repo policy gate missing | add explicit repo policy gate and tests | unit + final verify |
| SC-001 | implemented_verified | existing schemas and docs distinguish contract types | no new implementation | final verify |
| SC-002 | implemented_unverified | service version creation exists | add two-version immutability test | unit |
| SC-003 | implemented_verified | source precedence test covers built-in/deployment; add repo/local allowed coverage if touched | optional test hardening | unit |
| SC-004 | partial | local denial covered; repo denial missing | add repo denial test | unit |
| SC-005 | implemented_verified | provenance assertions in resolver/materializer tests | no new implementation | final verify |
| SC-006 | implemented_verified | materializer writes `.agents/skills_active` and not `.agents/skills` | no new implementation | final verify |
| SC-007 | implemented_verified | spec and Jira input preserve MM-405 | no new implementation | traceability check |

## Technical Context

**Language/Version**: Python 3.12 with Pydantic v2 models, SQLAlchemy async ORM, Temporal Python SDK activity boundaries 
**Primary Dependencies**: Pydantic v2, SQLAlchemy async session fixtures, Temporal activity wrappers, existing agent-skill resolver/materializer services 
**Storage**: Existing agent skill tables and artifact-backed version content; no new persistent tables planned 
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py tests/unit/services/test_skill_materialization.py` for iteration; final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` 
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration checks when Docker is available; no provider verification required 
**Target Platform**: MoonMind API service, Temporal worker activity runtime, managed runtime workspace preparation 
**Project Type**: Python web service and Temporal workflow system 
**Performance Goals**: Skill resolution remains deterministic and proportional to the number of candidate skill directories and deployment skill records; no additional network calls are introduced for repo/local policy gating 
**Constraints**: Runtime mode; no raw credentials; no mutation of checked-in skill folders during materialization; large skill content stays out of workflow history; unsupported policy states fail closed rather than silently enabling untrusted sources; preserve MM-405 traceability 
**Scale/Scope**: One agent-skill source-policy slice covering catalog contracts, immutable versions, source precedence, repo/local policy gates, and runtime materialization boundaries

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The work keeps agent skills as adapter/materialization inputs and does not build a new cognitive engine.
- **II. One-Click Agent Deployment**: PASS. No new required service, external dependency, or secret is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The source-policy model is runtime-neutral and not provider-specific.
- **IV. Own Your Data**: PASS. Skill catalog and resolved snapshots remain operator-controlled data/artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. This story strengthens skill contracts, source provenance, and policy gates.
- **VI. Replaceability and Scientific Method**: PASS. The plan is test-first and narrows work to the missing policy behavior.
- **VII. Runtime Configurability**: PASS. Policy gating is explicit input/config state, not hardcoded trust in repo content.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay in agent-skill resolver/service/activity boundaries.
- **IX. Resilient by Default**: PASS. Denied or unsupported source policy fails closed for untrusted repo/local sources.
- **X. Facilitate Continuous Improvement**: PASS. Verification records MM-405 traceability and policy evidence.
- **XI. Spec-Driven Development**: PASS. Implementation proceeds from this spec/plan/tasks sequence.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Volatile orchestration input remains under `local-only handoffs`; no canonical migration narrative is added.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility aliases or semantic fallbacks are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/206-agent-skill-catalog-source-policy/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── agent-skill-source-policy.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/schemas/agent_skill_models.py
moonmind/services/skill_resolution.py
moonmind/workflows/agent_skills/agent_skills_activities.py
api_service/services/agent_skills_service.py
api_service/db/models.py
tests/unit/services/test_skill_resolution.py
tests/unit/api/test_agent_skills_service.py
tests/unit/services/test_skill_materialization.py
```

**Structure Decision**: Use the existing agent-skill schema, resolver, service, and materialization modules. No new package or persistent table is planned. Add or update focused unit tests around existing resolver/service boundaries, and use final verification to confirm already-covered behavior remains intact.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

## Phase 0: Research Summary

Research classifies MM-405 as a runtime feature. Existing code already implements the majority of the catalog and resolved-snapshot model. The repo policy gap is concrete: `SkillResolutionContext` has `allow_local_skills` but no equivalent explicit `allow_repo_skills`, while `RepoSkillLoader` loads `.agents/skills` whenever `workspace_root` is present. Implementation should add explicit repo-source policy and tests without changing the existing source precedence order when repo sources are allowed.

## Phase 1: Design Artifact Summary

- `research.md`: documents requirement status, repo evidence, and the missing repo-source policy gate.
- `data-model.md`: records existing agent-skill entities and the added policy field semantics.
- `contracts/agent-skill-source-policy.md`: defines resolver input/output behavior and policy decisions.
- `quickstart.md`: defines test-first unit checks, final unit suite, integration command, and traceability checks.

## Post-Design Constitution Re-Check

PASS. The design artifacts preserve runtime mode, keep skill content out of workflow history, keep `.agents/skills` as runtime-visible active input only after resolution/materialization, and add explicit fail-closed policy handling for repo/local sources.

## Managed Setup Note

The active managed runtime branch is `mm-405-759fbff4`, which does not match the numbered feature directory. Use `SPECIFY_FEATURE=206-agent-skill-catalog-source-policy` when running Moon Spec helper scripts so they resolve this feature deterministically.

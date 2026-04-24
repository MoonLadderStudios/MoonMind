# Implementation Plan: Managed Runtime Skill Projection

**Branch**: `[208-managed-runtime-skill-projection]` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/208-managed-runtime-skill-projection/spec.md`

## Summary

MM-407 requires managed runtime preparation to project an immutable resolved skill snapshot into the canonical `.agents/skills` path while keeping the backing store MoonMind-owned and run-scoped. The repo already has two relevant materialization paths: `moonmind/workflows/skills/materializer.py` plus `workspace_links.py` for managed Codex task jobs, and `moonmind/services/skill_materialization.py` for Temporal `agent_skill.materialize`. The service path still writes `.agents/skills_active/active_manifest.json` and explicitly avoids `.agents/skills`; this story will align it with the canonical runtime-visible projection by using shared link validation, writing `_manifest.json`, and adding focused unit and activity-boundary coverage. Existing Codex worker instruction behavior already exposes compact skill-location guidance and will be verified rather than broadly refactored.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/skills/workspace_links.py` projects `.agents/skills`; `moonmind/services/skill_materialization.py` does not | align service materializer projection | unit + activity boundary |
| FR-002 | partial | shared link helper validates symlink target; service path bypasses it | use shared link helper from service materializer | unit |
| FR-003 | implemented_unverified | `workspace_links.py` rejects existing non-symlink path; service lacks coverage | add service test preserving source directories and rejecting incompatible path | unit |
| FR-004 | implemented_unverified | both materializers use run-scoped roots | verify service writes backing store once per call and exposes projection | unit |
| FR-005 | missing | service writes `active_manifest.json`, not `_manifest.json`, and lacks visible-path/backing-path fields | write `_manifest.json` with required metadata | unit |
| FR-006 | implemented_unverified | run materializer links only selected skills; service needs multi-skill proof | add multi-skill selected-only projection test | unit |
| FR-007 | implemented_verified | `workspace_links.py` points `.agents/skills` and `.gemini/skills` to same active store with tests | no new implementation | final regression |
| FR-008 | partial | shared helper reports non-symlink path, but service does not use it and message lacks full remediation | route service through helper and normalize diagnostics | unit |
| FR-009 | implemented_unverified | `worker.py` compact instruction lines mention `.agents/skills` and selected skill path | add/retain focused instruction assertion | unit |
| FR-010 | implemented_unverified | service writes bodies to disk from artifact refs; prompt index only returns refs | verify no full body appears in prompt index/activity output | unit |
| FR-011 | implemented_unverified | service materializes from supplied `ResolvedSkillSet`; no runtime re-resolution in service | add activity-boundary test that materialize consumes supplied snapshot | unit |
| FR-012 | implemented_verified | `spec.md` preserves MM-407 brief and traceability | no code work | final verification |
| DESIGN-REQ-005 | partial | service still exposes `.agents/skills_active`, not canonical `.agents/skills` | align service visible path | unit |
| DESIGN-REQ-011 | implemented_unverified | hybrid mode sets prompt index ref and workspace files | verify hybrid returns prompt ref plus visible workspace | unit |
| DESIGN-REQ-012 | missing | active manifest lacks required visible path, backing path, and source contribution fields | add `_manifest.json` fields | unit |
| DESIGN-REQ-013 | implemented_unverified | local overlay convention exists in resolver tests | ensure runtime projection does not rewrite existing source input dirs | unit |
| DESIGN-REQ-014 | implemented_verified | `.agents/skills` and `.gemini/skills` link to same active store | no new implementation | final regression |
| DESIGN-REQ-015 | implemented_unverified | service boundary exists; workflow payload carries compact refs | add activity-boundary materialization test | unit |
| DESIGN-REQ-016 | partial | activity accepts resolved snapshot and materializes it, but projection is noncanonical | align service materializer | unit |
| DESIGN-REQ-017 | partial | adapter responsibility is documented and Codex worker uses shared links; service path needs canonical projection | align service path and tests | unit |
| DESIGN-REQ-021 | implemented_unverified | materialization takes supplied snapshot | test no resolver call in materialize boundary | unit |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK activity wrappers, pytest, existing artifact service interfaces
**Storage**: Filesystem workspace projection and artifact-backed skill content refs; no new persistent storage
**Unit Testing**: pytest via `./tools/test_unit.sh`; focused iteration with `python -m pytest` for affected tests
**Integration Testing**: Existing hermetic integration suite via `./tools/test_integration.sh`; no new compose dependency expected for this story
**Target Platform**: Linux managed agent worker environments
**Project Type**: Python service/runtime orchestration code
**Performance Goals**: Materialization remains bounded to selected skill count and writes only selected skill files plus one manifest per snapshot
**Constraints**: Do not rewrite checked-in skill folders; fail before runtime launch for unprojectable paths; keep large skill bodies out of workflow history and inline instructions
**Scale/Scope**: One service materializer path, one activity boundary, existing managed runtime instruction coverage

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I, Orchestrate Don't Recreate: PASS. The work stays in adapter/service boundaries and does not add agent cognition.
- Principle V, Skills Are First-Class: PASS. The feature strengthens skill materialization contracts and tests.
- Principle VIII, Modular and Extensible Architecture: PASS. Changes are scoped to `moonmind/services/skill_materialization.py` and boundary tests.
- Principle IX, Resilient by Default: PASS. Incompatible workspace paths fail before runtime launch with actionable diagnostics.
- Principle XI, Spec-Driven Development: PASS. Specification, plan, tasks, implementation, and verification artifacts are produced before completion.
- Principle XII, Canonical Documentation Separates Desired State From Backlog: PASS. Migration notes remain in `local-only handoffs` and this runtime work does not rewrite canonical docs.
- Principle XIII, Pre-Release Compatibility Policy: PASS. No compatibility aliases are introduced; existing internal service semantics are aligned to the canonical path.

## Project Structure

### Documentation (this feature)

```text
specs/208-managed-runtime-skill-projection/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── runtime-skill-projection.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── services/
│ └── skill_materialization.py
├── workflows/
│ ├── agent_skills/
│ │ └── agent_skills_activities.py
│ └── skills/
│ └── workspace_links.py

tests/
└── unit/
 ├── services/
 │ └── test_skill_materialization.py
 └── workflows/
 ├── agent_skills/
 │ └── test_agent_skills_activities.py
 └── test_workspace_links.py
```

**Structure Decision**: Keep materialization behavior in the existing service and shared workspace-link helper. Add tests at the service and Temporal activity boundary rather than introducing a new runtime subsystem.

## Complexity Tracking

No constitution violations.

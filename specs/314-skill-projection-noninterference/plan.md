# Implementation Plan: Skill Projection Noninterference

**Branch**: `314-skill-projection-noninterference` | **Date**: 2026-05-07 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/specs/314-skill-projection-noninterference/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` could not complete because the managed branch is `change-jira-issue-mm-608-to-status-in-pr-995031b1`, not a numeric MoonSpec branch. `.specify/feature.json` already points to this active feature directory, so planning proceeded manually against the active feature artifacts.

## Summary

MM-608 requires managed runtime active skill projection to stop interfering with repo-authored `.agents/skills` sources while preserving a reliable active skill path for agents. Current repo evidence shows substantial implementation already exists in the materializer, workspace link helper, skill loaders, managed runtime instruction preparation, publish filtering, and verifier-skill preflight text. Planning treats the story as runtime work: close remaining verification gaps, harden compatibility surfaces where needed, and prove the behavior with focused unit tests plus a hermetic integration boundary that prepares a managed selected-skill turn in a checkout containing tracked skill sources.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/services/skill_materialization.py` skips alias when `.agents/skills` is a repo-authored directory; unit test exists in `tests/unit/services/test_skill_materialization.py` | Keep behavior; add/confirm end-to-end managed-turn evidence | unit + integration |
| FR-002 | implemented_unverified | Materializer no longer calls preserve/move path in normal flow; source-preservation helpers remain unused | Verify no success-path move/delete and decide whether unused helpers should stay or be removed in implementation | unit + integration |
| FR-003 | implemented_unverified | `_active_backing_dir()` uses `/runtime/skills_active/<snapshot>` outside `repo`; activity runtime passes run-scoped backing root | Strengthen evidence that repo checkout root `skills_active` is not created during managed-session materialization | unit + integration |
| FR-004 | implemented_unverified | `activity_runtime.py` activation summary uses `visiblePath` and selected-skill `SKILL.md` path | Add/confirm tests for repo-authored alias unavailable path and instruction text | unit |
| FR-005 | partial | Materializer metadata includes active skills, backing path, visible path, alias path/status, compatibility paths, manifest path, preservation flag | Verify exact required metadata shape and add missing operator diagnostic fields only if tests expose gaps | unit |
| FR-006 | implemented_unverified | `workspace_links.py` uses `is_moonmind_owned_projection()` and blocks unknown symlinks | Expand alias outcome tests for created/reused/stale-owned/blocked cases | unit |
| FR-007 | implemented_unverified | `_replace_link()` and materializer projection errors fail before launch for unsafe paths | Add assertions for actionable diagnostics across file and unknown symlink cases | unit |
| FR-008 | partial | Normal flow skips repo-authored `.agents/skills`; preserve-and-link helpers still exist without explicit mode/lease semantics | Either remove dormant preserve-and-link helpers or document them as inaccessible; if mode remains, require explicit enum and lease contract | unit |
| FR-009 | implemented_unverified | `BuiltInSkillLoader` no longer uses `Path.cwd()/.agents/skills`; test covers CWD projection | Keep and verify packaged/configured roots do not depend on current workspace projection | unit |
| FR-010 | implemented_unverified | `RepoSkillLoader` and `LocalSkillLoader` reject active projections; tests cover manifest and runtime-root symlinks | Keep and verify diagnostics remain explicit | unit |
| FR-011 | partial | `moonspec-verify` skill text has projection preflight coverage via `tests/unit/agents/test_moonspec_verify_skill.py` | Add runtime/API-level preflight or justify skill-level preflight as the owning boundary during implementation | unit + integration |
| FR-012 | implemented_unverified | `_should_exclude_publish_path()` excludes only symlink projections with manifest or owned roots; tests cover generated symlinks | Add test that real repo-authored `.agents/skills` is not filtered | unit |
| FR-013 | partial | Metadata captures alias availability/status/errors; no dedicated structured event evidence found | Add structured diagnostic payload/log event for skip/block/fail decisions or prove existing metadata is surfaced to operators | unit + integration |
| FR-014 | partial | Multiple unit tests cover original failure surfaces; no single representative integration test found | Add hermetic integration/boundary test proving selected active skills and tracked repo skills coexist through managed turn prep | integration_ci |
| FR-015 | implemented_unverified | `spec.md` preserves MM-608 and original preset brief | Preserve issue key in plan, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_unverified | Materializer preserves existing directory in unit test | Add managed-turn boundary test | unit + integration |
| SCN-002 | implemented_unverified | Activation summary uses active visible path and repo-authored source warning | Confirm exact text and metadata | unit |
| SCN-003 | implemented_unverified | Workspace link helper owns alias safety checks | Add stale-owned replacement and directory/file/unknown symlink table coverage | unit |
| SCN-004 | implemented_unverified | Loader guard tests exist | Keep as regression evidence | unit |
| SCN-005 | partial | Skill-level MoonSpec verify preflight exists | Decide whether workflow/full-suite preflight needs code-level enforcement | unit + integration |
| SCN-006 | implemented_unverified | Publish filter generated-state tests exist | Add real repo-authored preservation case | unit |
| DESIGN-REQ-001 | implemented_unverified | `visiblePath` is source of activation summary and metadata | Strengthen boundary tests | unit + integration |
| DESIGN-REQ-002 | implemented_unverified | Repo directory is skipped/preserved | Strengthen managed-turn evidence | unit + integration |
| DESIGN-REQ-003 | implemented_unverified | Backing path is run-scoped under runtime directory | Verify no checkout contamination | unit + integration |
| DESIGN-REQ-004 | partial | Active path contract exists; alias unavailable path works | Verify full materialization metadata contract | unit |
| DESIGN-REQ-005 | partial | Strategy largely implemented; preserve-and-link helpers remain | Resolve dormant fallback semantics | unit |
| DESIGN-REQ-006 | implemented_unverified | Collision handling blocks unknown symlinks | Expand diagnostic tests | unit |
| DESIGN-REQ-007 | implemented_unverified | Activity runtime validates manifest snapshot and selected skill | Keep boundary coverage | unit |
| DESIGN-REQ-008 | implemented_unverified | Built-in/repo/local loader guards exist | Keep loader tests | unit |
| DESIGN-REQ-009 | partial | Verify preflight and publish filter exist, but representative full workflow evidence is missing | Add integration/boundary evidence | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK activity boundaries, pytest, existing MoonMind agent-skill resolver/materializer services  
**Storage**: Existing artifact-backed resolved skill snapshots and runtime filesystem materialization only; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage if a compose-backed boundary is needed; otherwise a focused Temporal/activity boundary test under unit suite with existing fixtures  
**Target Platform**: Linux managed-agent worker containers and local Docker Compose deployment  
**Project Type**: Python service/runtime orchestration with managed-agent filesystem boundaries  
**Performance Goals**: Skill materialization and projection checks remain bounded to selected skills and a small number of filesystem paths; no broad repo scans during runtime launch  
**Constraints**: Do not mutate checked-in `.agents/skills`; keep large skill bodies out of workflow history; preserve in-flight activity invocation compatibility; fail fast for unsafe projection targets; keep Jira issue `MM-608` traceability  
**Scale/Scope**: One story covering managed active skill materialization, alias safety, loader guards, activation summary metadata, publish filtering, MoonSpec verification preflight, and tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan preserves adapter/runtime boundaries and does not introduce a competing agent behavior layer.
- **II. One-Click Agent Deployment**: PASS. No new mandatory external dependency or secret is planned.
- **III. Avoid Vendor Lock-In**: PASS. The active skill path contract is runtime-adapter oriented and not tied to one provider.
- **IV. Own Your Data**: PASS. Skill bodies and manifests remain local/artifact-backed and visible by reference.
- **V. Skills Are First-Class and Easy to Add**: PASS. The story directly hardens skill resolution/materialization without collapsing skills into tools.
- **VI. Evolving Scaffolds**: PASS. Work stays behind materializer, loader, and adapter contracts with regression tests anchoring behavior.
- **VII. Runtime Configurability**: PASS. Existing configured roots and runtime metadata are preserved; any fallback mode must be explicit if retained.
- **VIII. Modular Architecture**: PASS. Changes are scoped to skill materialization, workspace links, loaders, runtime instruction preparation, verification preflight, publish filtering, and tests.
- **IX. Resilient by Default**: PASS. Unsafe projection collisions fail before runtime launch with actionable diagnostics.
- **X. Continuous Improvement**: PASS. Diagnostics and final verification evidence are planned.
- **XI. Spec-Driven Development**: PASS. This plan follows `spec.md` and preserves traceability.
- **XII. Canonical Docs vs Migration Backlog**: PASS. Planning and rollout details stay under `specs/314-skill-projection-noninterference/`.
- **XIII. Compatibility Policy**: PASS. No compatibility aliases or hidden fallbacks are added; any unsupported projection input fails fast.

Post-design re-check: PASS. The design artifacts below preserve the same boundaries and do not introduce new constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/314-skill-projection-noninterference/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skill-projection-contract.md
└── tasks.md              # Generated later by moonspec-tasks
```

### Source Code (repository root)

```text
moonmind/
├── services/
│   ├── skill_materialization.py
│   └── skill_resolution.py
├── workflows/
│   ├── skills/workspace_links.py
│   └── temporal/activity_runtime.py
├── agents/codex_worker/worker.py
└── schemas/
    ├── agent_skill_models.py
    └── temporal_models.py

tests/
├── unit/services/
│   ├── test_skill_materialization.py
│   └── test_skill_resolution.py
├── unit/workflows/
│   ├── test_workspace_links.py
│   └── temporal/test_agent_runtime_activities.py
├── unit/agents/
│   ├── test_moonspec_verify_skill.py
│   └── codex_worker/test_worker.py
└── integration/
    └── test_skill_projection_noninterference.py  # add only if activity-boundary unit tests cannot prove the original failure mode
```

**Structure Decision**: Use existing Python service/runtime modules and targeted pytest suites. No frontend, database migration, or new persistent service is planned for this story.

## Complexity Tracking

No constitution violations require justification.
## Agent Context Update

`.specify/scripts/bash/update-agent-context.sh` was first attempted without overrides and failed because the managed branch name does not match the active spec directory. It was rerun successfully with `SPECIFY_FEATURE=314-skill-projection-noninterference`, updating existing agent context files from this plan.


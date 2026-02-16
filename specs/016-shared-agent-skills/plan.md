# Implementation Plan: Unified Agent Skills Directory

**Branch**: `016-shared-agent-skills` | **Date**: 2026-02-15 | **Spec**: `specs/016-shared-agent-skills/spec.md`  
**Input**: Feature specification from `/specs/016-shared-agent-skills/spec.md`

## Summary

Introduce a run-scoped Skill Resolver + Materializer that produces one active skills directory and exposes it to both Codex and Gemini through two symlink adapters (`.agents/skills` and `.gemini/skills`). Keep one canonical skill artifact source, enforce integrity checks, and support per-run selection without touching global CLI state.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Celery 5.4, Pydantic settings, existing MoonMind workflow runtime, Codex CLI, Gemini CLI  
**Storage**: Run workspace filesystem under `/work/runs/<run_id>/`, immutable skill cache under `/var/lib/moonmind/skill_cache/<sha256>/`, optional object storage or git-backed skill registry sources  
**Testing**: Unit tests via `./tools/test_unit.sh`; workflow-level smoke checks through Docker Compose worker startup  
**Target Platform**: Linux Docker containers for API + worker processes  
**Project Type**: Backend workflow/runtime orchestration and worker filesystem materialization  
**Performance Goals**: Skill materialization adds bounded startup overhead and reuses immutable cache for repeat runs  
**Constraints**: One skill source-of-truth, no global CLI mutation, deterministic per-run selection, fail-fast verification on invalid artifacts  
**Scale/Scope**: Shared skills runtime for Codex and Gemini workers across queue-driven Spec workflow executions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` remains placeholder content with no enforceable MUST/SHOULD clauses.
- Repository constraints from `AGENTS.md` are enforced:
  - unit validation must run through `./tools/test_unit.sh`;
  - feature numbering uses next global prefix (`016`).

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/016-shared-agent-skills/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── skills-materializer-contract.md
│   └── shared-skills-workspace-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/skills/
├── registry.py                    # existing stage policy resolver
├── runner.py                      # existing stage execution wrapper
├── contracts.py                   # existing stage contracts
├── resolver.py                    # new: per-run skill selection + source resolution
├── materializer.py                # new: fetch/verify/cache/link runtime skill sets
└── workspace_links.py             # new: adapter symlink creation/validation

moonmind/workflows/speckit_celery/
├── workspace.py                   # extend run workspace preparation hooks
└── tasks.py                       # call resolver/materializer before stage dispatch

moonmind/config/
└── settings.py                    # add registry/cache/workspace knobs for skills runtime

celery_worker/
├── speckit_worker.py              # ensure runtime skill adapter checks for Codex path
└── gemini_worker.py               # ensure runtime skill adapter checks for Gemini path

tests/unit/workflows/
├── test_skills_resolver.py
├── test_skills_materializer.py
└── test_workspace_links.py

README.md
docs/
```

**Structure Decision**: Extend the existing `moonmind/workflows/skills/` package with resolver/materializer responsibilities and keep integration points in current Celery workflow runtime, avoiding parallel orchestration systems.

## Phase 0: Research Plan

1. Define artifact trust model (hash, optional signatures, immutable cache semantics).
2. Define selection precedence for queue defaults, workflow defaults, and job overrides.
3. Define workspace link strategy that satisfies Codex and Gemini discovery expectations.
4. Define operational guardrails for headless Gemini/Codex execution.
5. Define migration path from legacy `.codex/skills` assumptions to `.agents/skills` + `.gemini/skills`.

## Phase 1: Design Outputs

- `research.md`: design decisions, rationale, and rejected alternatives.
- `data-model.md`: resolver/materializer entities, validation rules, and state transitions.
- `contracts/skills-materializer-contract.md`: interface contract for resolution/materialization inputs, outputs, and error semantics.
- `contracts/shared-skills-workspace-contract.md`: strict filesystem layout and symlink invariants.
- `quickstart.md`: operator/developer validation flow for shared skills directory behavior.

## Post-Design Constitution Re-check

- Design keeps changes in runtime implementation surfaces and test strategy.
- No constitution-specific violations detected beyond placeholder constitution baseline.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

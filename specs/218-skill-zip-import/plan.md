# Implementation Plan: Skill Zip Import

**Branch**: `218-skill-zip-import` | **Date**: 2026-04-21 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/218-skill-zip-import/spec.md`

## Summary

Deliver MM-397 by hardening the existing Skills Page zip upload into a canonical skill import contract. Repo analysis found a partial `/api/tasks/skills/upload` implementation and frontend upload flow, but it lacked the requested `/api/skills/imports` contract, lowercase `skill.md` support, YAML-frontmatter manifest validation, import metadata response, and canonical frontend endpoint usage. The implementation adds a shared import helper in `api_service/api/routers/task_dashboard.py`, points the Skills Page at `/api/skills/imports`, and expands backend/frontend tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 / DESIGN-REQ-001 | implemented_verified | `api_service/api/routers/task_dashboard.py`; focused backend tests | completed | unit |
| FR-002 / DESIGN-REQ-002 | implemented_verified | `POST /api/skills/imports` route in `task_dashboard.py`; test metadata assertion | completed | unit |
| FR-003 / DESIGN-REQ-003 | implemented_unverified | constants and validation in `task_dashboard.py`; existing size tests not exhaustive | preserve and extend later if needed | final unit |
| FR-004 / DESIGN-REQ-004 | implemented_verified | unsafe path/symlink/device validation and path traversal regression test | completed | unit |
| FR-005 / DESIGN-REQ-005 | implemented_verified | one-directory and one-manifest validation; structure rejection tests | completed | unit |
| FR-006 / DESIGN-REQ-006 | implemented_verified | YAML frontmatter parser and missing/mismatch tests | completed | unit |
| FR-007 / DESIGN-REQ-007 | implemented_verified | valid bundle test preserves `scripts/`, `references/`, `assets/`, and extra files | completed | unit |
| FR-008 / DESIGN-REQ-008 | implemented_verified | import path extracts and copies files only; no subprocess/import execution path exists | completed | unit review |
| FR-009 | implemented_verified | rejected imports assert no skill directory / no temp directory remains | completed | unit |
| FR-010 / DESIGN-REQ-009 | implemented_verified | `SkillImportResponse` and metadata test | completed | unit |
| FR-011 | implemented_verified | default collision rejection test | completed | unit |
| FR-012 | implemented_verified | `frontend/src/entrypoints/skills.tsx` and `skills.test.tsx` canonical endpoint expectation | completed | frontend unit |
| FR-013 | implemented_verified | MM-397 preserved in `spec.md`, `plan.md`, `tasks.md`, and verification | completed | final verification |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Skills Page
**Primary Dependencies**: FastAPI multipart uploads, Python `zipfile`, PyYAML, existing skill resolver helpers, React, TanStack Query, Vitest
**Storage**: Existing configured local skill mirror only; no new persistent tables
**Unit Testing**: `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py -k 'skill_import_api or upload_dashboard_skill_zip'`
**Integration Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/skills.test.tsx`
**Target Platform**: Mission Control Skills Page and FastAPI dashboard routes
**Project Type**: Web application frontend plus FastAPI backend
**Performance Goals**: Validate and save typical skill zips synchronously without executing uploaded code
**Constraints**: Preserve trusted local skill mirror boundary; do not mutate checked-in skill folders; do not execute uploaded scripts; preserve MM-397 traceability
**Scale/Scope**: One upload route module, one Skills Page entrypoint, focused tests

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Extends existing Skills Page and skill mirror path.
- **II. One-Click Agent Deployment**: PASS. No new service dependency.
- **III. Avoid Vendor Lock-In**: PASS. Skill format remains filesystem/Markdown based.
- **IV. Own Your Data**: PASS. Uploaded bundles are stored in operator-controlled local storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. Adds first-class zip import.
- **VI. Evolving Scaffolds**: PASS. Hardens existing route instead of adding a separate importer stack.
- **VII. Runtime Configurability**: PASS. Uses configured local skill mirror root.
- **VIII. Modular Architecture**: PASS. Validation stays at API boundary and UI uses one endpoint.
- **IX. Resilient by Default**: PASS. Invalid imports fail before publishing a skill directory.
- **X. Continuous Improvement**: PASS. Adds regression tests for import behavior.
- **XI. Spec-Driven Development**: PASS. MM-397 artifacts are under `specs/218-skill-zip-import/`.
- **XII. Canonical Documentation Separation**: PASS. Jira orchestration input remains under `docs/tmp`.
- **XIII. Pre-Release Compatibility**: PASS. Canonical endpoint is used by the UI; existing legacy dashboard endpoint delegates to the same helper only for current local callers.

## Project Structure

### Documentation

```text
specs/218-skill-zip-import/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skill-import-api.md
├── tasks.md
└── verification.md
```

### Source Code

```text
api_service/api/routers/task_dashboard.py
frontend/src/entrypoints/skills.tsx
tests/unit/api/routers/test_task_dashboard.py
frontend/src/entrypoints/skills.test.tsx
docs/tmp/jira-orchestration-inputs/MM-397-moonspec-orchestration-input.md
```

## Complexity Tracking

No constitution violations.

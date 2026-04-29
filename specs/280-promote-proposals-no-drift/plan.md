# Implementation Plan: Promote Proposals Without Live Preset Drift

**Branch**: `280-promote-proposals-no-drift`
**Date**: 2026-04-29
**Spec**: `specs/280-promote-proposals-no-drift/spec.md`
**Input**: Single-story runtime feature request from MM-560.

## Summary

Task proposal creation and service-level promotion already validate canonical task payloads and preserve preset provenance metadata. The remaining gap is the promotion API and service allowing a full `taskCreateRequestOverride`, which can replace the reviewed proposal payload at promotion time. This plan removes full replacement promotion, keeps bounded controls (`runtimeMode`, `priority`, `maxAttempts`, `note`), validates the stored flat payload, and adds unit plus API boundary tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `CanonicalTaskPayload` rejects `type: "preset"` in task steps; no explicit proposal promotion regression test | add unit regression test for unresolved preset rejection | unit |
| FR-002 | implemented_verified | `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_preserves_preset_provenance` | keep existing behavior | unit |
| FR-003 | partial | service uses stored payload by default but accepts full override | remove full override path | unit + API |
| FR-004 | partial | `runtimeMode` currently works by constructing a full override | make runtime override bounded and service-owned | unit + API |
| FR-005 | missing | `taskCreateRequestOverride` is accepted by schema/router/service | reject/remove full replacement override | API |
| FR-006 | implemented_verified | `TaskProposalTaskPreview` exposes `presetProvenance`, `authoredPresetCount`, and `stepSourceKinds` | preserve behavior | API |
| SC-001 | partial | provenance test exists, preset rejection test missing | add unit test | unit |
| SC-002 | missing | API accepts override | update API test to reject override | API |
| SC-003 | partial | runtime shortcut API test exists but uses full override internally | update assertion to bounded service argument | API |
| SC-004 | implemented_unverified | spec artifacts preserve IDs | run traceability check | traceability |
| DESIGN-REQ-014 | implemented_verified | task contract rejects ambiguous `preset` step submission; preview uses preset provenance terminology | keep validation and preview terminology | unit/API |
| DESIGN-REQ-018 | partial | provenance preserved; reviewed flat payload can be replaced by override | remove replacement override | unit + API |
| DESIGN-REQ-019 | missing | full override can be used as an implicit refresh path | reject full override | API |

## Technical Context

- Language/version: Python 3.12
- Primary dependencies: FastAPI, Pydantic v2, SQLAlchemy async ORM, existing task contract models
- Storage: existing `task_proposals` table only; no schema changes
- Unit testing: pytest through `./tools/test_unit.sh`
- Integration testing: FastAPI router tests mounted in-process; no Docker required
- Target platform: MoonMind API and Temporal execution submission path
- Project type: backend API/service change with generated frontend OpenAPI type alignment
- Performance goals: no additional external calls or catalog lookups during promotion
- Constraints: no raw Jira credentials; do not add compatibility aliases for removed internal contract behavior
- Scale/scope: one proposal promotion request at a time

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The change preserves existing orchestration and validation surfaces.
- II. One-Click Agent Deployment: PASS. No new deployment dependencies.
- III. Avoid Vendor Lock-In: PASS. Proposal behavior is provider-neutral.
- IV. Own Your Data: PASS. Uses stored proposal payloads, not external live catalog state.
- V. Skills Are First-Class: PASS. Preserves skill/tool distinctions in reviewed payloads.
- VI. Replaceable Scaffolding: PASS. Removes a drift-prone override path and relies on existing contracts.
- VII. Runtime Configurability: PASS. No hardcoded deployment configuration.
- VIII. Modular Architecture: PASS. Changes stay within proposal schema, router, and service boundaries.
- IX. Resilient by Default: PASS. Promotion remains deterministic and validation-first.
- X. Continuous Improvement: PASS. Adds regression tests.
- XI. Spec-Driven Development: PASS. This plan follows the MM-560 spec.
- XII. Canonical Docs Separation: PASS. No canonical docs are changed for implementation notes.
- XIII. Pre-Release Compatibility: PASS. Removes the superseded internal full-override promotion path rather than adding aliases.

## Project Structure

```text
moonmind/
  schemas/task_proposal_models.py
  workflows/task_proposals/service.py
tests/
  unit/api/routers/test_task_proposals.py
  unit/workflows/task_proposals/test_service.py
frontend/src/generated/openapi.ts
specs/280-promote-proposals-no-drift/
```

## Complexity Tracking

No constitution violations or complexity exceptions.

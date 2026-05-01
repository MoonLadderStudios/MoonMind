# Implementation Plan: Author Governed Tool Steps

**Branch**: `289-author-governed-tool-steps` | **Date**: 2026-05-01 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:e448020a-e8e2-4796-b950-9b1c89ba469d/repo/specs/289-author-governed-tool-steps/spec.md`

## Summary

Implement the MM-576 runtime slice by connecting the Create-page Tool authoring panel to MoonMind's trusted typed tool metadata, presenting grouped/searchable Tool choices, deriving basic schema-backed input editing from the selected tool contract, and adding dynamic option support for Jira target statuses through the trusted Jira transition metadata path. Existing MM-563 work already submits manual Tool id/version/input payloads and rejects shell-like task step fields; this story completes the governed authoring experience around selection, discovery, schema guidance, dynamic options, and fail-closed validation. Unit tests cover backend tool metadata shape where needed; frontend tests cover the authoring path, search/grouping, dynamic options, and validation behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/task-create.tsx` has Tool text input; `/api/mcp/tools` lists trusted tool metadata | add grouped/searchable Tool picker backed by metadata | frontend integration |
| FR-002 | partial | manual Tool submissions preserve typed id/version when entered | preserve selected metadata id and resolve default version display when metadata lacks explicit version | frontend integration |
| FR-003 | partial | manual JSON object inputs exist; selected tool schema is not surfaced | render schema-derived input guidance and maintain object payload from schema fields | frontend integration |
| FR-004 | missing | no Create-page dynamic option provider flow for Jira statuses | add trusted Jira transition option lookup for eligible Jira transition Tool fields | frontend integration + unit as needed |
| FR-005 | implemented_unverified | `moonmind/workflows/tasks/task_contract.py` rejects command/script/shell/bash step keys | keep final verification and add regression if touched | unit/final verify |
| FR-006 | partial | task contract validates tool payload presence; UI does not validate selected metadata existence/option failures | block unknown selected tools and fail-closed option provider failures before submit | frontend integration |
| FR-007 | implemented_unverified | Step Type UI already uses Tool/Skill/Preset labels and omits Script | preserve assertions while adding governed picker UI | frontend integration |
| FR-008 | implemented_verified | `spec.md` preserves MM-576 and original brief | preserve in downstream artifacts and delivery metadata | final verify |
| DESIGN-REQ-007 | partial | trusted MCP tool metadata exposes names and schemas; Create page does not consume it | consume metadata in Tool authoring | frontend integration |
| DESIGN-REQ-008 | partial | `/api/mcp/tools` provides list source; no grouped/search UI or dynamic options | implement picker/search/grouping and Jira option provider | frontend integration |
| DESIGN-REQ-019 | partial | backend contract rejects malformed step shapes; UI only validates JSON and missing id | add metadata existence and dynamic-option failure checks | frontend integration |
| DESIGN-REQ-020 | implemented_unverified | UI terminology already says Tool; backend blocks shell keys | keep Tool wording and no Script concept | frontend integration + final verify |
| SC-001 | partial | manual id entry can submit a Tool step | prove picker-driven valid Tool submit | frontend integration |
| SC-002 | missing | no dynamic option UI | prove options are shown and fail closed | frontend integration |
| SC-003 | partial | some invalid submissions blocked | add coverage for unknown tool/option failure alongside existing checks | frontend integration |
| SC-004 | implemented_unverified | task contract has shell-like forbidden keys | preserve unit coverage/final verification | unit/final verify |
| SC-005 | implemented_unverified | existing Step Type tests assert Tool label | extend around picker | frontend integration |
| SC-006 | implemented_verified | source mapping exists in `spec.md` | preserve through tasks and verification | final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for trusted MCP/Jira tool contracts if backend shape changes are needed  
**Primary Dependencies**: React, TanStack Query, Vitest/Testing Library, FastAPI MCP tool registry, Pydantic v2  
**Storage**: Existing task draft and submission payload state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh`; targeted Python tests only if backend metadata changes are required  
**Integration Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for Create-page governed Tool behavior; hermetic integration not required unless backend route behavior changes beyond existing `/api/mcp/tools` contracts  
**Target Platform**: Mission Control Create page and MoonMind task execution API boundary  
**Project Type**: Web app plus Python service contracts  
**Performance Goals**: Tool metadata fetch and filtering remain responsive for the current trusted tool catalog size; no blocking provider option lookup until prerequisite inputs are available  
**Constraints**: Preserve MM-576, keep Jira interactions through trusted MoonMind surfaces, fail closed for unavailable dynamic options, reject arbitrary shell, avoid new storage, and preserve Tool terminology  
**Scale/Scope**: One Create-page Tool authoring path, one trusted metadata fetch path, and Jira transition dynamic options for the target-status example

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - uses trusted Tool metadata and Jira tool surfaces instead of rebuilding integrations.
- II One-Click Agent Deployment: PASS - no new deployment dependency.
- III Avoid Vendor Lock-In: PASS - governed Tool metadata remains provider-neutral; Jira dynamic options are one integration-specific provider behind the typed tool path.
- IV Own Your Data: PASS - no external storage or unmanaged data export.
- V Skills Are First-Class and Easy to Add: PASS - does not mutate agent skill bundle semantics.
- VI Scaffolds Evolve: PASS - thin UI behavior over typed tool contracts, easy to replace with richer schema rendering later.
- VII Runtime Configurability: PASS - no hardcoded operator secrets; unavailable configured tools fail closed.
- VIII Modular Architecture: PASS - existing Create-page and MCP tool registry boundaries are used.
- IX Resilient by Default: PASS - invalid or unauthorized Tool submissions stop before execution.
- X Continuous Improvement: PASS - final verification will publish evidence and residual risks.
- XI Spec-Driven Development: PASS - this plan follows `spec.md` and preserves MM-576 traceability.
- XII Canonical Docs vs Migration Backlog: PASS - implementation notes stay in feature artifacts, not canonical docs.
- XIII Pre-Release Compatibility: PASS - no compatibility aliases or hidden fallback behavior are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/289-author-governed-tool-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── governed-tool-picker.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/generated/openapi.ts
api_service/api/routers/mcp_tools.py
moonmind/mcp/jira_tool_registry.py
moonmind/workflows/tasks/task_contract.py
tests/unit/workflows/tasks/test_task_contract.py
tests/unit/mcp/test_jira_tool_registry.py
```

**Structure Decision**: Prefer the existing Create-page entrypoint and existing MCP tool registry because the story is about authoring trusted Tool steps from already-registered tool metadata. Backend work is limited to metadata or option-provider affordances if existing tool schemas are insufficient for the UI contract.

## Complexity Tracking

No constitution violations. MoonSpec helper scripts referenced by the skill snapshots (`scripts/bash/update-agent-context.sh`, `scripts/bash/check-prerequisites.sh`, and `scripts/bash/setup-plan.sh`) are absent in this checkout, so artifact generation and gate checks are performed manually in this run.

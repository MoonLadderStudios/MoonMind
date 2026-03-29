# Implementation Plan: Task Proposal System Plan Phase 1 and 3

**Branch**: `114-task-proposal-updates` | **Date**: 2026-03-28 | **Spec**: `specs/114-task-proposal-updates/spec.md`
**Input**: Feature specification from `/specs/114-task-proposal-updates/spec.md`

## Summary

The objective is to refactor MoonMind's task proposal workflow generation logic (Phase 1 & Phase 3) to adopt standard `CanonicalTaskPayload` API serialization, consolidate `TaskProposalPolicy` directly within `initialParameters`, implement the missing `defaultRuntime` configurations, eliminate the `agent_runtime` schema reliance, and assert explicit global proposal controls.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Temporal, Pydantic, SQLAlchemy 
**Storage**: PostgreSQL (for proposal records)  
**Testing**: pytest  
**Target Platform**: Linux Server / Docker Worker Fleet  
**Project Type**: single/backend  
**Performance Goals**: Sub-second API lookups  
**Constraints**: Must maintain backward compatibility for active running `MoonMind.Run` task proposals without crashing running workflows.  
**Scale/Scope**: Impacts all generated run tasks queueing to Temporal worker.

## Constitution Check

*GATE: Passed. No new architecture or top-level project layers introduced. Retains native feature boundaries.*

- **No New Translation Layers**: Superseded payload mappings (like `agent_runtime`) will be removed entirely without leaving backward-compat wrappers, though Temporal-facing changes (`TaskProposalPolicy` handling) will maintain execution history safety.
- **Declarative Contracts**: Utilizing preexisting `CanonicalTaskPayload` guarantees adherence directly.
- **Fail Fast Behavior**: Missing runtimes will throw errors gracefully instead of silently dying. 

## Project Structure

### Documentation (this feature)

```text
specs/114-task-proposal-updates/
├── spec.md
├── plan.md              # This file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/requirements-traceability.md
└── tasks.md             # To be populated next
```

### Source Code

```text
moonmind/
├── api/
│   └── routes/
│       └── proposals.py
├── workflows/
│   ├── tasks/
│   │   ├── task_contract.py
│   │   └── proposals.py
│   └── temporal/
│       └── workflows/
│           └── run.py
tests/
├── integration/
└── unit/
```

**Structure Decision**: Single project modifying internal Temporal run workflows and the task serialization contracts. Native MoonMind app directory mapped above.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*No violations.*

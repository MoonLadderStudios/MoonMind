# Implementation Plan: MoonMind.AgentRun Workflow

**Branch**: `073-agent-run-workflow` | **Date**: 2026-03-14 | **Spec**: [link](spec.md)
**Input**: Feature specification from `/specs/073-agent-run-workflow/spec.md`

## Summary

Implement Phase 2 of the Managed and External Agent Execution Model by creating a Temporal child workflow named `MoonMind.AgentRun`. This workflow acts as the unified execution point for true agent runtimes, orchestrating the launch, waiting for runtime events, output collection, and cancellation phases.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Temporal Python SDK (`temporalio`)  
**Storage**: N/A for this phase  
**Testing**: pytest (Temporal test environment)  
**Target Platform**: MoonMind Orchestrator / Temporal Worker  
**Project Type**: Backend (Temporal Workflows)  

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. One-Click Agent Deployment**: No external cloud services required for this local Temporal workflow.
- [x] **II. Avoid Vendor Lock-In**: Implements an `AgentAdapter` interface precisely to avoid vendor lock-in.
- [x] **V. The Bittersweet Lesson**: Thin scaffolding using robust temporal workflows and strict interfaces.
- [x] **VII. Modular and Extensible Architecture**: The generic `MoonMind.AgentRun` isolates workflow execution from adapter logic.
- [x] **VIII. Self-Healing by Default**: Employs durable timers, signals, and non-cancellable scopes for robust state management and cleanup.
- [x] **X. Spec-Driven Development**: All components trace back to DOC-REQ ids.

## Project Structure

### Documentation (this feature)

```text
specs/073-agent-run-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── services/
    └── temporal/
        ├── workflows/
        │   ├── agent_run.py        # Contains MoonMindAgentRun class
        │   └── shared.py           # Contains generic models if applicable
        └── adapters/
            ├── base.py             # AgentAdapter base class
            ├── managed.py          # ManagedAgentAdapter (stub or initial implementation)
            └── external.py         # ExternalAgentAdapter (stub or initial implementation)

tests/
└── services/
    └── temporal/
        └── workflows/
            └── test_agent_run.py   # Unit & Integration tests for AgentRun
```

**Structure Decision**: Python-based services tree for the Temporal worker logic as outlined above.

## Complexity Tracking

None required.

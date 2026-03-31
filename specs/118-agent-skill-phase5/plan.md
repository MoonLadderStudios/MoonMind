# Implementation Plan: Agent Skill System Phase 5

**Branch**: `118-agent-skill-phase5` | **Date**: 2026-03-31 | **Spec**: `specs/118-agent-skill-phase5/spec.md`

## Summary

Expand task and step models to accept `task.skills` and `step.skills` selectors. Explicitly route `agent_skill.resolve` and `agent_skill.materialize` invocations as activities and establish exact payload propagation flows inside `MoonMind.AgentRun` via `resolved_skillset_ref`. 

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Pydantic, Temporalio
**Testing**: pytest (Unit & Integration)
**Target Platform**: MoonMind backend services

## Requirements Traceability

- **DOC-REQ-001**: `task.skills` defined securely in `TaskExecutionSpec` 
- **DOC-REQ-002**: `step.skills` injected cleanly into `Step`
- **DOC-REQ-003**: Inheritance rules verified through tests.
- **DOC-REQ-004**: Structural schema validation enforces selection bounds.
- **DOC-REQ-005**: Define explicit Temporal `agent_skill.*` definitions.
- **DOC-REQ-006**: Configure `agent_skill.*` routing to the agent_runtime queue.
- **DOC-REQ-007**: Define explicitly handled `resolved_skillset_ref` boundaries across activities.
- **DOC-REQ-008**: Update `MoonMind.AgentRun` parameter boundaries to accept refs logic without bodies.
- **DOC-REQ-009**: Confirm logging payloads carry refs and metadata only.
- **DOC-REQ-010**: Pin snapshots during runs to sustain identical deterministic outcomes during retries.
- **DOC-REQ-011**: Execute `pytest test_task_contract.py test_tool_plan_contracts.py`.

## Code Modifications

### 1. `moonmind/workflows/tasks/task_contract.py`
Add structural skill selections to the existing canonical runtime structures.

### 2. `moonmind/workflows/skills/tool_plan_contracts.py` 
Include Step-based node models with `StepSkillSelectors`.

### 3. `moonmind/workflows/temporal/activity_runtime.py` / `worker_runtime.py` 
Wire `agent_skill.*` handlers properly through existing infrastructure. Ensure queues correctly target runtime configurations.

### 4. `moonmind/workflows/temporal/workflows/agent_run.py`
Define boundary arguments mapped onto activities explicitly referencing `resolved_skillset_ref`. 

### 5. `tests/unit/workflows/tasks/test_task_contract.py` & `tests/unit/workflows/skills/test_tool_plan_contracts.py`
Provide the required Test-Driven Development loops to support `TaskSkillSelection` and `StepSkillSelection`.

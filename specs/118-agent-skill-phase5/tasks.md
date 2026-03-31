# Tasks: Agent Skill System Phase 5

**Branch**: `118-agent-skill-phase5`
**Spec**: `specs/118-agent-skill-phase5/spec.md`
**Plan**: `specs/118-agent-skill-phase5/plan.md`

## 1. Schema & Validation Tests (TDD)
- [x] T001 Write failing unit tests in `tests/unit/workflows/tasks/test_task_contract.py` to validate `task.skills` fields on `TaskExecutionSpec`.
- [x] T002 Write failing unit tests in `tests/unit/workflows/skills/test_tool_plan_contracts.py` to validate `step.skills` on `Step`.
- [x] T003 Implement `TaskSkillSelectors` model in `moonmind/workflows/tasks/task_contract.py` and map onto `TaskExecutionSpec.skills`.
- [x] T004 Implement `StepSkillSelectors` model in `moonmind/workflows/skills/tool_plan_contracts.py` and map onto `Step.skills`.
- [x] T005 Ensure unit tests T001 and T002 now pass successfully.

## 2. Temporal Activity Integration
- [x] T006 Add `agent_skill.resolve` and `agent_skill.materialize` route definitions into `moonmind/workflows/temporal/activity_runtime.py`.
- [x] T007 Add `agent_skill.*` binding configuration to the `mm.activity.agent_runtime` queue logic. 

## 3. Workflow Execution Wiring & Ref Handling
- [x] T008 Update `moonmind/workflows/temporal/workflows/agent_run.py` to accept `resolved_skillset_ref` parameter logic.
- [x] T009 Write/verify tests within `tests/unit/workflows/temporal/workflows/test_agent_run.py` to ensure `resolved_skillset_ref` carries correctly.

## 4. Final Validation & Commit
- [x] T010 Execute full `./tools/test_unit.sh` pass.
- [x] T011 Commit changes ensuring all tasks are marked as complete.

# Quickstart: Skill Selection and Snapshot Resolution

## Focused Test-First Flow

1. Add failing unit coverage for effective task/step selector merging:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
```

Expected red cases before implementation:
- step-level exclusion removes inherited task-level include,
- step materialization mode overrides task mode for one step,
- original task selector remains unchanged.

2. Add failing workflow-boundary coverage for pre-launch resolution:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py
```

Expected red cases before implementation:
- effective selector invokes `agent_skill.resolve` or the equivalent boundary before runtime launch,
- `resolvedSkillsetRef` from the manifest/ref reaches the AgentRun request,
- pinned resolution failure stops before AgentRun launch.

3. Verify existing resolver and activity behavior stays green:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/services/test_skill_materialization.py
```

4. Run source traceability check:

```bash
rg -n "MM-406|DESIGN-REQ-006|DESIGN-REQ-007|DESIGN-REQ-008|DESIGN-REQ-009|DESIGN-REQ-010|DESIGN-REQ-019" specs/207-skill-selection-snapshot-resolution
```

5. Run final unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

6. Run hermetic integration verification when Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed runtime, record the exact blocker and rely on focused unit/workflow-boundary evidence plus final MoonSpec verification.

## Story Validation

Submit or simulate a task with:
- task-level skills including at least one pinned include,
- one step-level exclusion,
- one step-level materialization mode override.

Validate that:
- the effective selector reflects step override behavior,
- the original task-level selector is preserved,
- snapshot resolution happens before runtime launch,
- the launch request carries a compact `resolvedSkillsetRef`,
- large skill content remains in artifacts,
- retries/reruns use the same snapshot ref unless explicit re-resolution is requested.

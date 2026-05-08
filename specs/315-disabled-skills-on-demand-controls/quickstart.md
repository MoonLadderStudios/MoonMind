# Quickstart: Disabled Skills On Demand Controls

## Preconditions

- Work from `specs/315-disabled-skills-on-demand-controls/spec.md`.
- Preserve Jira issue key `MM-612` in downstream artifacts and final evidence.
- Use `MOONMIND_FORCE_LOCAL_TESTS=1` for managed-agent local unit verification.
- No new persistent storage or migration setup is expected.

## TDD Flow

1. Add failing settings tests for default disabled behavior and both supported setting names:

```bash
./tools/test_unit.sh tests/unit/config
```

2. Add failing unit tests for disabled on-demand query/request contracts before adding production handlers:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills
```

3. Add failing tests proving disabled request handling does not invoke Skill resolution, artifact persistence, materialization, or derived snapshot activation:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills tests/unit/services/test_skill_resolution.py
```

4. Add failing runtime activation tests for hidden-command and disabled-message behavior:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py
```

5. Add regression coverage proving normal initial selected Skill resolution and activation still works when Skills On Demand is disabled:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/workflows/temporal/test_agent_runtime_activities.py
```

## Integration Strategy

Add hermetic integration coverage marked `integration_ci` if unit-level activity tests cannot exercise the real managed-runtime command boundary. The integration scenario should:

1. Start with no Skills On Demand setting supplied.
2. Prepare a managed runtime with one initially selected Skill.
3. Attempt `moonmind.skills.query` and verify `denied`, `feature_disabled`, and zero results.
4. Attempt `moonmind.skills.request` and verify `denied`, `feature_disabled`, and no derived snapshot.
5. Confirm the initial active Skill snapshot remains available to the runtime.

Run hermetic integration coverage with:

```bash
./tools/test_integration.sh
```

## Final Verification Commands

Run focused checks first:

```bash
git diff --check
./tools/test_unit.sh tests/unit/config
./tools/test_unit.sh tests/unit/workflows/agent_skills
./tools/test_unit.sh tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py
```

Then run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run final MoonSpec verification after implementation and tests pass.


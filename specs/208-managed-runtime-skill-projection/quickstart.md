# Quickstart: Managed Runtime Skill Projection

## Classification

- Input type: single-story feature request.
- Intent: runtime.
- Active feature directory: `specs/208-managed-runtime-skill-projection`.
- Resume decision: no existing MM-407 spec artifacts were found, so orchestration started at specify.

## Test-First Validation

1. Add focused unit coverage for `AgentSkillMaterializer`:

```bash
python -m pytest tests/unit/services/test_skill_materialization.py -q
```

Expected red cases before implementation:

- `.agents/skills/_manifest.json` does not exist.
- service materializer does not expose `.agents/skills`.
- incompatible `.agents/skills` paths are not rejected through shared projection validation.

2. Add activity-boundary coverage for `agent_skill.materialize`:

```bash
python -m pytest tests/unit/workflows/agent_skills/test_agent_skills_activities.py -q
```

Expected red case before implementation:

- activity materialization output does not report canonical visible path metadata.

3. Final unit verification:

```bash
./tools/test_unit.sh
```

## Integration Verification

This story does not require live provider credentials. If filesystem projection changes touch managed worker prepare behavior, run:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed agent environment, record the exact Docker/socket blocker and rely on unit plus activity-boundary evidence.

## End-to-End Story Check

- Materialize a one-skill snapshot into a temporary workspace.
- Confirm `.agents/skills/_manifest.json` exists and lists MM-407-required metadata.
- Confirm `.agents/skills/<skill>/SKILL.md` is available when content is readable.
- Confirm multi-skill projection omits unselected repo skills.
- Confirm existing checked-in source directories are not rewritten.
- Confirm incompatible projection paths fail before runtime launch.

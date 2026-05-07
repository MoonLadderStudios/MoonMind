# Quickstart: Skill Projection Noninterference

## Preconditions

- Work from `specs/314-skill-projection-noninterference/spec.md`.
- Preserve Jira issue key `MM-608` in all downstream artifacts and final evidence.
- Use `MOONMIND_FORCE_LOCAL_TESTS=1` for managed-agent local unit verification.

## TDD Flow

1. Add or update unit tests for materialization metadata and repo-authored source preservation before changing production code:

```bash
./tools/test_unit.sh tests/unit/services/test_skill_materialization.py
```

2. Add or update workspace alias ownership tests:

```bash
./tools/test_unit.sh tests/unit/workflows/test_workspace_links.py
```

3. Add or update loader guard tests:

```bash
./tools/test_unit.sh tests/unit/services/test_skill_resolution.py
```

4. Add or update managed runtime activity tests for activation summaries and selected-skill visible path behavior:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_agent_runtime_activities.py
```

5. Add or update publish filtering and Codex/Gemini runtime command tests when those boundaries are affected:

```bash
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_executions.py
```

6. Confirm MoonSpec verifier preflight evidence:

```bash
./tools/test_unit.sh tests/unit/agents/test_moonspec_verify_skill.py
```

## Integration Strategy

If unit-level activity boundary coverage cannot prove the original failure mode, add a hermetic integration test marked `integration_ci` that:

1. Creates a managed job workspace with a real `.agents/skills/repo-skill/SKILL.md` directory.
2. Materializes a selected active skill snapshot for a managed Codex turn.
3. Confirms `.agents/skills` remains a directory and checked-in skill files are unchanged.
4. Confirms the activation summary points to the run-scoped active `visiblePath`.
5. Confirms representative verification/publish preflight sees no generated projection contamination.

Run hermetic integration coverage with:

```bash
./tools/test_integration.sh
```

## Final Verification Commands

Run focused checks first:

```bash
git diff --check
./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py
./tools/test_unit.sh tests/unit/workflows/test_workspace_links.py tests/unit/workflows/temporal/test_agent_runtime_activities.py
./tools/test_unit.sh tests/unit/agents/test_moonspec_verify_skill.py tests/unit/agents/codex_worker/test_worker.py
```

Then run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run final MoonSpec verification after implementation and tests pass.

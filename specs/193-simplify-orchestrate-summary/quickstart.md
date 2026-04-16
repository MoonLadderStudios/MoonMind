# Quickstart: Simplify Orchestrate Summary

## Focused Validation

1. Confirm seeded preset expansion behavior:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py
```

2. Run the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Manual Review

1. Inspect `api_service/data/task_step_templates/jira-orchestrate.yaml`.
2. Confirm the last operational step is the Jira Code Review transition, not a generic Jira orchestration report.
3. Confirm the PR creation step still writes `artifacts/jira-orchestrate-pr.json`.
4. Inspect `api_service/data/task_step_templates/moonspec-orchestrate.yaml`.
5. Confirm the last operational step is MoonSpec verification, not a separate orchestration report or publish narration step.
6. Inspect `moonmind/workflows/temporal/workflows/run.py` and confirm finalization still writes `reports/run_summary.json`.

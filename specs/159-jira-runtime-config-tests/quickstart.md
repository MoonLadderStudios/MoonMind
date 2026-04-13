# Quickstart: Jira Runtime Config Tests

## Purpose

Validate that Jira Create-page browser discovery is exposed only through the existing runtime config boot payload and only when the Jira UI rollout is enabled.

## Focused Verification

Run the runtime config router tests:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py
```

Expected outcome:

- Jira UI config is absent when the rollout is disabled.
- Jira UI config is still absent when backend Jira tooling is enabled but the Create-page rollout is disabled.
- Jira UI config appears with the expected endpoint templates when the rollout is enabled.
- Default Jira project, default Jira board, and session board memory settings are surfaced when configured.
- Existing runtime config coverage for non-Jira Create-page behavior continues to pass.

## Final Verification

Run the full unit suite:

```bash
./tools/test_unit.sh
```

Expected outcome:

- Unit tests pass.
- Frontend tests invoked by the standard runner pass.
- No external Jira credentials or Jira network access are required.

## Manual Contract Inspection

With the Jira Create-page rollout disabled, generated runtime config must omit:

- `sources.jira`
- `system.jiraIntegration`

With the rollout enabled, generated runtime config must include:

- `sources.jira.connections`
- `sources.jira.projects`
- `sources.jira.boards`
- `sources.jira.columns`
- `sources.jira.issues`
- `sources.jira.issue`
- `system.jiraIntegration.enabled`
- `system.jiraIntegration.defaultProjectKey`
- `system.jiraIntegration.defaultBoardId`
- `system.jiraIntegration.rememberLastBoardInSession`

All Jira source values must be MoonMind API paths and must not contain raw Jira base URLs, credentials, or browser-side auth details.

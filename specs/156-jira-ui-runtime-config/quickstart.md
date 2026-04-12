# Quickstart: Jira UI Runtime Config

## Preconditions

- Work from branch `156-jira-ui-runtime-config`.
- Keep runtime intent: this feature requires production runtime code changes plus validation tests.
- No Jira credentials are required for Phase 1.

## Implementation Steps

1. Add or confirm Create-page Jira rollout settings:

   - disabled by default
   - default project key
   - default board ID
   - session-only last-board memory flag

2. Add or confirm runtime config output:

   - when disabled, omit `sources.jira`
   - when disabled, omit `system.jiraIntegration`
   - when enabled, include the six Jira source templates from the contract
   - when enabled, include the four Jira integration settings from the contract

3. Add or confirm tests for:

   - disabled omission
   - enabled endpoint templates
   - configured default project and board values
   - existing runtime config keys remaining available when disabled

## Focused Verification

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/config/test_atlassian_settings.py
```

## Final Verification

```bash
./tools/test_unit.sh
```

## Expected Results

- Disabled Jira UI rollout produces no browser Jira config.
- Enabled Jira UI rollout produces the documented source and system contract.
- Existing Create page runtime config behavior remains unchanged when disabled.
- No Jira credentials or direct Jira URLs are exposed to browser runtime config.

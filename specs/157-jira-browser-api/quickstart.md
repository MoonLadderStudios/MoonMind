# Quickstart: Jira Browser API

## Goal

Validate the backend Jira browser API runtime path for the Create page.

## Preconditions

- The Jira Create-page browser feature flag is enabled for runtime testing.
- Trusted Jira configuration is available through the existing MoonMind Jira settings and SecretRef-aware auth path.
- Unit tests can run locally in the managed agent container.

## Focused Backend Verification

Run service and router coverage for this feature:

```bash
pytest tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q
```

Expected result:

- Connection verification returns safe metadata.
- Project allowlists are enforced.
- Board columns preserve board order and status mappings.
- Board issues group into mapped columns and unmapped items.
- Issue detail returns normalized text and recommended imports.
- Router failures are structured and safe.

## Runtime Config Regression

Confirm Create-page Jira exposure remains rollout-gated and separate from trusted Jira tooling:

```bash
pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py -q
```

Expected result:

- Jira browser config is omitted when disabled.
- Jira browser source templates and defaults appear when enabled.
- Trusted Jira tool enablement alone does not expose the Create-page browser.

## Existing Jira Boundary Regression

Confirm the new browser path does not weaken the existing Jira auth, client, and tool behavior:

```bash
pytest tests/unit/integrations/test_jira_auth.py tests/unit/integrations/test_jira_client.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_mcp_tools_router.py -q
```

Expected result:

- SecretRef-aware auth resolution still works.
- Retry, timeout, and redaction behavior remain intact.
- Existing Jira tool registry behavior remains unchanged.

## Full Unit Verification

Run the standard unit wrapper before final handoff:

```bash
./tools/test_unit.sh
```

## Manual Smoke Test

With Jira UI enabled and a safe test Jira connection:

1. Call the browser connection verification operation and confirm it returns safe metadata only.
2. List allowed projects and confirm denied projects do not appear.
3. List boards for an allowed project.
4. Load board columns and confirm the order matches the Jira board.
5. Load board issues and confirm empty columns remain visible.
6. Load issue detail and confirm normalized description, acceptance criteria, and recommended import text are present.
7. Disable or break Jira configuration and confirm the browser receives a structured safe error while manual task creation remains available.

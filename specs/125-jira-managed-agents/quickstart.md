# Quickstart: Jira Tools for Managed Agents

## 1. Configure Jira tool execution

Use local-development raw values:

```bash
export ATLASSIAN_JIRA_TOOL_ENABLED=true
export ATLASSIAN_AUTH_MODE=basic
export ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
export ATLASSIAN_EMAIL=bot@example.com
export ATLASSIAN_API_KEY=local-dev-token
```

Or use SecretRef-backed bindings:

```bash
export ATLASSIAN_JIRA_TOOL_ENABLED=true
export ATLASSIAN_AUTH_MODE_SECRET_REF=db://jira-auth-mode
export ATLASSIAN_SITE_URL_SECRET_REF=db://jira-site-url
export ATLASSIAN_EMAIL_SECRET_REF=db://jira-email
export ATLASSIAN_API_KEY_SECRET_REF=db://jira-api-key
```

Optional policy controls:

```bash
export ATLASSIAN_JIRA_ALLOWED_PROJECTS=ENG,OPS
export ATLASSIAN_JIRA_ALLOWED_ACTIONS=create_issue,create_subtask,edit_issue,get_issue,search_issues,get_transitions,transition_issue,add_comment,verify_connection
export ATLASSIAN_JIRA_REQUIRE_EXPLICIT_TRANSITION_LOOKUP=true
```

## 2. Discover tools

```bash
curl -s http://localhost:8000/mcp/tools
```

Confirm the response contains Jira tool names such as `jira.create_issue`, `jira.get_issue`, and `jira.verify_connection`.

## 3. Verify connectivity

Call the trusted verify tool through the existing tool-call endpoint:

```json
{
  "tool": "jira.verify_connection",
  "arguments": {
    "projectKey": "ENG"
  }
}
```

Expected outcome:

- success when the binding is valid and access is allowed
- sanitized failure when auth, permission, or connectivity is invalid

## 4. Validate issue creation

```json
{
  "tool": "jira.create_issue",
  "arguments": {
    "projectKey": "ENG",
    "issueTypeId": "10001",
    "summary": "Implement trusted Jira tools",
    "description": "Create the managed-agent Jira tool path"
  }
}
```

Expected outcome:

- Jira issue created through the trusted backend path
- no token appears in the response

## 5. Run validation

Focused checks:

```bash
pytest tests/config/test_atlassian_settings.py -q
pytest tests/unit/integrations/test_jira_auth.py -q
pytest tests/unit/integrations/test_jira_client.py -q
pytest tests/unit/integrations/test_jira_tool_service.py -q
pytest tests/unit/mcp/test_jira_tool_registry.py -q
pytest tests/unit/api/test_mcp_tools_router.py -q
```

Final required verification:

```bash
./tools/test_unit.sh
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

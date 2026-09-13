# Jira Issue Updater — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative field,
ADF/append, transition, secret-hygiene, authority, and success-evidence
rules stay in `SKILL.md`; this file only shows how the selected provider
is invoked.

## HTTP MCP invocation

When no native Jira tool is exposed but an authenticated MoonMind API
endpoint is available, call:

```bash
curl -sS -X POST "$MOONMIND_URL/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "jira.get_transitions",
    "arguments": {
      "issueKey": "ENG-123",
      "expandFields": true
    }
  }'
```

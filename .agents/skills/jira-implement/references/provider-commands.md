# Jira Implement — provider commands (portable reference)

Selected-provider invocation examples for this skill. Normative behavior
(draft/write intent, secret hygiene, authority boundaries, success evidence)
stays in `SKILL.md`; this file only shows how to invoke the selected provider.

## MoonMind trusted MCP fetch (bounded)

```bash
test -n "$MOONMIND_URL"
curl -fsS -H "$MOONMIND_AUTH_HEADER" "$MOONMIND_URL/mcp/tools"
curl -fsS -X POST "$MOONMIND_URL/mcp/tools/call" \
  -H 'content-type: application/json' \
  -H "$MOONMIND_AUTH_HEADER" \
  --data '{"tool":"jira.verify_connection","arguments":{}}'
curl -fsS -X POST "$MOONMIND_URL/mcp/tools/call" \
  -H 'content-type: application/json' \
  -H "$MOONMIND_AUTH_HEADER" \
  --data '{"tool":"jira.get_issue","arguments":{"issueKey":"ENG-123"}}'
```

When the MoonMind API requires auth, use an existing runtime token through
`MOONMIND_AUTH_HEADER`, `MOONMIND_API_TOKEN`, `MOONMIND_AUTH_TOKEN`,
`MOONMIND_BEARER_TOKEN`, or `MOONMIND_API_KEY`; do not print those values.

## Repository verification (selected repo)

Discover the verification entrypoint through repo conventions (`AGENTS.md`,
`CONTRIBUTING.md`, test selectors/manifests). For the MoonMind repo the
current selectors are targeted `./tools/test_unit.sh` path filters,
`--ui-args`, or selector-equivalent backend suites, and targeted
`./tools/test_integration.sh` for the affected integration boundary only.
Do not treat these repo-specific selectors as portable requirements for
other repositories.

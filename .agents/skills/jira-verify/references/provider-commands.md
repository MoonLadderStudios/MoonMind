# Jira Verify — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative evidence-first
ordering, secret hygiene, authority boundaries, and success evidence stay in
`SKILL.md`; this file only shows how the selected provider is invoked.

## Trusted MCP path (when exposed)

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Verify Jira authentication with `POST $MOONMIND_URL/mcp/tools/call` and
   JSON `{"tool":"jira.verify_connection","arguments":{}}`.
3. Fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON
   `{"tool":"jira.get_issue","arguments":{"issueKey":"ENG-123"}}`.
4. After the verification comment has posted successfully, and only when
   `update status` is true with a `PASS` verdict, fetch available
   transitions with `POST $MOONMIND_URL/mcp/tools/call` and JSON
   `{"tool":"jira.get_transitions","arguments":{"issueKey":"ENG-123"}}`,
   then transition only through `jira.transition_issue` with JSON
   `{"tool":"jira.transition_issue","arguments":{"issueKey":"ENG-123","transitionId":"101","fields":{}}}`.

When the MoonMind API requires auth, provide an existing runtime token via
`MOONMIND_AUTH_HEADER`, `MOONMIND_API_TOKEN`, `MOONMIND_AUTH_TOKEN`,
`MOONMIND_BEARER_TOKEN`, or `MOONMIND_API_KEY`; do not print those values.

## Bundled helper (when materialized)

```bash
.agents/skills/jira-verify/tools/post_jira_comment.py --issue <ISSUE> --body-file <comment_file>
```

Otherwise call the trusted Jira tool directly with `jira.add_comment` and
arguments `{"issueKey":"<ISSUE>","body":"<comment text>"}`.

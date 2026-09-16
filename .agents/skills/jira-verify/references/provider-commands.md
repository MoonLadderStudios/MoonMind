# Provider Commands (Jira Verify)

Selected-provider invocation shapes for this skill. This is a portable
reference inside the skill bundle: raw invocation shapes may live here, while
the root entrypoint keeps the portable intent (trusted-surface preference,
authentication and policy-denied handling, secret hygiene, evidence-first
ordering, and receipt-bound retries). Load this reference only when calling
the matching provider path. Resolve it through the run's immutable active
skill bundle
(`$MOONMIND_ACTIVE_SKILLS_DIR/jira-verify/references/provider-commands.md`
in MoonMind; `.agents/skills/jira-verify/references/provider-commands.md`
for a standalone checkout).

## Trusted MCP path

If Jira content is not already available to the runtime, use the trusted MCP
path when exposed:

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Verify Jira authentication with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.verify_connection","arguments":{}}`.
3. Fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.get_issue","arguments":{"issueKey":"ENG-123"}}`.
4. If `update status` is true, fetch available transitions with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.get_transitions","arguments":{"issueKey":"ENG-123"}}`, then transition only through `jira.transition_issue` with JSON `{"tool":"jira.transition_issue","arguments":{"issueKey":"ENG-123","transitionId":"101","fields":{}}}` after the PASS-only checks in the root entrypoint succeed.

## Bundled helper

If the bundled helper is materialized, post with:

```bash
.agents/skills/jira-verify/tools/post_jira_comment.py --issue <ISSUE> --body-file <comment_file>
```

When the MoonMind API requires auth, provide an existing runtime token via
`MOONMIND_AUTH_HEADER`, `MOONMIND_API_TOKEN`, `MOONMIND_AUTH_TOKEN`,
`MOONMIND_BEARER_TOKEN`, or `MOONMIND_API_KEY`; do not print those values.
Otherwise call the trusted Jira tool directly with `jira.add_comment` and
arguments `{"issueKey":"<ISSUE>","body":"<comment text>"}`.

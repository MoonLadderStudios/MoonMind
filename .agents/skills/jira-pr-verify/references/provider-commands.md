# Provider Commands (Jira PR Verify)

Selected-provider command catalog for this skill. This is a portable
reference inside the skill bundle: provider invocation shapes may live here,
while the root entrypoint keeps the portable intent (trusted-surface
preference, `gh`-first preflight policy, untrusted-reference handling,
secret hygiene, and verification ledger/comment outputs). Load this reference
only when calling the matching provider path. Resolve it through the run's
immutable active skill bundle
(`$MOONMIND_ACTIVE_SKILLS_DIR/jira-pr-verify/references/provider-commands.md`
in MoonMind;
`.agents/skills/jira-pr-verify/references/provider-commands.md` for a
standalone checkout).

## Trusted Jira MCP path

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Verify Jira authentication with `POST $MOONMIND_URL/mcp/tools/call` and JSON like `{"tool":"jira.verify_connection","arguments":{}}`.
3. If authentication succeeds, fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON like `{"tool":"jira.get_issue","arguments":{"issueKey":"KANDY-2558"}}`.
4. Use the sanitized tool result as the Jira source of truth.

## GitHub preflight (`gh`)

If the bundled helper is materialized in the workspace, use it:

```bash
.agents/skills/jira-pr-verify/tools/github_pr_preflight.py --repo <owner/repo> --pr <pr>
```

Otherwise run the equivalent `gh` commands directly:

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh pr view <pr> --repo <owner/repo> --json number,title,state,url,headRefName,baseRefName
```

When both `gh` and the connector are available, prefer `gh` for:

- PR metadata: `gh pr view ... --json ...`
- PR diff: `gh pr diff ...`
- PR checks: `gh pr checks ...`
- PR comments: `gh pr comment ... --body-file ...`

## PR inspection (`gh`)

```bash
gh pr view <pr> --repo <owner/repo> --json number,url,title,body,baseRefName,headRefName,files,commits,statusCheckRollup,reviewDecision,comments,reviews
gh pr diff <pr> --repo <owner/repo>
gh pr checks <pr> --repo <owner/repo>
```

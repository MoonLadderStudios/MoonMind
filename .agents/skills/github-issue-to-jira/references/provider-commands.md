# Provider Commands (GitHub Issue to Jira)

Selected-provider command catalog for this skill. This is a portable
reference inside the skill bundle: provider invocation shapes may live here,
while the root entrypoint keeps the portable intent (exactly one terminal
action, trusted-path preference, untrusted-reference handling, secret
hygiene, receipt-bound retries, and partial-success reporting). Load this
reference only when calling the matching provider path. Resolve it through
the run's immutable active skill bundle
(`$MOONMIND_ACTIVE_SKILLS_DIR/github-issue-to-jira/references/provider-commands.md`
in MoonMind;
`.agents/skills/github-issue-to-jira/references/provider-commands.md` for a
standalone checkout).

## Read/preflight (`gh`)

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh issue view <issue> --repo <owner/repo> --json number,title,state,url,body,labels,comments,author,createdAt,updatedAt
```

## Post a comment and close (`gh`)

```bash
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
gh issue close <issue> --repo <owner/repo> --reason completed
```

## Needs-clarification label and comment (`gh`)

```bash
gh label create "needs clarification" --repo <owner/repo> --description "More product or technical detail is needed" --color C5DEF5 || true
gh issue edit <issue> --repo <owner/repo> --add-label "needs clarification"
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
```

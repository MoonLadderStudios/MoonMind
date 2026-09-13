# Jira PR Verify — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative secret
hygiene, authority boundaries, and success evidence stay in `SKILL.md`;
this file only shows how the selected provider is invoked.

## Preflight

```bash
.agents/skills/jira-pr-verify/tools/github_pr_preflight.py --repo <owner/repo> --pr <pr>
```

Or the equivalent `gh` commands directly:

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh pr view <pr> --repo <owner/repo> --json number,title,state,url,headRefName,baseRefName
```

## Inspect

- PR metadata: `gh pr view ... --json ...`
- PR diff: `gh pr diff ...`
- PR checks: `gh pr checks ...`
- PR comments: `gh pr comment ... --body-file ...`

Full inspect shapes:

```bash
gh pr view <pr> --repo <owner/repo> --json number,url,title,body,baseRefName,headRefName,files,commits,statusCheckRollup,reviewDecision,comments,reviews
gh pr diff <pr> --repo <owner/repo>
gh pr checks <pr> --repo <owner/repo>
```

## Comment

```bash
.agents/skills/jira-pr-verify/tools/post_pr_comment.py --repo <owner/repo> --pr <pr> --body-file <comment_file>
```

Otherwise:

```bash
gh pr comment <pr> --repo <owner/repo> --body-file <comment_file>
```

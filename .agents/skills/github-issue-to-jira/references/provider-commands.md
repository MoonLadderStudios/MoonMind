# GitHub Issue to Jira — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative terminal-path,
secret-hygiene, authority, and success-evidence rules stay in `SKILL.md`;
this file only shows how the selected provider is invoked.

## GitHub read path (`gh` primary)

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh issue view <issue> --repo <owner/repo> --json number,title,state,url,body,labels,comments,author,createdAt,updatedAt
```

Use a GitHub connector only when `gh` is unavailable or unauthenticated.

## Close path

```bash
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
gh issue close <issue> --repo <owner/repo> --reason completed
```

## Needs-clarification path

```bash
gh label create "needs clarification" --repo <owner/repo> --description "More product or technical detail is needed" --color C5DEF5 || true
gh issue edit <issue> --repo <owner/repo> --add-label "needs clarification"
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
```

## Jira story path

Create through the trusted Jira tool surface with metadata-driven field
IDs. Search for a matching story by project, summary, and GitHub issue URL
before retrying an uncertain creation.

# GitHub Issue Verify — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative evidence-first
ordering, secret hygiene, authority boundaries, and success evidence stay in
`SKILL.md`; this file only shows how the selected provider is invoked.

## Read path (`gh` primary)

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,defaultBranchRef,viewerPermission,isPrivate
gh issue view <issue> --repo <owner/repo> --json number,title,state,stateReason,url,body,labels,comments,author,createdAt,updatedAt
```

Use an equivalent trusted GitHub connector when `gh` is unavailable or
unauthenticated.

## Comment path

```bash
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
```

Otherwise use the trusted GitHub connector's issue-comment operation.

## Completion path (only after successful comment post)

```bash
gh issue close <issue> --repo <owner/repo> --reason completed
```

Or update the issue to `state: closed` with `state_reason: completed`
through a trusted connector.

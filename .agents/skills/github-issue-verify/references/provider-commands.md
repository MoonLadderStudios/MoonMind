# Provider Commands (GitHub Issue Verify)

Selected-provider command catalog for this skill. This is a portable
reference inside the skill bundle: provider invocation shapes may live here,
while the root entrypoint keeps the portable intent (trusted-path preference,
untrusted-reference handling, secret hygiene, evidence-first comment/close
ordering, and receipt-bound retries). Load this reference only when calling
the matching provider path. Resolve it through the run's immutable active
skill bundle
(`$MOONMIND_ACTIVE_SKILLS_DIR/github-issue-verify/references/provider-commands.md`
in MoonMind;
`.agents/skills/github-issue-verify/references/provider-commands.md` for a
standalone checkout).

## Read/preflight (`gh`)

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,defaultBranchRef,viewerPermission,isPrivate
gh issue view <issue> --repo <owner/repo> --json number,title,state,stateReason,url,body,labels,comments,author,createdAt,updatedAt
```

## Post the verification comment (`gh`)

```bash
gh issue comment <issue> --repo <owner/repo> --body-file <comment_file>
```

## Close after a successful post (`gh`)

```bash
gh issue close <issue> --repo <owner/repo> --reason completed
```

Or update the issue to `state: closed` with `state_reason: completed`
through a trusted connector.

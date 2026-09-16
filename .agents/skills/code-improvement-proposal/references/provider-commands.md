# Provider Commands (Code Improvement Proposal)

Selected-provider command catalog and payload templates for this skill. This
is a portable reference inside the skill bundle: provider API shapes may live
here, while the root entrypoint keeps the portable intent (dry-run default,
trusted-surface preference, secret hygiene, duplicate handling, and output
statuses). Load this reference only when publishing through the matching
provider path. Resolve it through the run's immutable active skill bundle
(`$MOONMIND_ACTIVE_SKILLS_DIR/code-improvement-proposal/references/provider-commands.md`
in MoonMind; `.agents/skills/code-improvement-proposal/references/provider-commands.md`
for a standalone checkout).

## GitHub (`gh`)

Preferred when `gh` is available and authenticated:

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh issue list --repo <owner/repo> --state open --search "<theme/path terms>" --json number,title,url,labels
gh issue create --repo <owner/repo> --title "<title>" --body-file <body_file> --label "<label>"
```

Use a GitHub connector only when `gh` is unavailable or unauthenticated.

GitHub payload shape:

```json
{
  "title": "Code improvement proposal: centralize payments retry and idempotency handling",
  "body": "<markdown issue body>",
  "labels": ["code-quality", "technical-debt", "refactor"],
  "assignees": []
}
```

## Jira (raw shape, not the primary path)

The primary path is the trusted Jira tool surface described in the root
entrypoint. The raw provider shape below is for the explicitly authorized
standalone adapter only, with equivalent write-intent, validation, and
receipt semantics. `POST /rest/api/3/issue` is never the primary path here.

Payload shape (Atlassian Document Format description):

```json
{
  "fields": {
    "project": { "key": "ENG" },
    "issuetype": { "name": "Task" },
    "summary": "Code improvement proposal: centralize payments retry and idempotency handling",
    "labels": ["code-quality", "technical-debt", "refactor"],
    "components": [{ "name": "Payments" }],
    "description": { "type": "doc", "version": 1, "content": [] }
  }
}
```

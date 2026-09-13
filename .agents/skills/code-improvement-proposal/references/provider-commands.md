# Code Improvement Proposal — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative draft/write
intent, secret hygiene, authority boundaries, and success evidence stay in
`SKILL.md`; this file only shows how the selected provider is invoked.

## GitHub (`gh` preferred)

```bash
gh auth status --hostname github.com
gh repo view <owner/repo> --json nameWithOwner,viewerPermission,isPrivate
gh issue list --repo <owner/repo> --state open --search "<theme/path terms>" --json number,title,url,labels
gh issue create --repo <owner/repo> --title "<title>" --body-file <body_file> --label "<label>"
```

Use a GitHub connector only when `gh` is unavailable or unauthenticated.

## Jira (trusted tool surface only)

Keep the internal issue body in Markdown first, then convert it to Atlassian
Document Format (ADF) at the final publishing step. Fetch create metadata
for the target project and issue type first; map requested fields to Jira
field IDs through metadata instead of hardcoding custom field IDs. Then
create the issue through the trusted Jira tool surface. Never use raw
credentials or a direct-HTTP fallback.

Payload shape:

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

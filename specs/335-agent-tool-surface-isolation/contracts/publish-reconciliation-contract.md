# Publish Reconciliation Contract

Source traceability: `MM-680` - Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows.

## Purpose

Define MoonMind-owned branch push and pull request creation behavior so residual races and pre-existing remote state reconcile instead of terminal-failing workflows.

## Branch Publish

Inputs:
- `repo`
- `branch`
- `base_branch`
- `last_recorded_remote_sha` when available
- sanitized workflow/session context

Required behavior:
1. Resolve current branch and base references without exposing secrets.
2. Commit workspace changes when needed through MoonMind-owned activity code.
3. Push with lease semantics against the last activity-recorded remote SHA when one is available.
4. On lease miss, fetch current remote state and return a structured retryable conflict result.
5. Return `no_commits` when the branch has no commits over the base.
6. Never classify a lease miss as a non-retryable terminal application error.

Structured conflict result:
```json
{
  "push_status": "lease_conflict",
  "push_branch": "feature-branch",
  "push_base_branch": "main",
  "retryable": true,
  "diagnostic_kind": "publish_lease_conflict",
  "summary": "Remote branch changed before publish; fetch/rebase or retry with updated lease."
}
```

## Pull Request Create or Adopt

Inputs:
- `repo`
- `head`
- `base`
- `title`
- `body`

Required behavior:
1. Query open pull requests for the requested `head` and `base` before creating.
2. If one exists, return success with `created=false`, `adopted=true`, `url`, and `headSha` when known.
3. If none exists, create the pull request and return `created=true`, `adopted=false`, `url`, and `headSha` when known.
4. If creation fails with a validation error, do not treat it as adoption unless an explicit follow-up lookup finds a matching PR.
5. Secret-like content in title/body remains blocked.

Adoption result:
```json
{
  "url": "https://github.com/owner/repo/pull/123",
  "created": false,
  "adopted": true,
  "summary": "Existing pull request adopted for head feature-branch into main.",
  "headSha": "abc123"
}
```

## Diagnostics

Publish reconciliation emits sanitized diagnostics for:
- `pull_request_adopted`
- `pull_request_created`
- `publish_lease_conflict`
- `direct_publish_denied`
- `publish_failed`

Diagnostics must not contain raw credentials, tokenized URLs, auth headers, cookies, or private key material.

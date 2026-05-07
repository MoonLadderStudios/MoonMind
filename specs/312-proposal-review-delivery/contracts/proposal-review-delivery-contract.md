# Proposal Review Delivery Contract

## Delivery Input

The proposal delivery boundary accepts a stored proposal delivery record or a validated proposal submission result with:

- delivery record ID
- provider: `github` or `jira`
- repository or Jira project destination
- title, summary, category, tags, priority
- dedup key/hash
- stored proposal snapshot or `task_snapshot_ref`
- origin run/workflow metadata
- resolved policy metadata
- provider metadata

Rules:
- Validate local delivery policy and allowlists before provider calls.
- Search local open duplicates before provider delivery.
- Search provider metadata when local state is stale or missing.
- Do not send provider calls when the stored proposal snapshot is invalid.
- Do not expose raw provider credentials in inputs, outputs, logs, issue bodies, or provider metadata.

## GitHub Issue Delivery

Create or update a GitHub Issue with:

- title prefixed `[MoonMind proposal]`
- canonical proposal labels or configured equivalents
- category, priority, status, and dedup labels/metadata
- hidden MoonMind marker containing delivery record ID, snapshot ref, and dedup hash
- links to source run and relevant artifacts
- reviewer action instructions
- explicit stored-snapshot notice

Duplicate behavior:
- If an open issue matches the local delivery record or hidden marker, update it.
- If an open issue matches dedup metadata, update or link it.
- If no match exists, create a new issue.

## Jira Issue Delivery

Create or update a Jira issue with:

- summary prefixed `[MoonMind proposal]`
- ADF-rendered description with review context
- configured labels/custom fields for category, priority, status, dedup hash, repository, origin, runtime, and snapshot ref when supported
- configured workflow state or action triggers
- source run and artifact links
- issue links to duplicate or related proposals when applicable
- explicit stored-snapshot notice

Duplicate behavior:
- Search local delivery record first.
- Search Jira by configured custom fields, labels, or hidden marker metadata when available.
- Update or link matching open Jira issues instead of creating duplicates.

## Reviewer Decision Handling

Accepted decisions:

- promote
- dismiss
- defer
- priority update

Inputs:
- provider
- external issue identity
- provider event ID
- actor identity
- decision source: comment command, workflow transition, field update, label update, or explicit provider event
- bounded decision parameters: runtime, priority, max attempts, note, defer-until

Rules:
- Provider event IDs are idempotency keys.
- Actor and action permissions are checked before mutation.
- Only bounded controls are parsed.
- Edited issue body/description text is never treated as a replacement task payload.
- Promotion loads the stored proposal snapshot from MoonMind.

## Delivery Output

Successful delivery output includes:

- delivery record ID
- provider
- external key
- external URL
- created or updated flag
- duplicate source when reused
- delivered or synced timestamp
- sanitized warnings

Failure output includes:

- delivery record ID when available
- provider
- destination
- sanitized reason
- recoverable next action
- no raw credentials, auth headers, cookies, tokens, private keys, or provider response dumps

# Data Model: Proposal Review Delivery

## Traceability

This data model supports Jira issue `MM-598` and the stored proposal delivery requirements mapped from DESIGN-REQ-001, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-027, and DESIGN-REQ-031.

## Proposal Delivery Record

Represents one MoonMind proposal delivered or intended for delivery to an external review tracker.

Fields:
- `id`: stable delivery record identifier.
- `provider`: `github` or `jira`.
- `external_key`: provider issue number or Jira issue key when known.
- `external_url`: provider issue URL when known.
- `repository`: canonical repository target used for delivery and dedup.
- `dedup_key`: human-inspectable dedup key from repository and normalized title.
- `dedup_hash`: stable hash used for duplicate lookup.
- `status`: proposal lifecycle state.
- `title`, `summary`, `category`, `tags`, `review_priority`: reviewer-facing metadata.
- `task_create_request`: stored validated proposal snapshot.
- `task_snapshot_ref`: optional artifact-backed snapshot reference.
- `origin_source`, `origin_id`, `origin_external_id`, `origin_metadata`: source run/workflow identity and compact evidence.
- `provider_metadata`: provider-scoped metadata such as labels, issue properties, Jira project fields, hidden marker IDs, or sync cursors.
- `resolved_policy`: resolved delivery provider, target, repository decision, defaulting evidence, and policy outcome.
- `delivered_at`, `last_synced_at`: provider delivery/sync timestamps.
- `promoted_at`, `promoted_by_user_id`, `decided_by_user_id`, `decision_note`: reviewer decision state.

Validation rules:
- `provider` must be `github` or `jira`.
- Provider metadata must not contain raw credentials, auth headers, cookies, tokens, private keys, or plaintext secret refs.
- Dedup identity is based on canonical repository target and normalized proposal title.
- External key/url may be absent before delivery but must be present after confirmed provider creation/update.

State transitions:
- `open` -> provider delivered or updated while still reviewable.
- `open` -> `promoted` after verified reviewer approval starts a new execution.
- `open` -> `dismissed` after verified reviewer dismissal.
- `open` -> deferred state representation through provider metadata or future status if implementation extends status vocabulary.
- Terminal records are not treated as open duplicates.

## External Proposal Issue

Represents the GitHub Issue or Jira issue shown to reviewers.

Fields:
- Provider issue identity and URL.
- Review title/summary.
- Labels, fields, workflow state, or issue properties carrying proposal markers.
- Human-readable body/description.
- Dedup marker and delivery record marker.
- Source run, artifact, and snapshot links.
- Reviewer action controls.
- Stored-snapshot notice.

Validation rules:
- Rendered text must not include raw executable payload replacement instructions.
- Large logs, diagnostics, artifacts, and snapshots are linked by reference.
- Hidden markers or issue properties must be deterministic enough for duplicate lookup.

## Stored Proposal Snapshot

Represents the executable source of truth for promotion.

Fields:
- Canonical task creation request or artifact ref.
- Repository target.
- Task instructions and compact runtime/skill/preset provenance.
- Priority, max attempts, and bounded promotion controls.

Validation rules:
- Promotion uses this snapshot plus bounded reviewer controls only.
- Edited external issue content cannot replace this snapshot.
- Snapshot refs must not expose raw storage keys or presigned URLs.

## Provider Decision Event

Represents a reviewer action observed through GitHub or Jira.

Fields:
- Provider.
- Provider event ID or equivalent idempotency key.
- External issue key/URL.
- Actor identity.
- Decision: promote, dismiss, defer, or priority.
- Optional note/reason.
- Observed timestamp.
- Resulting external issue state.

Validation rules:
- Duplicate provider event IDs are no-ops.
- Actor and action must pass provider policy before mutation.
- Event body text is untrusted except for explicit configured command fields.
- Decision output must be redacted before persistence or user-visible reporting.

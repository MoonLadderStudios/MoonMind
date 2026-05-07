# Proposal Delivery Contract

## `proposal.submit` Activity

Input:
- `candidates`: list of generated proposal candidates.
- `policy`: optional task proposal policy from the canonical task payload.
- `origin`: workflow/run origin metadata using snake_case keys.

Output:
- `generated_count`: number of candidates inspected.
- `submitted_count`: number accepted by policy, dedup, validation, and handed to the trusted proposal service or duplicate update path.
- `errors`: redacted visible errors for skipped or failed candidates.
- `delivery_decisions` or equivalent compact evidence when implemented: per-candidate accepted, skipped, defaulted, rewritten, duplicate, or rejected reason.

Rules:
- Resolve proposal policy before provider delivery.
- Preserve explicit candidate/task values over defaults unless policy rejects them.
- Enforce capacity, destination allowlists, severity gates, and tag gates before delivery.
- Classify project and MoonMind targets before consuming target slots.
- Normalize workflow origin metadata to snake_case before service submission.
- Do not call provider-specific delivery code until validation and local duplicate checks complete.

## `TaskProposalService.create_proposal` / Delivery Record Boundary

Input:
- `title`
- `summary`
- `category`
- `tags`
- `task_create_request`
- `origin_source`
- `origin_id`
- `origin_metadata`
- `proposed_by_worker_id` or `proposed_by_user_id`
- `review_priority`
- resolved delivery policy metadata or provider metadata when implementation adds those fields.

Output:
- A persisted proposal delivery record or an existing open duplicate record/update result.

Rules:
- Validate title, summary, task payload, origin, priority, and policy metadata before persistence.
- Compute dedup identity from canonical repository target and normalized title.
- Search local open records before creating a new record.
- Apply provider metadata duplicate checks before creating provider-facing records.
- Keep provider-specific metadata separate from canonical fields.
- Redact secret-like data before storing task snapshots or metadata.

## Proposal Repository Contract

Required operations:
- Create a new delivery record.
- Find an open delivery record by provider/destination/dedup hash.
- Update or annotate an existing open duplicate path.
- Persist provider/external issue identifiers after trusted delivery.
- Query similar/dedup records for operator visibility.

Rules:
- Duplicate lookup occurs before new external issue creation.
- Duplicate update/link/comment behavior is idempotent.
- Closed, promoted, dismissed, deferred, or superseded records are not treated as open duplicates unless an explicit provider rule says otherwise.

## Provider Metadata Contract

Rules:
- Provider metadata is optional and scoped to GitHub/Jira delivery and sync.
- Metadata may include provider issue identity, labels, hidden markers, issue properties, project key, components, or sync cursors.
- Metadata must not include raw credentials, auth headers, cookies, tokens, private keys, or secret refs resolved to plaintext.
- Provider metadata may participate in duplicate detection but cannot override canonical repository/title dedup identity.

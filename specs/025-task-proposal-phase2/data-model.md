# Data Model: Task Proposal Queue Phase 2

## Tables

### task_proposals (existing + new columns)
| Column | Type | Description |
| --- | --- | --- |
| `dedup_key` | text | Lowercased `repository:title_slug` used for grouping similar proposals. |
| `dedup_hash` | char(64) | SHA256 of `dedup_key` for indexing stability. |
| `review_priority` | enum(`low`,`normal`,`high`,`urgent`) | Reviewer triage priority (default `normal`). |
| `snoozed_until` | timestamptz | When set, indicates proposal is snoozed until timestamp. |
| `snooze_note` | text | Optional explanation displayed in UI. |
| `snoozed_by_user_id` | uuid FK user | Who last snoozed or unsnoozed the proposal. |
| `similar_cache` | jsonb | Optional summary of recent similar proposals for faster UI loads (evicted on update). |

Existing status enum expands to include implicit `snoozed` virtual state (status stays `open`, filtered via `snoozed_until`).

### task_proposal_notifications (new audit table)
| Column | Type | Description |
| --- | --- | --- |
| `id` | uuid PK |
| `proposal_id` | uuid FK task_proposals |
| `category` | text |
| `target` | text | Slack channel or webhook name |
| `status` | enum(`sent`,`failed`) |
| `error` | text nullable | Failure reason |
| `created_at` | timestamptz |

## Indexes
- `ix_task_proposals_dedup_hash` on (`dedup_hash`, `status`) for duplicate lookups.
- `ix_task_proposals_snoozed_until` partial where `snoozed_until IS NOT NULL` for auto-unsnooze job.
- `ix_task_proposals_priority_created` on (`review_priority`, `created_at DESC`).

## Derived Views
- Similar proposals query: `SELECT id, title, category, created_at FROM task_proposals WHERE dedup_hash = :hash AND id != :proposal_id AND status = 'open' ORDER BY created_at DESC LIMIT 10;`

## Validation Rules
- Dedup key computed as `<repository.lower()>:<slug(title)>` with slug removing punctuation/whitespace.
- Snooze expiration job runs every minute to set `snoozed_until=NULL` when past due and emit metric.
- Notification dedup: `ON CONFLICT (proposal_id, target)` DO NOTHING to avoid duplicates.

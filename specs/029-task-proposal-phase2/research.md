# Research Log: Task Proposal Queue Phase 2

## Deduplication Strategy
- **Decision**: Use normalized repo + slugified title as dedup key, hashed with SHA256 for compact storage and to future-proof for index length.
- **Rationale**: Reuses readily available attributes, deterministic across workers, avoids storing raw text while still enabling grouping.
- **Alternatives Considered**:
  - Trigram similarity queries (higher DB cost, requires pg_trgm extension not enabled).
  - Worker-provided dedup tokens (hard to enforce consistency, trust boundary issues).

## Similar Proposal Surfacing
- **Decision**: Query other proposals sharing the same `dedup_key` ordered by `created_at DESC` with limit 10.
- **Rationale**: Avoids complex fuzzy matching while still grouping near-duplicates; stable and index-friendly.
- **Alternatives Considered**: Cosine similarity on embeddings (would add heavy dependencies) and naive title substring search (slow, inconsistent).

## Edit-Before-Promote Flow
- **Decision**: Extend promote endpoint with optional `taskCreateRequestOverride` payload; UI will open modal pre-populated with stored request allowing edits.
- **Rationale**: Keeps single endpoint, leverages existing validation path, ensures audit data stays centralized.
- **Alternatives Considered**: Separate `PUT /api/proposals/{id}` editing route (adds concurrency issues), manual queue job creation page (duplicates logic already in proposals).

## Snooze + Priority Mechanics
- **Decision**: Add `review_priority` ENUM + `snoozed_until` timestamp persisted on proposals plus optional `snooze_note`. Provide API to snooze/unsnooze and background job to auto-unsnooze.
- **Rationale**: Minimal additions to data model, easy to query/expose, works with existing status filter semantics.
- **Alternatives Considered**: Separate `snoozed` status table (complex) or storing snooze metadata only in UI (non-persistent).

## Notifications
- **Decision**: Reuse existing webhook/Slack client utilities from queue service layer; fire-and-forget event after proposal creation when category matches `security` or `tests` with dedup per proposal ID.
- **Rationale**: Aligns with Phase 1 telemetry approach, avoids blocking request/response cycle, uses same config as other alerts.
- **Alternatives Considered**: Cron job scanning DB (introduces delay) and sending notifications for every category (too noisy).

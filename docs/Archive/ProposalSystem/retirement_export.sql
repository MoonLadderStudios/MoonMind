-- Explicit one-time evidence export. The output contains stored workflow input
-- and must be protected like a database backup. Invoke with:
--   psql "$DATABASE_URL" --quiet --tuples-only \
--     --file=docs/Archive/ProposalSystem/retirement_export.sql \
--     > /secure/path/follow-up-export.csv

BEGIN TRANSACTION READ ONLY;

COPY (
    SELECT
        id,
        status,
        title,
        summary,
        repository,
        provider,
        external_key,
        external_url,
        origin_source,
        origin_id,
        origin_external_id,
        origin_metadata,
        workflow_snapshot_ref,
        workflow_create_request,
        provider_metadata,
        proposed_by_worker_id,
        proposed_by_user_id,
        promoted_by_user_id,
        decided_by_user_id,
        decision_note,
        promoted_at,
        delivered_at,
        last_synced_at,
        created_at,
        updated_at
    FROM workflow_proposals
    ORDER BY created_at, id
) TO STDOUT WITH (FORMAT CSV, HEADER TRUE);

COMMIT;

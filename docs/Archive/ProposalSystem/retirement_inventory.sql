-- Read-only, bounded pre-upgrade inventory for the retired subsystem.
-- Run with psql before Alembic revision 367_remove_workflow_proposals.

BEGIN TRANSACTION READ ONLY;

SELECT status, COUNT(*) AS record_count
FROM workflow_proposals
GROUP BY status
ORDER BY status;

SELECT COUNT(*) AS notification_count
FROM workflow_proposal_notifications;

SELECT id, status, repository, provider, external_key, external_url
FROM workflow_proposals
WHERE external_url IS NOT NULL
ORDER BY created_at DESC
LIMIT 1000;

SELECT
    id,
    status,
    repository,
    jsonb_path_query_array(
        provider_metadata,
        '$.providerDecisions[*].promotedExecutionId'
    ) AS promoted_execution_ids
FROM workflow_proposals
WHERE status = 'promoted'
   OR jsonb_path_exists(
       provider_metadata,
       '$.providerDecisions[*].promotedExecutionId'
   )
ORDER BY created_at DESC
LIMIT 1000;

SELECT COUNT(*) AS records_lacking_disposition_metadata
FROM workflow_proposals
WHERE
    (external_url IS NOT NULL AND (provider IS NULL OR external_key IS NULL))
    OR (
        status = 'promoted'
        AND NOT jsonb_path_exists(
            provider_metadata,
            '$.providerDecisions[*].promotedExecutionId'
        )
    )
    OR (workflow_snapshot_ref IS NULL AND workflow_create_request = '{}'::jsonb);

COMMIT;

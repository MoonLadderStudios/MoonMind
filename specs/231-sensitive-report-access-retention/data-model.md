# Data Model: Sensitive Report Access and Retention

## Temporal Artifact

- Existing durable artifact metadata row.
- Relevant fields:
  - `artifact_id`: immutable artifact identifier.
  - `status`: `pending_upload`, `complete`, `failed`, or `deleted`.
  - `retention_class`: `ephemeral`, `standard`, `long`, or `pinned`.
  - `redaction_level`: controls raw access and preview behavior.
  - `metadata_json.preview_artifact_id`: optional pointer to a preview artifact used by `default_read_ref`.
  - `expires_at`, `deleted_at`, `hard_deleted_at`: lifecycle timestamps.

## Artifact Link

- Existing execution-to-artifact relationship.
- Relevant fields:
  - `namespace`, `workflow_id`, `run_id`: execution identity.
  - `link_type`: report or observability classification.
  - `label`: optional display label.
- Report link types in scope:
  - `report.primary`
  - `report.summary`
  - `report.structured`
  - `report.evidence`
- Observability link types must remain independent:
  - `runtime.stdout`
  - `runtime.stderr`
  - `runtime.merged_logs`
  - `runtime.diagnostics`
  - `debug.trace`

## Artifact Pin

- Existing pin row that protects an artifact from lifecycle deletion.
- Relevant fields:
  - `artifact_id`
  - `pinned_by_principal`
  - `pinned_at`
  - `reason`

## State Transitions

1. Create `report.primary` or `report.summary` without explicit retention -> `retention_class=long`.
2. Create `report.structured` or `report.evidence` without explicit retention -> `retention_class=standard`.
3. Pin report artifact -> `retention_class=pinned`, `expires_at=None`.
4. Unpin report artifact -> recompute default retention from existing report link; `report.primary` returns to `long`.
5. Soft delete report artifact -> `status=deleted`, `deleted_at` set, pin removed.
6. Hard delete report artifact -> existing lifecycle path marks `hard_deleted_at`/`tombstoned_at`; unrelated artifacts are not traversed or mutated.

# Data Model: Resolve Proposal Policy and Delivery Records

## Resolved Proposal Policy

Fields:
- `targets`: ordered set of allowed target classes, including `project` and/or `moonmind`.
- `max_items_project`: maximum project-targeted proposals accepted for one submission batch.
- `max_items_moonmind`: maximum MoonMind-targeted run-quality proposals accepted for one submission batch.
- `min_severity_for_moonmind`: minimum signal severity accepted for MoonMind run-quality routing.
- `default_runtime`: optional runtime stamped only when a candidate omits runtime.
- `delivery_provider`: resolved provider, such as `github`, `jira`, or policy-selected `auto` result.
- `delivery_destination`: resolved repository/project destination after allowlist validation.
- `decision_reason`: compact explanation of accepted, defaulted, rewritten, skipped, or rejected routing.

Validation rules:
- Explicit candidate/task values are preserved over defaults unless policy rejects them.
- Project candidates retain their canonical repository target.
- MoonMind run-quality candidates rewrite to the configured MoonMind repository only after category, severity, and approved tag gates pass.
- Unsupported provider/destination values fail before external issue delivery.

## Proposal Delivery Record

Canonical fields, reusing or extending the existing `task_proposals` record:
- `id`
- `provider`
- `external_key`
- `external_url`
- `repository`
- `dedup_key`
- `dedup_hash`
- `status`
- `title`
- `summary`
- `category`
- `tags`
- `review_priority`
- `priority_override_reason`
- `task_create_request` or `task_snapshot_ref`
- `origin_source`
- `origin_id`
- `origin_metadata`
- `proposed_by_worker_id`
- `proposed_by_user_id`
- `delivered_at`
- `last_synced_at`
- `promoted_at`
- `promoted_execution_id`
- `promoted_by_actor`
- `decided_by_actor`
- `decision_note`
- `provider_metadata`
- `created_at`
- `updated_at`

Validation rules:
- The record is the audit and idempotency source for provider delivery.
- Provider-specific metadata is stored in `provider_metadata`, not mixed into canonical fields or origin metadata.
- Existing persisted field names may be kept when they already represent the canonical meaning; missing fields require explicit implementation choices or documented subset decisions.

State transitions:
- `pending_delivery` -> `delivered` when provider delivery succeeds.
- `pending_delivery` -> `delivery_failed` when provider delivery fails after trusted retry behavior.
- `open` -> `approved` -> `promoted` for reviewer-approved promotion.
- `open` -> `dismissed`, `deferred`, or `superseded` for reviewer decisions or duplicate handling.

## Dedup Identity

Fields:
- `repository`: canonical repository target after project/MoonMind routing.
- `normalized_title`: slug-like normalized proposal title.
- `dedup_key`: bounded string derived from repository and normalized title.
- `dedup_hash`: stable hash of the dedup key.

Validation rules:
- Dedup identity is computed before creating a new external issue.
- Local open delivery records are checked before creating a new record.
- Provider metadata can identify duplicates when local records are absent or incomplete.
- Closed, dismissed, promoted, or superseded records do not count as open duplicates unless provider rules explicitly treat them as reusable.

## Workflow Origin Metadata

Fields:
- `origin.source`: `workflow` for Temporal-backed proposals.
- `origin.id`: durable `workflow_id`.
- `origin.metadata.workflow_id`
- `origin.metadata.temporal_run_id`
- `origin.metadata.trigger_repo`
- `origin.metadata.starting_branch`
- `origin.metadata.working_branch`
- `origin.metadata.trigger_job_id`
- `origin.metadata.trigger_step_id`
- `origin.metadata.signal`

Validation rules:
- Metadata keys are snake_case.
- `temporal_run_id` is diagnostic metadata, not the durable proposal origin identity.
- Raw provider credentials, tokens, and secret-like values are never stored in origin metadata.

## Provider-Specific Metadata

Fields:
- `provider`: provider name associated with the external issue destination.
- `issue_type`, `project_key`, `labels`, `components`, or provider-specific configured fields for Jira.
- `repository`, `installation`, labels, issue number, or hidden marker fields for GitHub.
- Provider returned identifiers and sync cursors when needed.

Validation rules:
- Provider-specific metadata must not alter canonical delivery-record semantics.
- Secret-like values and auth headers are forbidden.
- Provider metadata is used for dedup and sync only after canonical policy validation passes.

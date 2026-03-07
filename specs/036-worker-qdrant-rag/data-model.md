# Data Model: Direct Worker Qdrant Retrieval Loop

## ContextPack
- **Fields**: `context_text` (markdown), `items` (ordered list of ContextItem), `filters` (repo/tenant/run), `budgets` (token + latency limits), `usage` (actual tokens, latency), `telemetry_id` (for dashboards), `retrieved_at` (UTC iso string).
- **Relationships**: Aggregates many ContextItems; produced by both direct Qdrant client and RetrievalGateway.

## ContextItem
- **Fields**: `score` (float), `source` (path or URI), `offset_start`, `offset_end`, `trust_class` (`canonical|overlay|external`), `run_id` (optional), `chunk_hash`, `payload` (subset of Qdrant payload for debugging).
- **Purpose**: Provide citations for prompt injection and auditing.

## RetrievalQuery
- **Fields**: `query` (text), `top_k`, `filters` (repo, tenant, namespace, payload constraints), `overlay_policy` (`include|skip`), `transport` (`direct|gateway`), `budgets` (token, latency), `request_id`.
- **Usage**: Input to CLI/gateway; logged for observability.

## OverlayChunk
- **Fields**: `collection_name`, `path`, `chunk_hash`, `text`, `vector`, `trust_class="workspace_overlay"`, `run_id`, `expires_at`, `created_at`.
- **Behavior**: Lives in per-run collection or canonical collection with payload markers. Dedup key `(path, chunk_hash)` determines merge priority.

## TelemetryEvent
- **Fields**: `event_name` (`embedding_started`, `embedding_completed`, `qdrant_query_completed`, `overlay_upsert_completed`), `job_id`, `run_id`, `duration_ms`, `payload_size`, `filters`, `error` (optional).
- **Relation**: Emitted by CLI and queue worker to central observability pipeline.

## RetrievalGatewayPolicy
- **Fields**: `allowed_repos`, `allowed_tenants`, `max_top_k`, `max_latency_ms`, `auth_scope` (worker token), `quota_window` (per worker), `audit_log_id`.
- **Purpose**: Enforces governance when direct Qdrant access is blocked.

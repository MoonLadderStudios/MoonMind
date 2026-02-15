# MoonMind Memory Architecture (Desired State, Phased)

Status: Proposed  
Last Updated: 2026-02-13  
Scope: Chat, RAG, Spec workflow, orchestrator, and agent queue memory behavior

## 1) Intent

MoonMind needs a memory architecture that:

- Preserves current behavior by default.
- Improves retrieval quality and cross-run continuity.
- Keeps security and auditability first-class.
- Can be rolled out incrementally with runtime feature flags.

This document replaces the prior 3-phase draft with a more granular phase model that is safe to adopt in production.

## 2) Current Project Context

MoonMind already has the right base primitives:

- LlamaIndex + Qdrant retrieval in `chat` and `/context`.
- Durable workflow state in PostgreSQL (`spec_workflow_runs`, `spec_workflow_task_states`, `orchestrator_runs`, `agent_jobs`).
- Durable artifact storage under `var/artifacts/spec_workflows/<run_id>` and `var/artifacts/agent_jobs/<job_id>`.
- Celery + RabbitMQ for background and orchestration work.

Target design should extend these components, not replace them.

## 3) Tool Research and Design Implications

The following tool capabilities were researched from primary documentation and used to shape this architecture.

### 3.1 Qdrant (semantic store)

Observed capabilities:

- Qdrant supports hybrid and multi-stage queries with `prefetch`, plus fusion methods including RRF and DBSF.
- Payload-based filtering and payload indexing are first-class, which is critical for namespace and project isolation.
- Qdrant recommends single-collection multitenancy patterns for most users, with payload partitioning.
- Snapshot backup and remote S3 upload/restore are supported, with restore expected on same minor version.

Architecture implications:

- Phase 3 uses payload-filtered dense retrieval as the default semantic memory base.
- Phase 4 adds hybrid retrieval and reranking on top of that base.
- Phase 7 disaster recovery can rely on snapshots plus object storage.

### 3.2 LlamaIndex (retrieval and memory orchestration)

Observed capabilities:

- LlamaIndex memory model supports short-term memory and optional long-term blocks.
- Metadata filters support complex boolean conditions and can be pushed into vector store queries.
- Reciprocal rerank fusion and post-retrieval rerankers are available for higher quality retrieval.

Architecture implications:

- Phase 3 introduces strict metadata filters for namespace/project/security scope.
- Phase 4 adds retrieval fusion and reranking without replacing MoonMind API contracts.
- Existing `QdrantRAG` path can evolve into a Retrieval Gateway without changing client APIs.

### 3.3 PostgreSQL (control plane and episodic ledger)

Observed capabilities:

- Row-Level Security (RLS) enables deny-by-default semantics unless explicit policies exist.
- `jsonb` with GIN indexes supports efficient semi-structured event payload filtering.
- Declarative partitioning is suitable for high-volume append-only event ledgers.
- `CREATE INDEX CONCURRENTLY` supports online index rollouts.

Architecture implications:

- Phase 1 enforces namespace identity and access checks.
- Phase 2 stores append-only episodic events with `jsonb` metadata.
- Phase 7 applies retention and partition maintenance for long-term stability.

### 3.4 Celery + RabbitMQ (memory jobs)

Observed capabilities:

- Celery supports route-based queue partitioning and task canvas primitives (chain/group/chord).
- Celery reliability controls (`acks_late`, retries, backoff) fit idempotent memory pipelines.
- RabbitMQ quorum queues are the modern replicated queue model; dead-letter and delivery-limit behavior can be configured for safer retries.

Architecture implications:

- Phase 2 event ingest and Phase 5 consolidation run as dedicated queue workloads.
- Memory jobs stay isolated from latency-sensitive chat paths.
- Retry and lease behavior align with existing workflow patterns in MoonMind.

### 3.5 Object storage (artifact and snapshot durability)

Observed capabilities:

- S3 provides strong consistency and versioning controls useful for artifact immutability and recovery workflows.
- Qdrant snapshot flows can integrate with S3-compatible storage.

Architecture implications:

- Phase 2 stores raw artifacts by reference, not inline memory payload.
- Phase 7 includes backup and recovery drills across Postgres + Qdrant + artifact store.

## 4) Architecture Invariants

These invariants apply to all phases:

- Every read/write is namespace-scoped.
- Every memory-derived answer/action has provenance.
- Raw logs remain in artifact storage by default; memory stores summaries and metadata.
- Canonical memory writes are review-gated.
- Memory failure cannot take down core chat or workflow operation.

## 5) Phase Model (Expanded)

The architecture is split into 8 phases.  
Each phase is valid in isolation (only that phase enabled) and in cumulative rollout mode.

### Phase Overview

| Phase | Name | Main outcome |
|---|---|---|
| 0 | Compatibility Baseline | Current MoonMind behavior, no new memory features required |
| 1 | Namespace and Policy Envelope | Uniform access policy and namespace identity |
| 2 | Episodic Ledger | Durable action history across workflows, queue, orchestrator |
| 3 | Semantic Memory Foundation | Metadata-rich dense retrieval with isolation filters |
| 4 | Hybrid Retrieval and Reranking | Higher retrieval quality (dense + lexical + rerank) |
| 5 | Consolidation and Canonical Registry | Summaries, reflections, approved truths |
| 6 | Personal and Team Memory | Scoped personalization and cross-agent continuity |
| 7 | Reliability, Retention, and Recovery | Backup/replay/retention hardening and compliance controls |

## 6) Feature Flag Contract

To guarantee safe rollout and isolated execution:

- `MEMORY_MODE=off|phase`
- `MEMORY_PHASE=<0..7>`
- `MEMORY_PHASE_STRATEGY=cumulative|isolated`
- `MEMORY_FAIL_OPEN=true|false` (default `true`)

Behavior:

- `off`: use current baseline behavior.
- `phase + cumulative`: enable all components where `component.phase <= MEMORY_PHASE`.
- `phase + isolated`: enable only components tagged with `component.phase == MEMORY_PHASE`, plus compatibility adapters.
- `MEMORY_FAIL_OPEN=true`: any memory subsystem failure falls back to baseline chat/workflow behavior.

Compatibility adapter requirements:

- API contracts must stay stable for `/v1/chat/completions`, `/context`, workflow/orchestrator/queue routes.
- Disabled phase features return empty contributions, not errors, unless explicitly configured strict.

## 7) Component-to-Phase Mapping

| Component | Phase | If isolated, app behavior |
|---|---|---|
| Existing dense RAG (`QdrantRAG`) | 0 | Current behavior unchanged |
| Namespace resolver + policy service | 1 | Requests run with namespace checks, memory writes no-op if store missing |
| `memory_events` ledger + producers | 2 | Event capture active; retrieval still baseline dense RAG |
| Semantic metadata schema + filtered retrieval | 3 | Dense retrieval with filters; no canonical/personal memory required |
| Hybrid retriever + reranker | 4 | Hybrid path active; falls back to dense if sparse/reranker unavailable |
| Consolidation workers + summaries | 5 | Summary/canonical lookup optional; retrieval continues without it |
| Personal/team preference store + thread checkpoints | 6 | Personalization active; no dependency on canonical layer |
| Backup/retention/replay controllers | 7 | Operational hardening active; functional paths remain baseline-compatible |

## 8) Detailed Phase Specifications

### Phase 0: Compatibility Baseline

Active components:

- Current chat and `/context` retrieval path.
- Existing workflow/orchestrator/agent queue DB and artifact models.

Data requirements:

- No new schema.

Fallback:

- Not applicable; this is the fallback target for all other phases.

Definition of done:

- MoonMind behavior matches pre-memory-enhancement behavior.

### Phase 1: Namespace and Policy Envelope

Active components:

- Namespace resolver for chat/workflow/orchestrator/queue requests.
- Central policy service: read/write authorization, redaction hooks, provenance enforcement.

Data requirements:

- `memory_namespaces` and namespace ACL metadata (or equivalent mapping tables).

Read behavior:

- Retrieval requests apply namespace and scope checks before data access.

Write behavior:

- Unauthorized writes denied.
- Authorized writes can no-op safely if target phase storage is disabled.

Isolated operation:

- App still uses baseline retrieval and workflows.
- Policy layer wraps existing flows without requiring later-phase tables.

Definition of done:

- No cross-namespace retrieval/write bleed.
- Deny-by-default for unresolved namespace identity.

### Phase 2: Episodic Ledger

Active components:

- Append-only `memory_events` ingestion from:
  - chat tool calls,
  - spec workflow task transitions,
  - orchestrator plan steps,
  - agent queue lifecycle events.

Data requirements:

- `memory_events` table with `jsonb` payload and GIN indexes.
- Optional partitioning by month/project.

Read behavior:

- Optional episodic snippets can augment prompts for recent failures/actions.

Write behavior:

- Events are metadata-first with artifact references (`artifact_path`, `digest`), not raw logs.

Isolated operation:

- If only Phase 2 is enabled, event capture works and app retrieval remains baseline.

Definition of done:

- All target pipelines produce structured events.
- Event ingest failures do not fail user-visible requests.

### Phase 3: Semantic Memory Foundation

Active components:

- Metadata-hardened semantic records in Qdrant.
- Retrieval gateway with mandatory namespace/project/security filtering.

Data requirements:

- `memory_records` metadata model and ingestion pipeline updates.
- Qdrant payload schema and payload indexes for filter keys.

Read behavior:

- Dense retrieval + metadata filter constraints.

Write behavior:

- All records include provenance and freshness metadata.

Isolated operation:

- If only Phase 3 is enabled, filtered dense retrieval works with current chat.
- If semantic index unavailable, `MEMORY_FAIL_OPEN=true` reverts to baseline path.

Definition of done:

- Retrieval returns only namespace-allowed documents.
- Provenance is attached to context snippets.

### Phase 4: Hybrid Retrieval and Reranking

Active components:

- Hybrid retrieval (dense + lexical/sparse).
- Reranker stage for final context candidate ordering.

Data requirements:

- Sparse representation fields and reranker configuration.

Read behavior:

- Query -> filtered candidate generation -> fusion -> rerank -> context package.

Write behavior:

- Indexer persists both semantic and lexical retrieval signals where configured.

Isolated operation:

- If sparse or reranker is down, system degrades to Phase 3 dense retrieval automatically.

Definition of done:

- Measured improvement on retrieval quality benchmarks (Recall@k, nDCG).

### Phase 5: Consolidation and Canonical Registry

Active components:

- Consolidation workers that turn episodic streams into summaries.
- Canonical registry for approved memory statements.

Data requirements:

- `memory_summaries`, `canonical_memory`, approval metadata.

Read behavior:

- High-impact workflows prefer canonical records first, then retrieval corpus.

Write behavior:

- Canonical writes require review state and approver identity.

Isolated operation:

- If only Phase 5 is enabled, registry lookup is additive; missing summaries do not block baseline answers.

Definition of done:

- Canonical retrieval path is active and auditable.
- Summary objects maintain source-event/source-artifact traceability.

### Phase 6: Personal and Team Memory

Active components:

- User/team preference memory in sub-namespaces.
- Thread checkpoint memory for long-running multi-agent flows.

Data requirements:

- `memory_preferences` and `thread_checkpoints` (or equivalent contracts).

Read behavior:

- Preferences are opt-in and scope-checked.

Write behavior:

- Preference writes are isolated from project canonical memory.

Isolated operation:

- If only Phase 6 is enabled, preference memory functions while retrieval stays baseline.

Definition of done:

- No preference leakage across teams/projects/users.
- Checkpoint continuity works across queue/orchestrator/workflow hand-offs.

### Phase 7: Reliability, Retention, and Recovery

Active components:

- Backup automation (Postgres + Qdrant + artifact store).
- Retention controllers and legal hold controls.
- Replay tooling for memory audits and incident response.

Data requirements:

- Retention policy metadata and backup catalogs.

Read behavior:

- Unchanged functional behavior; controls are operational.

Write behavior:

- Retention lifecycle applies by namespace and record class.

Isolated operation:

- If only Phase 7 is enabled, app runs baseline behavior with improved operational resilience.

Definition of done:

- Restore and replay drill success in staging.
- Retention jobs operate without affecting request availability.

## 9) API and Schema Contracts (Target)

### 9.1 Core contracts

- `MemoryEvent` (Phase 2)
  - `event_id`, `timestamp`, `namespace_id`, `project_id`
  - `thread_id`, `run_id`, `task_id`, `actor_type`, `actor_id`
  - `event_type`, `status`, `summary`, `artifact_refs[]`
  - `security_scope`, `redaction_status`

- `MemoryRecord` (Phase 3)
  - `record_id`, `record_type`, `namespace_id`, `project_id`
  - `content_ref_or_text`, `metadata`, `source_ref`, `created_at`, `expires_at`

- `MemorySummary` (Phase 5)
  - `summary_id`, `summary_scope`, `source_event_ids[]`, `source_artifact_refs[]`
  - `summary_text`, `review_state`, `quality_score`

- `CanonicalMemory` (Phase 5)
  - `canonical_id`, `domain`, `statement`, `evidence_refs[]`
  - `approval_state`, `approved_by`, `approved_at`, `version`

### 9.2 Retrieval contract

Inputs:

- actor identity, namespace, query, optional `thread_id`.

Pipeline:

- policy filter -> candidate retrieval -> ranking -> context packaging with citations.

Outputs:

- context bundle with provenance, trust class, and token budget telemetry.

### 9.3 Write contract

- all writes pass namespace policy + redaction gate.
- all writes emit audit events.
- canonical writes require approval state transition.

## 10) Implementation Hooks in MoonMind

Likely integration points:

- Retrieval path:
  - `moonmind/rag/retriever.py`
  - `api_service/api/routers/chat.py`
  - `api_service/api/routers/context_protocol.py`

- Workflow/orchestration event producers:
  - `moonmind/workflows/speckit_celery/tasks.py`
  - `moonmind/workflows/orchestrator/tasks.py`
  - `moonmind/workflows/agent_queue/service.py`

- Storage and policy services:
  - new `moonmind/memory/*` package for policy, repository, and consolidation logic.

## 11) Operational SLO and Quality Gates

Minimum runtime SLOs:

- Memory subsystem failure must not hard-fail chat/workflow endpoints when `MEMORY_FAIL_OPEN=true`.
- Retrieval latency budgets must stay bounded by configuration (p95 target set per deployment).
- Event ingestion backlogs must alert before SLO impact.

Quality gates per phase:

- Phase 3+: citation coverage.
- Phase 4+: retrieval benchmark gain over dense-only baseline.
- Phase 5+: canonical contradiction and stale-summary rates.
- Phase 7: restore drill and replay drill pass rate.

## 12) References (Primary Sources)

Qdrant:

- Hybrid and multi-stage queries (prefetch, RRF, DBSF): https://qdrant.tech/documentation/concepts/hybrid-queries/
- Filtering and payload model: https://qdrant.tech/documentation/concepts/filtering/
- Multitenancy guidance: https://qdrant.tech/documentation/guides/multiple-partitions/
- Snapshots and S3 integration: https://qdrant.tech/documentation/concepts/snapshots/

LlamaIndex:

- Memory module concepts: https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/memory/
- Metadata filter API: https://docs.llamaindex.ai/en/stable/api_reference/storage/vector_store/
- Reciprocal rerank fusion retriever: https://docs.llamaindex.ai/en/stable/api_reference/retrievers/query_fusion/

PostgreSQL:

- Row-Level Security: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- `jsonb` indexing: https://www.postgresql.org/docs/current/datatype-json.html
- Table partitioning: https://www.postgresql.org/docs/current/ddl-partitioning.html
- `CREATE INDEX CONCURRENTLY`: https://www.postgresql.org/docs/current/sql-createindex.html

Celery:

- Routing tasks and queues: https://docs.celeryq.dev/en/stable/userguide/routing.html
- Canvas workflows: https://docs.celeryq.dev/en/stable/userguide/canvas.html
- Task reliability and retries: https://docs.celeryq.dev/en/stable/userguide/tasks.html

RabbitMQ:

- Quorum queues: https://www.rabbitmq.com/docs/quorum-queues
- Dead-lettering and reliability details: https://www.rabbitmq.com/docs/dlx

S3:

- Strong consistency: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html#ConsistencyModel
- Versioning: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html

# MoonMind Memory Architecture

## Goals

- Manage more context than can fit in a single context window (Beads, LlamaIndex)
- Learn from mistakes (Beads, Ledger)
- Build project context naturally (Mem0)
- Preserve decisions, clarifications, and conclusions (Mem0)
- Stratify context by significance (Mem0)



## Relevant Tools Already in Use

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

### 3.5 Object storage (artifact and snapshot durability)

Observed capabilities:

- S3 provides strong consistency and versioning controls useful for artifact immutability and recovery workflows.
- Qdrant snapshot flows can integrate with S3-compatible storage.

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



## 3) Memory Planes (Types)

MoonMind memory is split into three orthogonal planes:

| Plane | Primary question it answers | Source of truth | Typical objects | Leading option(s) |
|---|---|---|---|---|
| A) Planning Memory (Beads) | “What should we do next, and what blocks it?” | Repo-scoped planning graph | epics/tasks, deps, claims/ownership, discovered follow-ups | **Beads** (steveyegge/beads) |
| B) Task History Memory | “What happened last time we tried this?” | Append-only run ledger + artifacts | runs, digests, error signatures, fix patterns, outcomes | **Run Ledger + Digests + Fix Library** |
| C) Long-Term Memory | “What do we know / how do we do this?” | Canonical registry + semantic store (and/or Mem0) | decisions, conventions, playbooks, preferences, knowledge summaries | **Native (Qdrant + Postgres)** and/or **Mem0** |

### 3.1 How the planes compose in the runtime


Recommended default composition for a new task run:

1) **Planning prefetch (Plane A)**: if a task is linked to a Beads issue, load the issue + deps + current “ready” siblings.
2) **History prefetch (Plane B)**: retrieve similar run digests, and any matching error-signature playbooks.
3) **Long-term prefetch (Plane C)**: prefer canonical statements + curated summaries; then fall back to semantic retrieval.
4) Execute the selected skill (spec/build/implement/test).
5) **Writeback**
   - Update Beads issue state (claimed/closed + discovered follow-ups).
   - Append to run ledger + store artifacts.
   - Generate digest + error signature updates.
   - Optionally promote to canonical memory (human-gated).

### 3.2 Plane boundaries and non-goals

- Planning memory is NOT semantic recall. It is a task/dependency graph.
- Task history is NOT canonical truth. It is evidence + outcomes + “what worked”.
- Long-term memory is NOT raw logs. It is curated, provenance-backed knowledge.

## 4) Plane A: Planning Memory (Beads)

### 4.1 Purpose

Planning memory provides durable, distributed “next-step” state:

- break work into issues
- encode dependencies and blockers
- enable safe multi-agent claiming
- allow “discovered work” to be captured as new nodes
- keep planning repo-local and reviewable (git-native)

### 4.2 Leading option: Beads

Use Beads as the repo-scoped planning graph that agents can read/write.

**MoonMind integration contract (recommended)**

- Add optional `planning_ref` to tasks/runs:
  - `planning_system = beads`
  - `planning_repo = <repo>`
  - `planning_id = <beads_issue_id>`
- In the worker:
  - pre-run: `beads.get_issue(planning_id)` + deps + “ready” siblings (context pack)
  - start-run: `beads.claim(planning_id, run_id)`
  - end-run: `beads.close(planning_id, outcome)` and `beads.create(discovered_followups[])`

**Operational stance**
- Prefer calling Beads via CLI (JSON output) from workers that already have shell access.
- Treat Beads writes as best-effort and idempotent (do not block the run on Beads).

### 4.3 Alternatives within Plane A

| Option | Pros | Cons | When to pick |
|---|---|---|---|
| Beads | git-native, dependency graph, agent-friendly | introduces a new repo-local substrate | when you want agents to manage multi-step plans autonomously |
| GitHub Issues/Projects | familiar UI + integrations | not repo-local; API friction; weaker offline/distributed workflows | when humans already live in GitHub issues |
| Spec Kit `plan.md`/`tasks.md` as planning | already in-repo | harder to compute “ready”; harder to claim/merge | when you want minimal new tooling |
| MoonMind internal DAG (DB) | unified control plane | not git-native; harder to review/merge per repo | when you need centralized multi-repo planning |

### 4.4 Persistence + distributed systems options for Plane A

**Persistence backends**
- **Git-backed repo branch (recommended)**: keep Beads state in the repo under a dedicated branch (e.g. `beads-sync`) to reduce merge conflicts with code changes.
- **SQL-backed local store**: Beads can be backed by a local DB (e.g., SQLite) for dev simplicity; treat the serialized Beads dataset as the artifact committed to git.
- **Versioned SQL store (team mode)**: use a versioned SQL backend (e.g. Dolt-style workflows) if you want DB-level merges and history alongside git workflows.

**Distributed patterns**
- **Many agents / many machines**: each worker uses its own clone (preferred), pulls the Beads branch, claims work, and pushes updates.
- **Worktrees**: avoid background/daemonized “auto commit/push” patterns if worktrees share state; prefer explicit, no-daemon CLI calls in the worker.
- **Conflict model**: accept occasional merge conflicts and resolve via git; keep Beads payloads small and stable to limit conflict frequency.

**Reliability**
- Beads updates must never be a hard dependency for chat/workflows.
- If Beads is down or conflicts, runs proceed; the worker records a ledger event noting the planning write failure.

## 5) Plane B: Task History Memory (Run Ledger + Digests + Fix Patterns)

### 5.1 Purpose

Task history memory turns “runs” into durable, queryable evidence:

- auditability (what happened)
- replayability (what changed, what commands ran, what tests ran)
- learning loops (what fixes worked)
- retrieval (have we tried this before?)

### 5.2 Leading option: Run Ledger (Postgres) + Artifact Store + Digest Index

**Record an append-only Run Ledger (source of truth)**
- Postgres tables store structured run fields (IDs, refs, runtime/model, outcome, touched scope).
- Large artifacts (logs, patches, test reports) are stored in an artifact store and referenced by URI.

**Generate a Run Digest (semantic retrieval)**
- After each run, a background job creates a compact digest:
  - intent, result, key changes, decisions, gotchas, next steps
- Embed/index the digest (NOT raw logs) for fast “what happened last time?” retrieval.

**Maintain an Error Signature → Fix Pattern library (procedural memory)**
- Derive `error_signature` from logs (regex + heuristics + optional LLM extraction).
- Store “fix playbooks” keyed by signature:
  - what worked, constraints, environment notes, links to runs/PRs.

### 5.3 Alternatives within Plane B

| Option | Pros | Cons | When to pick |
|---|---|---|---|
| Postgres ledger + artifacts + digest index (recommended) | strongest provenance + analytics + replay | more plumbing than “just mem0” | when correctness and debugging matter (game-dev CI, UE builds) |
| “Mem0 only” (no ledger) | fastest integration | weak audit trail; hard to replay | only for small personal workflows |
| Observability/log platforms (ELK/OTel) as history | strong logs/search | not tailored for “runs” semantics | complementary, not a replacement |

### 5.4 Persistence + distributed systems options for Plane B

**Persistence**
- **Postgres (recommended)**:
  - append-only tables, `jsonb` for semi-structured payloads
  - partition monthly by `project_id` for long retention
  - RLS for deny-by-default and namespace isolation
- **Artifact store**:
  - dev: local filesystem under `var/artifacts/...`
  - prod: S3-compatible object store with versioning + lifecycle policies
- **Digest index**:
  - Qdrant collection with strict payload filters (namespace/project/repo)
  - snapshots to object storage for recovery

**Distributed patterns**
- Run ledger writes are **idempotent** and keyed by `run_id`.
- Digest generation runs on a dedicated Celery queue; retry-safe jobs re-check `run_id` state.
- RabbitMQ quorum queues recommended for replicated queue durability.
- If Qdrant is unavailable, store digests in Postgres and backfill embeddings later (fail-open).

## 6) Plane C: Long-Term Memory (Canonical + Semantic + Personal/Team)

### 6.1 Purpose

Long-term memory is where MoonMind stores stable knowledge that should be reused:

- canonical “how we do X” decisions and conventions
- curated summaries and reflections
- team/user preferences (scoped)
- procedural playbooks (where they generalize beyond single error signatures)

### 6.2 Leading options for Plane C

#### Option C1 (Native): Qdrant + Postgres (Canonical Registry) + Consolidation Workers

This follows the existing Phase 3–6 design:

- semantic retrieval records in Qdrant (dense/hybrid, with strict metadata filters)
- canonical registry in Postgres with human-gated approval
- consolidation workers that create summaries/rollups
- personal/team preferences stored in scoped namespaces
- thread checkpoints for long-running multi-agent continuity

#### Option C2 (Mem0-backed): Mem0 as the memory “API layer”, backed by your stores

Use Mem0 as a standardized memory interface, but keep the Run Ledger as source of truth.

- Run digests can be written as episodic memories (with metadata filters for repo/project)
- promoted “Project Memory” becomes semantic/canonical memories with `canon=true`
- procedural memories can store fix playbooks beyond a single run
- underlying persistence still remains Postgres + vector store (Qdrant) + artifact pointers

**Policy note**
Even with Mem0, keep:
- provenance pointers (run_id, commit_sha, PR URL, artifact URIs)
- review gating for canon memory
- namespace partitioning to avoid contamination

### 6.3 Extensions / additional options (within Plane C)

| Option | Adds | Why it might matter |
|---|---|---|
| GraphRAG / Knowledge-Graph RAG | relationship-aware retrieval over docs | improves “big picture” answers across many design docs |
| Dedicated graph DB (Neo4j/Memgraph) | deep multi-hop queries | if dependency reasoning becomes a primary workload |
| LlamaIndex KG indexes | quick KG experiments | if you already rely heavily on LlamaIndex orchestration |

### 6.4 Persistence + distributed systems options for Plane C

**Native (C1)**
- Postgres canonical tables (replicated/managed DB options).
- Qdrant single-node (dev) → clustered (team/prod) with snapshot/restore drills.
- Consolidation jobs on Celery; isolate from chat latency paths.

**Mem0-backed (C2)**
- Mem0 service can be deployed as:
  - single instance (dev) with Postgres + Qdrant
  - horizontally scaled stateless API layer (prod), using shared persistence
- Vector store options:
  - Qdrant (already in stack)
  - alternative managed vector stores if needed later
- Optional graph store:
  - if Mem0 graph memory is enabled, use a graph DB appropriate to scale and ops constraints.

## 7) Tool Research and Design Implications

(Keep existing sections 3.1–3.5 unchanged, renumbered here if desired.)
Additions:

### 7.6 Beads (planning substrate)

Architecture implications:

- Beads is a repo-scoped distributed planning layer; it should be treated as an optional plane.
- MoonMind should integrate via an adapter that emits/consumes stable JSON for agents.
- Beads should not be a hard dependency for executing tasks.

### 7.7 Mem0 (memory orchestration layer)

Architecture implications:

- Mem0 can sit above the native stores to unify “add/search/update” memory operations.
- MoonMind should keep run ledger + artifacts as source of truth, and store pointers + digests in Mem0.
- Canonical memory should remain review-gated regardless of which memory API layer is used.

## 8) Architecture Invariants

These invariants apply to all phases and all planes:

- Every read/write is namespace-scoped.
- Every memory-derived answer/action has provenance.
- Raw logs remain in artifact storage by default; memory stores summaries and metadata.
- Canonical memory writes are review-gated.
- Memory failure cannot take down core chat or workflow operation.
- Planning memory (Beads) is repo-scoped and must not create cross-namespace leakage.

## 9) Phase Model (Expanded)

The existing Phase 0–7 model remains the safe rollout strategy.

Important nuance:
- Phases primarily describe the rollout for Plane B (task history) and Plane C (long-term).
- Plane A (Beads) is orthogonal and can be enabled independently, but benefits from Phase 1 policy envelopes and Phase 2+ ledger linking.

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

## 10) Feature Flag Contract

Keep existing memory phase flags, and add plane toggles.

Existing:
- `MEMORY_MODE=off|phase`
- `MEMORY_PHASE=<0..7>`
- `MEMORY_PHASE_STRATEGY=cumulative|isolated`
- `MEMORY_FAIL_OPEN=true|false` (default `true`)

Plane toggles (new):
- `PLANNING_MODE=off|beads`
- `TASK_HISTORY_MODE=off|ledger` (defaults to `ledger` when `MEMORY_PHASE>=2`)
- `LONG_TERM_MODE=off|native|mem0` (defaults to `native` when `MEMORY_PHASE>=3`)

Notes:
- `PLANNING_MODE=beads` can be enabled even when `MEMORY_MODE=off`.
- `LONG_TERM_MODE=mem0` does not remove the need for the ledger/artifact stores; it changes the memory API layer.

## 11) Plane + Phase mapping (new)

| Plane | Minimum safe phase | Why |
|---|---:|---|
| Planning (Beads) | 0 (better with 1/2) | can run repo-local; Phase 1 adds scope/policy; Phase 2 adds run linkage |
| Task History | 2 | requires the episodic ledger + artifact refs |
| Long-Term | 3 (better with 5/6) | requires semantic store + filters; consolidation/personalization later |

## 12) Component-to-Phase Mapping

(Keep existing table; add the Beads adapter as orthogonal.)

Add row:
- `Beads planning adapter` | Plane A (orthogonal) | Adds planning context; does not alter baseline retrieval.

## 13) Detailed Phase Specifications

(Keep existing Phase 0–7 sections unchanged, but cross-reference the planes where relevant.)

- Phase 2 now explicitly covers Plane B: Run Ledger + artifacts + digests.
- Phase 5 consolidation can produce Plane C summaries AND optionally generate “promotion candidates” from Plane B digests.
- Phase 6 personal/team memory lives in Plane C but can incorporate user preferences about planning/task history inclusion.

## 14) API and Schema Contracts (Target)

Add plane-specific contracts.

### 14.1 Planning (Plane A)
- `PlanningRef`
  - `planning_system`, `repo`, `planning_id`
  - `linked_task_id`, `linked_run_id`, `linked_pr_url`

### 14.2 Task history (Plane B)
- `TaskRun` (ledger)
  - `run_id`, `task_id`, `thread_id`
  - `repo`, `base_branch`, `head_branch`, `commit_sha`, `pr_url`
  - `worker_runtime`, `model`, `effort`
  - `status`, `error_signature`
  - `files_changed[]`, `tests_run[]`, `commands[]`
  - `artifact_refs[]`
- `RunDigest`
  - `run_id`, `intent`, `result`, `changes`, `decisions`, `gotchas`, `next_steps`
- `FixPattern`
  - `error_signature`, `fix_steps[]`, `evidence_run_ids[]`, `evidence_refs[]`

### 14.3 Long-term (Plane C)
(Keep `MemoryRecord`, `MemorySummary`, `CanonicalMemory`, `memory_preferences`, `thread_checkpoints` concepts as-is.)

## 15) Implementation Hooks in MoonMind

Keep existing likely integration points, plus:

- Planning adapter:
  - new `moonmind/planning/beads_adapter.py` (or equivalent)
  - worker “pre-run context” and “post-run writeback” hooks

- Task history:
  - new post-run digest Celery task
  - error signature extraction step
  - fix-pattern store upsert

## 16) Operational SLO and Quality Gates

Keep existing SLOs; add:

- Planning updates are best-effort; failures must not fail runs.
- History digest jobs are backlog-tolerant; failure alerts but no user-facing hard failure.
- Canonical promotion remains human-gated; measure “false canon” rate.

## 17) References (Primary Sources)

Keep existing references, and add:

- Beads: https://github.com/steveyegge/beads
- Mem0 docs: https://docs.mem0.ai/
- Mem0 repo: https://github.com/mem0ai/mem0
```

If you want, I can also provide a **patch-style diff** against the current file, but the above is designed to be a clean “drop-in replacement” that preserves your existing phase model while making the **three memory types** first-class.

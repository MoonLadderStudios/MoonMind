# MoonMind Memory Architecture (Desired State)

Status: Proposed
Last Updated: 2026-02-16
Scope: Chat, RAG, Task Dashboard (agent queue), Spec workflow, orchestrator

## 1) Goals

MoonMind memory exists to make agent runs faster, safer, and more repeatable:

- Assemble **more usable context than fits in a single context window** by composing planning + history + long-term memory + document RAG into a scoped “context pack”.
- **Reduce repeat failures** by surfacing “what happened last time” (similar runs, error signatures, fix patterns) *before* a worker executes.
- Preserve **decisions, conventions, and preferences** as reusable long-term memory with provenance.
- Keep **planning state reviewable and repo-local**, so humans and agents share the same work graph.
- Maintain strict **isolation** (namespace/repo scoped): no cross-repo contamination.
- **Fail-open**: memory accelerates; it is never a hard dependency for chat or task execution.
- Keep **auditability first-class**: every memory contribution is traceable back to evidence (run/job IDs, commits, artifacts).

Non-goals:

- Storing raw logs or entire repos as “memory” (raw data stays in artifacts and source control).
- Auto-promoting unreviewed “truth” into canonical project knowledge.

## 2) Current State (Baseline)

MoonMind already has the core primitives we will extend:

- **Document retrieval (RAG)**: LlamaIndex + Qdrant powering chat and `/context`.
- **Durable execution state**: Postgres tables for spec workflows, orchestrator runs, and agent queue jobs.
- **Durable artifacts**: filesystem artifact roots for workflow runs and agent jobs.
- **Background jobs**: Celery + RabbitMQ for orchestration and asynchronous processing.

This architecture does not replace these primitives. It adds a thin “memory layer” that:
1) reads from them, and
2) writes compact, high-signal summaries back into Qdrant + Mem0.

## 3) Chosen Model: Three Memory Planes

MoonMind uses three orthogonal memory planes. Each plane has a clear purpose and source of truth.

### Plane A — Planning Memory (Beads)

**Question:** “What should we do next, and what blocks it?”

- Beads is the repo-scoped planning substrate (issues + dependencies + claims).
- Git-native and reviewable.
- Best-effort: Beads failures never block task execution.

**Objects:**
- Work items, dependency edges, readiness state, discovered follow-ups, claim/close metadata.

### Plane B — Task History Memory (Run Ledger → Digests → Fix Patterns)

**Question:** “What happened last time we tried something like this?”

**Source of truth:**
- Postgres run/job rows + lifecycle/event streams + timestamps.
- Artifact store for logs/patches/test results.

**Derived retrieval indexes (not sources of truth):**
- **Run Digests**: short structured summaries of intent/result/changes/decisions/gotchas/next steps.
- **Error Signatures → Fix Patterns**: procedural memory keyed by signature, backed by evidence runs.

**Storage:**
- Digests and fix patterns are embedded and indexed in Qdrant (never raw logs).
- Every digest links back to evidence: run/job IDs, commits/PRs, artifact paths.

### Plane C — Long-Term Memory (Mem0)

**Question:** “What do we know / how do we do this here?”

**Chosen approach:**
- Mem0 is the long-term memory API layer for MoonMind.
- Mem0 stores curated, reusable knowledge:
  - decisions, conventions, playbooks, preferences, “how we do X”.
- Mem0 does **not** replace Plane B. Plane B remains the audit trail and evidence base.

**Policy:**
- Every long-term memory entry carries provenance (“derived from run X”, “approved by Y”).
- Only approved/curated classes are used by default during retrieval.

## 4) Read Path: Building the Context Pack

Every chat request and every task run may request a “context pack”. It is assembled in this order:

1) **Planning (Beads) — optional**
- If the request references a Beads work item, load:
  - the issue, its dependencies, currently-ready siblings,
  - acceptance criteria / plan notes.

2) **History (Run Digests + Fix Patterns)**
- Search Qdrant for similar run digests scoped to the same namespace/repo.
- If an error signature is known (or predicted), pull the most successful fix patterns first.

3) **Long-term (Mem0)**
- Query Mem0 for relevant project/team/user memory:
  - conventions, known pitfalls, preferred workflows, playbooks, user/team prefs.

4) **Documents (RAG via LlamaIndex + Qdrant)**
- Retrieve supporting design docs/specs/guides from the existing doc index.

5) **Packaging + budgets**
- Normalize all candidates into one bundle:
  - `text`, `source`, `trust_class`, `provenance`, `recency`, `token_cost`
- Enforce a token budget and include provenance for every included item.

Fail-open behavior:
- If any subsystem is unavailable, that component contributes nothing; the request still runs.

## 5) Write Path: Turning Runs Into Memory

Writeback is automatic, async-first, and idempotent by run/job ID.

### 5.1 On run start
- If linked to Beads: claim the work item with `run_ref`.
- Record minimal start metadata in the existing run/job tables (baseline behavior).

### 5.2 During execution
- Append events to existing run/job event streams (status transitions, key tool calls).
- Store large outputs to artifacts (logs, patches, test output).

### 5.3 On run finish
1) **Generate a Run Digest (Plane B index)**
- Structured summary:
  - intent, outcome, key changes, key decisions, gotchas, next steps
- Link to evidence (run/job id, commit/PR, artifact refs).
- Embed + upsert into Qdrant.

2) **Update Fix Patterns (Plane B procedural memory)**
- Extract/normalize error signatures (from logs, structured errors, and/or LLM extraction).
- When a run succeeds after a fix, attach that run as evidence for the signature and update the playbook.

3) **Promote stable learnings to Mem0 (Plane C)**
- Promotions are small and durable:
  - conventions, environment pitfalls, stable decisions, reusable playbooks.
- Promotions default to `draft` unless explicitly marked `approved` (human- or policy-gated).

4) **Planning writeback to Beads (Plane A)**
- Close/update the work item and create discovered follow-ups as new nodes.

## 6) Storage Contracts

### 6.1 Sources of truth
- **Postgres**: workflow runs, task states, orchestrator runs, agent queue jobs, event logs.
- **Artifact store**:
  - workflow artifacts under `var/artifacts/spec_workflows/<run_id>/`
  - agent job artifacts under `var/artifacts/agent_jobs/<job_id>/`
- **Git**: code + Beads planning state.

### 6.2 Qdrant: retrieval indexes (not truth)
Recommended: one collection with payload partitioning:

Payload keys (minimum):
- `record_kind`: `doc | run_digest | fix_pattern | long_term_shadow`
- `namespace_id`, `repo`, `security_scope`
- `run_ref.kind`, `run_ref.id` (when applicable)
- `created_at`, `expires_at` (optional)
- `trust_class` (e.g., `raw`, `derived`, `approved`)

### 6.3 Mem0: long-term memories
Required metadata on every Mem0 entry:
- `namespace_id`, `repo`, `scope` (`project | team | user`)
- `review_state` (`draft | approved | deprecated`)
- `provenance` pointers (run/job IDs, commits, doc refs)

## 7) Integration Surfaces

MoonMind implements this architecture with four small adapters/services:

- `PlanningAdapter` (Beads)
  - `prefetch(planning_ref) -> planning_context`
  - `claim/close/create_followups(...)` (best-effort)

- `TaskHistoryService`
  - `build_run_digest(run_ref) -> digest`
  - `extract_error_signature(artifacts) -> signature`
  - `upsert_digest_and_fix_patterns(...)`

- `LongTermMemoryService` (Mem0)
  - `search(query, scope, filters) -> memories`
  - `add_or_update(memory, review_state, provenance)`

- `RetrievalGateway`
  - `retrieve_context_pack(query, run_ref?, planning_ref?, budgets) -> context_pack`
  - used by chat, `/context`, and task workers.

## 8) Runtime Controls (Feature Flags)

Minimal flags (fail-open by default):

- `MEMORY_ENABLED=true|false`
- `MEMORY_PLANNING=off|beads`
- `MEMORY_HISTORY=off|digest`
- `MEMORY_LONG_TERM=off|mem0`
- `MEMORY_FAIL_OPEN=true|false` (default `true`)
- `MEMORY_CONTEXT_BUDGET_TOKENS=<int>`

## 9) Operational Expectations

- Memory must never make chat/task endpoints unavailable.
- Indexing jobs are async and backlog-tolerant (alerting, not paging).
- Qdrant/Mem0 outages degrade retrieval quality, not correctness.
- Retention:
  - artifacts follow deployment policy (dev local, prod S3 lifecycle)
  - digests/fix patterns are retained long (small, high-value)
  - long-term memories are versioned; deprecated entries remain discoverable but not injected

## 10) References

- Beads: https://github.com/steveyegge/beads
- Mem0: https://docs.mem0.ai/
- Qdrant: https://qdrant.tech/documentation/
- LlamaIndex: https://docs.llamaindex.ai/

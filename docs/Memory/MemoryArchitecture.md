# MoonMind Memory Architecture (Desired State)

Status: Proposed  
Last Updated: 2026-03-27  
Scope: Chat, RAG, Mission Control, Temporal execution history

## 1. Goals

MoonMind memory exists to make agent runs faster, safer, and more repeatable:

- assemble more usable context than fits in a single context window
- reduce repeat failures by surfacing what happened on similar runs
- preserve decisions, conventions, and preferences with provenance
- keep planning state reviewable and repo-local
- maintain strict repo and namespace isolation
- fail open so memory improves execution but never blocks it
- keep auditability first-class

Non-goals:

- storing raw logs or entire repos as memory
- auto-promoting unreviewed truth into canonical knowledge

## 2. Current state

MoonMind already has the primitives this layer builds on:

- **Document retrieval (RAG)** via LlamaIndex + Qdrant
- **Durable execution state** via Temporal plus Postgres projections
- **Durable artifacts** via the artifact system
- **Task-oriented product surfaces** over Temporal executions

This memory architecture does not replace those primitives. It adds a memory
layer that reads from them and writes compact, high-signal summaries back into
retrieval stores.

## 3. Three memory planes

### Plane A: Planning memory

Question: "What should we do next, and what blocks it?"

- repo-scoped planning state
- best-effort and reviewable
- never a hard dependency for task execution

### Plane B: Task history memory

Question: "What happened last time we tried something like this?"

Source of truth:

- Temporal execution history and visibility metadata
- Postgres execution projections and event metadata
- artifact-backed evidence such as logs, patches, and summaries

Derived retrieval indexes:

- run digests
- error signatures and fix patterns

### Plane C: Long-term memory

Question: "What do we know or how do we do this here?"

- curated conventions, decisions, playbooks, and preferences
- provenance on every durable memory entry
- review state such as draft/approved/deprecated

## 4. Read path

Each chat request or task run may request a context pack assembled from:

1. planning context
2. history digests and fix patterns
3. long-term memory
4. document retrieval
5. packaging and token budgeting

If any subsystem is unavailable, it contributes nothing and the request still
runs.

## 5. Write path

Writeback is automatic, async-first, and keyed by execution identity.

### On run start

- attach planning references when applicable
- record minimal execution metadata

### During execution

- append structured events
- store large outputs in artifacts

### On run finish

1. generate a run digest
2. update fix patterns
3. promote stable learnings into long-term memory when approved
4. write planning follow-ups when applicable

## 6. Storage contracts

### 6.1 Sources of truth

- **Temporal**: workflow lifecycle and history
- **Postgres**: projections, event metadata, and memory-adjacent indexes
- **Artifact store**: execution artifacts and evidence files
- **Git**: source code and planning state

### 6.2 Retrieval indexes

Qdrant or equivalent retrieval indexes should store:

- document chunks
- run digests
- fix patterns
- shadow copies of approved long-term entries when needed for retrieval

### 6.3 Long-term memory records

Each long-term memory entry should include:

- namespace or repo scope
- review state
- provenance to executions, commits, docs, or artifacts

## 7. Integration surfaces

MoonMind implements this architecture with a small set of adapters/services:

- `PlanningAdapter`
- `TaskHistoryService`
- `LongTermMemoryService`
- `RetrievalGateway`

## 8. Runtime controls

Representative flags:

- `MEMORY_ENABLED`
- `MEMORY_PLANNING`
- `MEMORY_HISTORY`
- `MEMORY_LONG_TERM`
- `MEMORY_FAIL_OPEN`
- `MEMORY_CONTEXT_BUDGET_TOKENS`

## 9. Operational expectations

- memory must never make chat or task endpoints unavailable
- indexing is async and backlog-tolerant
- retrieval outages degrade quality, not correctness
- artifacts follow normal retention policy
- high-value digests and approved long-term memories are retained longer than
  raw transient evidence

## 10. References

- Beads: https://github.com/steveyegge/beads
- Mem0: https://docs.mem0.ai/
- Qdrant: https://qdrant.tech/documentation/
- LlamaIndex: https://docs.llamaindex.ai/

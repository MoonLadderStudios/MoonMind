# MoonMind Memory Architecture (Desired State)

Status: Proposed
Last Updated: 2026-09-09
Scope: Chat, Dashboard (workflow and execution surfaces), Spec workflow, system

This desired-state document assumes MoonMind's current Temporal-first bridge architecture. Older queue/Celery terminology should be treated as historical only, not as the current execution substrate.

MoonMind is vector-free: there is no MoonMind-managed vector database,
embedding service, collection, or retrieval index. Ordinary chat and ordinary
workflows need no vector configuration, and explicit vector requirements
(managed semantic retrieval, embedding-backed memory, collection/overlay
administration) are rejected before execution. Retained exact-context,
artifact, and history reads are not equivalent to semantic recall.

## 1) Goals

MoonMind memory exists to make agent runs faster, more secure, and more repeatable:

- Assemble **more usable context than fits in a single context window** by composing planning + history + curated project knowledge into a scoped context bundle.
- **Reduce repeat failures** by surfacing “what happened last time” (similar runs, error signatures, fix patterns) *before* a worker executes.
- Preserve **decisions, conventions, and preferences** as reusable curated knowledge with provenance.
- Keep **planning state reviewable and repo-local**, so humans and agents share the same work graph.
- Maintain strict **isolation** (namespace/repo scoped): no cross-repo contamination.
- **Fail-open**: memory accelerates; it is never a hard dependency for chat or workflow execution.
- Keep **auditability first-class**: every memory contribution is traceable back to evidence (run/job IDs, commits, artifacts).

Non-goals:

- Storing raw logs or entire repos as “memory” (raw data stays in artifacts and source control).
- Auto-promoting unreviewed “truth” into canonical project knowledge.
- Semantic similarity recall over a managed vector index (retired; not equivalent to the exact reads below).

## 2) Current State (Baseline)

MoonMind already has the core primitives we extend:

- **Durable execution state**: Temporal-backed workflow executions plus Postgres-backed execution/projection records used by the dashboard and execution APIs.
- **Durable artifacts and run evidence**: S3-compatible workflow artifacts plus managed-run observability artifacts and workspace-backed log spools.
- **Background orchestration**: Temporal workflows and activities running across specialized worker fleets.
- **Curated project knowledge (Plane C)**: optional Mem0 adapter support for approved project memories with required provenance metadata, disabled unless explicitly enabled for a scoped integration.
- **Exact context assembly**: workflow intent, attachments, artifact refs, and run history composed into a bounded context bundle with budgets and provenance.

This architecture does not replace these primitives. It adds a thin “memory layer” that:
1) reads from them, and
2) writes compact, high-signal summaries back into Postgres-backed projections and artifacts.

## 3) Chosen Model: Three Memory Planes

MoonMind uses three orthogonal memory planes. Each plane has a clear purpose and source of truth.

### Plane A — Planning Memory (Beads)

**Question:** “What should we do next, and what blocks it?”

- Beads is the repo-scoped planning substrate (issues + dependencies + claims).
- Git-native and reviewable.
- Best-effort: Beads failures never block workflow execution.

**Objects:**
- Work items, dependency edges, readiness state, discovered follow-ups, claim/close metadata.

### Plane B — Run History Memory (Run Ledger → Digests → Fix Patterns)

**Question:** “What happened last time we tried something like this?”

**Source of truth:**
- Temporal-backed execution state plus Postgres execution/projection rows, timestamps, and related metadata.
- Artifact storage and managed-run observability artifacts for logs, patches, diagnostics, and test results.

**Derived records (not sources of truth):**
- **Run Digests**: short structured summaries of intent/result/changes/decisions/gotchas/next steps.
- **Error Signatures → Fix Patterns**: procedural memory keyed by signature, backed by evidence runs.

**Storage:**
- Digests and fix patterns live in Postgres-backed projections and artifacts (never raw logs).
- Every digest links back to evidence: `workflowId`, `agentRunId` when applicable, commits/PRs, and artifact refs.

### Plane C — Long-Term Memory (Mem0, opt-in)

**Question:** “What do we know / how do we do this here?”

**Chosen approach:**
- Mem0 is an explicitly scoped, opt-in long-term memory integration for MoonMind.
- Mem0 stores curated, reusable knowledge:
  - decisions, conventions, playbooks, preferences, “how we do X”.
- Mem0 does **not** replace Plane B. Plane B remains the audit trail and evidence base.
- It is disabled by default and never an implicit replacement vector store.

**Policy:**
- Every curated entry carries provenance (“derived from run X”, “approved by Y”).
- Only approved/curated classes are used by default during context assembly.

## 4) Read Path: Building the Context Bundle

Every chat request and every workflow run may request a context bundle. It is assembled in this order:

1) **Planning (Beads) — optional**
- If the request references a Beads work item, load:
  - the issue, its dependencies, currently-ready siblings,
  - acceptance criteria / plan notes.

2) **History (Run Digests + Fix Patterns)**
- Look up related run digests scoped to the same namespace/repo by exact metadata (repo, workflow, error signature, recency).
- If an error signature is known (or predicted), pull the most successful fix patterns first.

3) **Curated project knowledge (Mem0, opt-in)**
- Load relevant conventions, known pitfalls, preferred workflows, playbooks, and user/team prefs.

4) **Attachments and artifacts**
- Resolve explicitly referenced design docs, specs, guides, files, and artifact refs.

5) **Packaging + budgets**
- Normalize all candidates into one bundle:
  - `text`, `source`, `trust_class`, `provenance`, `recency`, `token_cost`
- Enforce a token budget and include provenance for every included item.

Fail-open behavior:
- If any subsystem is unavailable, that component contributes nothing; the request still runs.

## 5) Write Path: Turning Runs Into Memory

Writeback is automatic, async-first, and idempotent by execution identity (`workflowId`, and `agentRunId` for managed-run observability when applicable).

### 5.1 On run start
- If linked to Beads: claim the work item with `run_ref`.
- Record minimal start metadata in the Temporal-backed execution row/projection and related run metadata.

### 5.2 During execution
- Persist lifecycle state through Temporal history, execution projections, and managed-run observability records as appropriate.
- Store large outputs to artifacts (logs, patches, test output).

### 5.3 On run finish
1) **Generate a Run Digest (Plane B record)**
- Structured summary:
  - intent, outcome, key changes, key decisions, gotchas, next steps
- Link to evidence (`workflowId`, `agentRunId` when present, commit/PR, artifact refs).
- Persist to the Postgres-backed projection and publish as an artifact.

2) **Update Fix Patterns (Plane B procedural memory)**
- Extract/normalize error signatures (from logs, structured errors, and/or LLM extraction).
- When a run succeeds after a fix, attach that run as evidence for the signature and update the playbook.

3) **Promote stable learnings to Mem0 (Plane C, opt-in)**
- Promotions are small and durable:
  - conventions, environment pitfalls, stable decisions, reusable playbooks.
- Promotions default to `draft` unless explicitly marked `approved` (human- or policy-gated).

4) **Planning writeback to Beads (Plane A)**
- Close/update the work item and create discovered follow-ups as new nodes.

## 6) Storage Contracts

### 6.1 Sources of truth
- **Postgres**: Temporal-backed execution records/projections, workflow support tables, system runs, run digests, fix patterns, and related event metadata.
- **Artifact store**:
  - workflow artifacts default under `var/artifacts/workflows/<workflow_id>/` and may be relocated via `WORKFLOW_ARTIFACT_ROOT`
  - local-dev Temporal artifact files default under `var/artifacts/temporal_artifacts/`
  - managed-run workspace and observability files may exist under `/work/agent_jobs/<job_id>/...` in worker containers; treat that as runtime-local workspace layout rather than the primary durable artifact root
- **Git**: code + Beads planning state.

### 6.2 Derived records (not truth)
Digest and fix-pattern projections are small, high-value derived records:

Minimum fields:
- `record_kind`: `run_digest | fix_pattern | curated_note`
- `namespace_id`, `repo`, `security_scope`
- `run_ref.kind`, `run_ref.id` (when applicable)
- `created_at`, `expires_at` (optional)
- `trust_class` (e.g., `raw`, `derived`, `approved`)

### 6.3 Mem0: long-term memories (opt-in integration)
Required metadata on every Mem0 entry:
- `namespace_id`, `repo`, `scope` (`project | team | user`)
- `review_state` (`draft | approved | deprecated`)
- `provenance` pointers (`workflowId`, `agentRunId` when applicable, commits, doc refs)

Memory provenance must stay model-agnostic. It records durable evidence pointers, not the model or provider that happened to produce the memory contribution.

## 7) Integration Surfaces

MoonMind implements this architecture with small adapters/services:

- `PlanningAdapter` (Beads)
  - `prefetch(planning_ref) -> planning_context`
  - `claim/close/create_followups(...)` (best-effort)

- `TaskHistoryService`
  - `build_run_digest(run_ref) -> digest`
  - `extract_error_signature(artifacts) -> signature`
  - `upsert_digest_and_fix_patterns(...)`

- `LongTermMemoryService` (Mem0, opt-in)
  - `search(query, scope, filters) -> memories`
  - `add_or_update(memory, review_state, provenance)`

- `ContextAssembly`
  - `assemble_context_bundle(query, run_ref?, planning_ref?, budgets) -> context_bundle`
  - used by chat and workflow workers.

## 8) Runtime Controls (Feature Flags)

Minimal flags (fail-open by default):

- `MEMORY_ENABLED=true|false`
- `MEMORY_PLANNING=off|beads`
- `MEMORY_HISTORY=off|digest`
- `MEMORY_LONG_TERM=off|mem0`
- `MEMORY_FAIL_OPEN=true|false` (default `true`)
- `MEMORY_CONTEXT_BUDGET_TOKENS=<int>`

## 9) Operational Expectations

- Memory must never make chat/workflow endpoints unavailable.
- Indexing jobs are async and backlog-tolerant (alerting, not paging).
- Knowledge/history outages degrade context richness, not correctness.
- Retention:
  - artifacts follow deployment policy (dev local, prod S3 lifecycle)
  - digests/fix patterns are retained long (small, high-value)
  - curated memories are versioned; deprecated entries remain discoverable but not injected

## 10) References

- Beads: https://github.com/steveyegge/beads
- Mem0: https://docs.mem0.ai/ (opt-in integration only; disabled by default)

# 🌙 MoonMind Roadmap

> Tracking the major milestones remaining to fully deliver on the README promise.
>
> Last updated: 2026-03-21

---

## How to read this document

Each milestone maps to a specific claim in the [README](../../README.md). Status uses:

| Tag | Meaning |
|-----|---------|
| ✅ Shipped | Functional and in the main branch |
| 🔧 Partial | Core exists but key pieces are missing |
| 📐 Designed | Architecture/spec exists, implementation not started |
| ⬜ Not Started | No implementation or spec yet |

Remaining items within each milestone are numbered **M.N** (milestone.item) and tracked with checkboxes:
- `[x]` = done
- `[ ]` = still remaining

---

## Milestone 1 — Managed Agent Runtimes ✅

**README claim:** *"MoonMind can run Claude Code, Gemini CLI, and Codex CLI as managed workers on your own infrastructure."*

### What's shipped
- Codex, Gemini, Claude, and Cursor Temporal activity workers (`codex-worker`, `gemini-worker`, `claude-worker`, `cursor-worker`)
- OAuth auth-volume bootstrap for Codex and Gemini (`tools/auth-*.sh`)
- Docker-socket-proxy sandboxing for all workers
- Runtime adapter pattern (`moonmind/agents/`, `moonmind/workflows/temporal/runtime/`)
- Claude API key gate removed — Claude runtime available without API key (#795)
- Cursor CLI added as managed agent runtime (#794)
- Auto-seeding of default auth profiles on API startup

### Remaining tasks
- [x] **1.1** Claude auth volume flow matches Codex/Gemini parity — Spec 072 (`managed-agents-auth`)
- [x] **1.2** Auth profile management UI in Mission Control — API router (`auth_profiles.py`) + dashboard
- [x] **1.3** Worker health checks & readiness probes — Constitution IX
- [x] **1.4** Cursor CLI managed runtime — Spec 094 (Phases 1–5)
- [x] **1.5** Remove Claude API key gate — #795
- [x] **1.6** Auto-seed auth profiles on startup
- [ ] **1.7** Graceful worker pause / unpause — Spec 038/040 (`worker-pause`), API exists, dashboard wiring needed
- [ ] **1.8** Universal Tmate OAuth sessions — Design doc in `docs/ManagedAgents/TmateArchitecture.md` (4 phases: session table, API, UI modal, tmate runner, provider registry)

---

## Milestone 2 — External / Black-Box Agent Coordination 🔧

**README claim:** *"Even cloud-hosted agents like Jules and Codex Cloud benefit from coordination. MoonMind tracks status, injects context, and responds with feedback."*

### What's shipped
- Jules Temporal external-event contract and adapter (`moonmind/jules/`, `docs/ExternalAgents/`)
- External-agent integration system design doc
- Jules end-to-end external event workflow — adapter, event wiring, multi-step `sendMessage` flow, status polling
- External Runs tab removed from Mission Control (external runs now integrated into main task view)

### Remaining tasks
- [x] **2.1** Jules end-to-end external event workflow — Spec 048/066, adapter exists, event wiring complete
- [x] **2.2** Remove External Runs tab — External runs integrated into main dashboard
- [ ] **2.3** Codex Cloud integration adapter — No adapter yet for the hosted Codex product
- [ ] **2.4** Generic external-agent adapter pattern — Designed in `ExternalAgentIntegrationSystem.md`, not generalized in code

---

## Milestone 3 — Multi-Step Planning & Step-Based Context 🔧

**README claim:** *"Break a massive goal into discrete steps with presets, and let MoonMind schedule and sequence them."* / *"Inject the right context into each step and clear it between steps."*

### What's shipped
- Task presets system (spec 026/028/055 — `TaskPresetsSystem.md`)
- Task step templates & step sequencing API (`task_step_templates.py`, `TasksStepSystem.md`)
- Manifest-based task submission (`manifest.schema.json`, `moonmind/manifest/`)
- Task proposal queue for automated step generation
- `proposal_generate` activity implemented

### Remaining tasks
- [ ] **3.1** Fix task proposal system end-to-end — `proposal_generate` activity exists but proposals not surfaced reliably in UI
- [ ] **3.2** Automatic context injection per step — Context pack exists (`rag/context_pack.py`), not wired into step execution
- [ ] **3.3** Context clearing between steps — No implementation; promised in README
- [ ] **3.4** Multi-step workflow visualization in Mission Control — Dashboard shows tasks but not step DAGs
- [ ] **3.5** Preset-driven scheduling (auto-sequence from goal) — Presets exist but goal-to-plan decomposition is manual
- [ ] **3.6** Overhaul and streamline schedules UI — Current schedules interface needs UX improvement

---

## Milestone 4 — Fire-and-Forget Resiliency 🔧

**README claim:** *"Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Backed by Temporal, workflows survive container crashes and restarts. Automatic stuck detection and smart retries."*

### What's shipped
- Temporal foundation (server, DB, namespace init)
- Durable workflows for agent runs (`agent_run.py`, `run.py`)
- Worker crash recovery via Temporal replay
- Recurring task schedules (spec 049)
- TRY_CANCEL activity cancellation — fast cancellation without waiting for activity completion
- Force-terminate path for stuck tasks

### Remaining tasks
- [x] **4.1** Fast cancellation via `TRY_CANCEL` — All `execute_activity` calls now use `ActivityCancellationType.TRY_CANCEL`
- [x] **4.2** Force-terminate path for stuck tasks — Shipped in `9050b4d9`
- [ ] **4.3** Automatic stuck-detection for agent runs — Spec 039 (`worker-self-heal`), partial design
- [ ] **4.4** Smart retry policies per runtime — Temporal retries exist but not tuned per agent type
- [ ] **4.5** Intervention request signaling (agent asks for human help) — README promises "monitor intervention requests"
- [ ] **4.6** Notification system (email/webhook on completion) — No notification channel for fire-and-forget results

---

## Milestone 5 — RAG & Document Retrieval 🔧

**README claim:** *"Ground agents with built-in loaders for GitHub, Jira, Confluence, Google Drive, and local files."*

### What's shipped
- LlamaIndex + Qdrant RAG pipeline (`moonmind/rag/` — 12 source files)
- Data loaders / indexers: GitHub, Jira, Confluence, Google Drive, local files (`moonmind/indexers/`)
- Manifest system: schema validation, reader adapters, pipeline, evaluation (`moonmind/manifest/` — 12 source files)
- Manifest CLI (`moonmind manifest validate|plan|run|evaluate`)
- Manifest ingest Temporal workflow (`moonmind/workflows/temporal/manifest_ingest.py`)
- Spec 088 manifest schema pipeline spec implemented (#796)
- Embedding model updated to current Gemini default (#787)
- Context pack primitives (`rag/context_pack.py`)
- RAG retrieval CLI (`moonmind rag search`)
- RAG overlay and guardrails
- RAG doc ↔ spec consolidation completed (see `docs/tmp/RagDocUpdates.md`)

### Remaining tasks
- [ ] **5.1** End-to-end manifest ingest testing — Manifest pipeline built but not fully tested against live data sources
- [ ] **5.2** RAG retrieval quality validation — Evaluation framework exists (`manifest/evaluation.py`) but no golden datasets or baseline metrics established
- [ ] **5.3** Context pack assembly wired into agent runs — Primitives exist; not integrated into Temporal activity execution
- [ ] **5.4** Index health monitoring — No dashboard view of indexed collections, document counts, or freshness
- [ ] **5.5** Incremental re-indexing — Full reindex only; no delta/incremental update path
- [ ] **5.6** Multi-collection retrieval — Single-collection queries; no cross-collection or federated search

---

## Milestone 6 — Memory & Procedural Learning 📐

**README claim:** *"Procedural memory retains structured summaries from past runs so agents don't repeat the same mistakes."*

### What's shipped
- Memory architecture design doc (`docs/Memory/MemoryArchitecture.md` — "Desired State")

### Remaining tasks
- [ ] **6.1** Run Digests (Plane B — task history summaries) — Architecture defined, no implementation
- [ ] **6.2** Fix Patterns / Error Signatures (procedural memory) — Architecture defined, no implementation
- [ ] **6.3** Long-Term Memory integration (Mem0 / Plane C) — Architecture defined, no integration
- [ ] **6.4** Planning Memory (Beads / Plane A) — Architecture defined, no integration
- [ ] **6.5** Token budgeting & provenance tracking — Designed in memory arch, not implemented
- [ ] **6.6** Memory feature flags (`MEMORY_ENABLED`, etc.) — Defined in spec, not in codebase

---

## Milestone 7 — Mission Control Dashboard 🔧

**README claim:** *"Track real-time run status, browse generated artifacts, monitor intervention requests, and audit full execution histories."*

### What's shipped
- Task dashboard with queue view, dark mode, Tailwind styling
- Real-time SSE for live task status (spec 023)
- Temporal artifact presentation (spec 063)
- Task editing, cancellation, resubmission
- Runtime selector on submit (with runtime column in task list — #793)
- Task detail fields: Runtime, Model, Effort
- Fast cancellation UX (TRY_CANCEL + force-terminate)
- External runs integrated into main task dashboard

### Remaining tasks
- [ ] **7.1** Migrate settings page to Mission Control — Settings currently in separate profile page, should be unified
- [ ] **7.2** Artifact browsing UI (files/logs/patches) — API exists (`temporal_artifacts.py`), dashboard integration partial
- [ ] **7.3** Intervention request monitoring — Not implemented
- [ ] **7.4** Execution history / audit trail view — Spec 067 (`run-history-rerun`), API exists, UI incomplete
- [ ] **7.5** Side-by-side comparison view — README promises "run the same task with different models and runtimes to compare results"
- [ ] **7.6** Multi-step / step DAG visualization — Steps are tracked but no graphical visualization
- [ ] **7.7** Worker fleet health dashboard — No per-worker health view

---

## Milestone 8 — Universal Integration (MCP & APIs) 🔧

**README claim:** *"Connect any agent through MCP or standard API endpoints."*

### What's shipped
- MCP server endpoint (`/context` — `context_protocol.py`)
- MCP tools wrapper (`mcp_tools.py`)
- OpenAI-compatible chat API (`chat.py`)
- Codex MCP tools adapter doc

### Remaining tasks
- [ ] **8.1** MCP Streamable HTTP Transport (2025 spec) — Current `/context` is REST-style; modern MCP uses streamable HTTP
- [ ] **8.2** MCP resource & tool discovery — Clients can't discover what MoonMind offers via MCP
- [ ] **8.3** Webhook / callback API for external agents — Jules external events started, no generic webhook receiver
- [ ] **8.4** OpenAI Responses API compatibility — Only Chat Completions format supported

---

## Milestone 9 — Sandboxed Execution & Security 🔧

**README claim:** *"Runtimes run behind a Docker socket proxy with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs."*

### What's shipped
- Docker-socket-proxy (`tecnativa/docker-socket-proxy`) with restricted API endpoints
- Agent workspace volume isolation
- Auth-volume separation per runtime

### Remaining tasks
- [ ] **9.1** File allowlist enforcement — Promised in README, no implementation found
- [ ] **9.2** Credential sanitization from logs — Agent rules prohibit secrets in output; no runtime log scrubber
- [ ] **9.3** Per-runtime capability routing policy — Proxy limits Docker API endpoints, but not per-runtime policies
- [ ] **9.4** Network egress policies for sandboxes — No outbound network restrictions on worker containers

---

## Milestone 10 — Vendor Portability & Model Flexibility 🔧

**README claim:** *"Swap between proprietary cloud models and local open-source models with a single configuration change."* / *"Multi-Agent Chaining: Break massive goals into smaller steps. Only use expensive models for steps that need them."*

### What's shipped
- Provider SDK clients (OpenAI, Google, Anthropic, Ollama)
- Optional Ollama and vLLM compose profiles
- Runtime selector per task submission
- Model routing in chat endpoint

### Remaining tasks
- [ ] **10.1** Per-step model/runtime selection in multi-step flows — Steps don't independently select models
- [ ] **10.2** Cost tracking / billing-aware routing — No cost instrumentation
- [ ] **10.3** Model comparison mode (same task, different models) — README promises this; no implementation
- [ ] **10.4** Artifact/memory portability across model switches — Artifacts are model-agnostic; memory doesn't track model provenance

---

## Milestone 11 — Observability & Continuous Improvement 🔧

**README claim (Constitution X):** *"Every run MUST end with a structured outcome summary"* / *"The system SHOULD capture improvement signals and route them into a reviewable backlog."*

### What's shipped
- Temporal visibility / execution queries (spec 064)
- Structured workflow run states in Postgres
- Task finish summary system (spec 079)

### Remaining tasks
- [ ] **11.1** Structured outcome summaries on every run — Spec 079 started; not fully wired
- [ ] **11.2** Improvement signal capture (retries, loops, flaky tests) — Constitution X mandates this
- [ ] **11.3** Reviewable improvement backlog / proposals queue — Task proposals exist; not fed by telemetry
- [ ] **11.4** Metrics / dashboards (run duration, success rate, cost) — No operational metrics endpoint
- [ ] **11.5** Structured logging enrichment (run IDs, worker IDs) — structlog in use; inconsistent enrichment

---

## Housekeeping — Codebase Cleanup 🔧

These are technical debt items that don't map to README claims but improve code quality and maintainability.

### Remaining tasks
- [ ] **H.1** Complete legacy system removal — Migration exists (`c1d2e3f4a5b6`); requirements and guard tests in `specs/087-orchestrator-removal/` and `tests/unit/orchestrator_removal/`. Remaining queue/dashboard phases tracked in `docs/tmp/SingleSubstrateMigration.md`.
- [ ] **H.2** Spec deduplication — Merge duplicate specs identified in `docs/tmp/SpecMergeReview.md`: Worker Pause (038/040), Claude gating (044/046), Manifest Phase 0 (032/034), Jules events (048 stub → delete), Task Presets (026/028)
- [ ] **H.3** Legacy skill dispatch cleanup — Remove dead `tool.type == "skill"` branch in `run.py`; all current plan generators emit `agent_runtime` nodes. See `docs/tmp/skill-system-alignment.md`.
- [ ] **H.4** Delete legacy docs identified in `docs/LegacyDocsReview.md` — 6 docs flagged for deletion (`CodexCliWorkers.md`, `GeminiCliWorkers.md`, `SpecKitAutomation.md`, etc.)

---

## Summary: Priority Ordering

The milestones below are ordered by **impact on delivering the README promise** (highest first):

| Priority | Milestone | Current Status | Remaining |
|----------|-----------|----------------|-----------|
| 🔴 P0 | **5 — RAG & Document Retrieval** | 🔧 Partial | 6 items |
| 🔴 P0 | **6 — Memory & Procedural Learning** | 📐 Designed only | 6 items |
| 🔴 P0 | **3 — Multi-Step Planning & Context** | 🔧 Partial | 6 items |
| 🟠 P1 | **4 — Resiliency (stuck detection, intervention)** | 🔧 Partial | 4 items |
| 🟠 P1 | **7 — Mission Control Dashboard** | 🔧 Partial | 7 items |
| 🟠 P1 | **2 — External Agent Coordination** | 🔧 Partial | 2 items |
| 🟡 P2 | **9 — Sandboxed Execution & Security** | 🔧 Partial | 4 items |
| 🟡 P2 | **11 — Observability & Improvement** | 🔧 Partial | 5 items |
| 🟡 P2 | **8 — Universal Integration (MCP)** | 🔧 Partial | 4 items |
| 🟢 P3 | **10 — Vendor Portability & Model Flexibility** | 🔧 Partial | 4 items |
| 🟢 P3 | **1 — Managed Agent Runtimes** | ✅ Shipped | 2 items |
| 🟢 P3 | **H — Housekeeping / Cleanup** | 🔧 Partial | 4 items |

---

## `docs/tmp/` File Dispositions

| File | Disposition | Rationale |
|------|-------------|-----------|
| `Roadmap.md` | **Keep** | This file — the living roadmap |
| `CancellationAnalysis.md` | **Delete** | Recommendations shipped (TRY_CANCEL in all activities, force-terminate path). Analysis preserved in Milestone 4 entries. |
| `OrchestratorRemovalPlan.md` | **Deleted** | Superseded by spec 087, in-repo removal work, and `SingleSubstrateMigration.md`. |
| `RagDocUpdates.md` | **Delete** | Marked "Status: Complete". Spec merge plan fully executed (spec 088 shipped). |
| `SpecMergeReview.md` | **Keep until H.2 complete** | Actionable merge candidates not yet resolved. Tracked as H.2 in Housekeeping. |
| `skill-system-alignment.md` | **Keep until H.3 complete** | Legacy skill branch still present in `run.py`. Tracked as H.3 in Housekeeping. |

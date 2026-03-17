# 🌙 MoonMind Roadmap

> Tracking the major milestones remaining to fully deliver on the README promise.
>
> Last updated: 2026-03-17

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
- Codex, Gemini, and Claude Temporal activity workers (`codex-worker`, `gemini-worker`, `claude-worker`)
- OAuth auth-volume bootstrap for Codex and Gemini (`tools/auth-*.sh`)
- Docker-socket-proxy sandboxing for all workers
- Runtime adapter pattern (`moonmind/agents/`, `moonmind/workflows/temporal/runtime/`)

### Remaining tasks
- [x] **1.1** Claude auth volume flow matches Codex/Gemini parity — Spec 072 (`managed-agents-auth`)
- [x] **1.2** Auth profile management UI in Mission Control — API router (`auth_profiles.py`) + dashboard
- [x] **1.3** Worker health checks & readiness probes — Constitution IX
- [ ] **1.4** Graceful worker pause / unpause — Spec 038/040 (`worker-pause`), API exists, dashboard wiring needed

---

## Milestone 2 — External / Black-Box Agent Coordination 🔧

**README claim:** *"Even cloud-hosted agents like Jules and Codex Cloud benefit from coordination. MoonMind tracks status, injects context, and responds with feedback."*

### What's shipped
- Jules Temporal external-event contract and adapter (`moonmind/jules/`, `docs/ExternalAgents/`)
- External-agent integration system design doc

### Remaining tasks
- [ ] **2.1** Jules end-to-end external event workflow — Spec 048/066, adapter exists, event wiring incomplete
- [ ] **2.2** Codex Cloud integration adapter — No adapter yet for the hosted Codex product
- [ ] **2.3** Generic external-agent adapter pattern — Designed in `ExternalAgentIntegrationSystem.md`, not generalized in code
- [ ] **2.4** Status tracking dashboard for external runs — Mission Control only shows managed runs currently

---

## Milestone 3 — Multi-Step Planning & Step-Based Context 🔧

**README claim:** *"Break a massive goal into discrete steps with presets, and let MoonMind schedule and sequence them."* / *"Inject the right context into each step and clear it between steps."*

### What's shipped
- Task presets system (spec 026/028/055 — `TaskPresetsSystem.md`)
- Task step templates & step sequencing API (`task_step_templates.py`, `TasksStepSystem.md`)
- Manifest-based task submission (`manifest.schema.json`, `moonmind/manifest/`)
- Task proposal queue for automated step generation

### Remaining tasks
- [ ] **3.1** Automatic context injection per step — Context pack exists (`rag/context_pack.py`), not wired into step execution
- [ ] **3.2** Context clearing between steps — No implementation; promised in README
- [ ] **3.3** Multi-step workflow visualization in Mission Control — Dashboard shows tasks but not step DAGs
- [ ] **3.4** Preset-driven scheduling (auto-sequence from goal) — Presets exist but goal-to-plan decomposition is manual

---

## Milestone 4 — Fire-and-Forget Resiliency 🔧

**README claim:** *"Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Backed by Temporal, workflows survive container crashes and restarts. Automatic stuck detection and smart retries."*

### What's shipped
- Temporal foundation (server, DB, namespace init)
- Durable workflows for agent runs (`agent_run.py`, `run.py`)
- Worker crash recovery via Temporal replay
- Recurring task schedules (spec 049)

### Remaining tasks
- [ ] **4.1** Automatic stuck-detection for agent runs — Spec 039 (`worker-self-heal`), partial design
- [ ] **4.2** Smart retry policies per runtime — Temporal retries exist but not tuned per agent type
- [ ] **4.3** Intervention request signaling (agent asks for human help) — README promises "monitor intervention requests"
- [ ] **4.4** Notification system (email/webhook on completion) — No notification channel for fire-and-forget results

---

## Milestone 5 — Memory & Procedural Learning 📐

**README claim:** *"Procedural memory retains structured summaries from past runs so agents don't repeat the same mistakes."* / *"Ground agents with built-in loaders for GitHub, Jira, Confluence, Google Drive, and local files."*

### What's shipped
- Document RAG: LlamaIndex + Qdrant (`moonmind/rag/`)
- Data loaders / indexers: GitHub, Jira, Confluence, Google Drive, local files (`moonmind/indexers/`)
- Context pack primitives (`rag/context_pack.py`)
- Memory architecture design doc (`docs/Memory/MemoryArchitecture.md` — "Desired State")

### Remaining tasks
- [ ] **5.1** Run Digests (Plane B — task history summaries) — Architecture defined, no implementation
- [ ] **5.2** Fix Patterns / Error Signatures (procedural memory) — Architecture defined, no implementation
- [ ] **5.3** Long-Term Memory integration (Mem0 / Plane C) — Architecture defined, no integration
- [ ] **5.4** Planning Memory (Beads / Plane A) — Architecture defined, no integration
- [ ] **5.5** Context pack assembly wired into agent runs — Primitives exist; not integrated into Temporal activity execution
- [ ] **5.6** Token budgeting & provenance tracking — Designed in memory arch, not implemented
- [ ] **5.7** Memory feature flags (`MEMORY_ENABLED`, etc.) — Defined in spec, not in codebase

---

## Milestone 6 — Mission Control Dashboard 🔧

**README claim:** *"Track real-time run status, browse generated artifacts, monitor intervention requests, and audit full execution histories."*

### What's shipped
- Task dashboard with queue view, dark mode, Tailwind styling
- Real-time SSE for live task status (spec 023)
- Temporal artifact presentation (spec 063)
- Task editing, cancellation, resubmission
- Runtime selector on submit

### Remaining tasks
- [ ] **6.1** Artifact browsing UI (files/logs/patches) — API exists (`temporal_artifacts.py`), dashboard integration partial
- [ ] **6.2** Intervention request monitoring — Not implemented
- [ ] **6.3** Execution history / audit trail view — Spec 067 (`run-history-rerun`), API exists, UI incomplete
- [ ] **6.4** Side-by-side comparison view — README promises "run the same task with different models and runtimes to compare results"
- [ ] **6.5** Multi-step / step DAG visualization — Steps are tracked but no graphical visualization
- [ ] **6.6** Worker fleet health dashboard — No per-worker health view

---

## Milestone 7 — Universal Integration (MCP & APIs) 🔧

**README claim:** *"Connect any agent through MCP or standard API endpoints."*

### What's shipped
- MCP server endpoint (`/context` — `context_protocol.py`)
- MCP tools wrapper (`mcp_tools.py`)
- OpenAI-compatible chat API (`chat.py`)
- Codex MCP tools adapter doc

### Remaining tasks
- [ ] **7.1** MCP Streamable HTTP Transport (2025 spec) — Current `/context` is REST-style; modern MCP uses streamable HTTP
- [ ] **7.2** MCP resource & tool discovery — Clients can't discover what MoonMind offers via MCP
- [ ] **7.3** Webhook / callback API for external agents — Jules external events started, no generic webhook receiver
- [ ] **7.4** OpenAI Responses API compatibility — Only Chat Completions format supported

---

## Milestone 8 — Sandboxed Execution & Security 🔧

**README claim:** *"Runtimes run behind a Docker socket proxy with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs."*

### What's shipped
- Docker-socket-proxy (`tecnativa/docker-socket-proxy`) with restricted API endpoints
- Agent workspace volume isolation
- Auth-volume separation per runtime

### Remaining tasks
- [ ] **8.1** File allowlist enforcement — Promised in README, no implementation found
- [ ] **8.2** Credential sanitization from logs — Agent rules prohibit secrets in output; no runtime log scrubber
- [ ] **8.3** Per-runtime capability routing policy — Proxy limits Docker API endpoints, but not per-runtime policies
- [ ] **8.4** Network egress policies for sandboxes — No outbound network restrictions on worker containers

---

## Milestone 9 — Vendor Portability & Model Flexibility 🔧

**README claim:** *"Swap between proprietary cloud models and local open-source models with a single configuration change."* / *"Multi-Agent Chaining: Break massive goals into smaller steps. Only use expensive models for steps that need them."*

### What's shipped
- Provider SDK clients (OpenAI, Google, Anthropic, Ollama)
- Optional Ollama and vLLM compose profiles
- Runtime selector per task submission
- Model routing in chat endpoint

### Remaining tasks
- [ ] **9.1** Per-step model/runtime selection in multi-step flows — Steps don't independently select models
- [ ] **9.2** Cost tracking / billing-aware routing — No cost instrumentation
- [ ] **9.3** Model comparison mode (same task, different models) — README promises this; no implementation
- [ ] **9.4** Artifact/memory portability across model switches — Artifacts are model-agnostic; memory doesn't track model provenance

---

## Milestone 10 — Observability & Continuous Improvement 🔧

**README claim (Constitution X):** *"Every run MUST end with a structured outcome summary"* / *"The system SHOULD capture improvement signals and route them into a reviewable backlog."*

### What's shipped
- Temporal visibility / execution queries (spec 064)
- Structured workflow run states in Postgres
- Task finish summary system (spec 079)

### Remaining tasks
- [ ] **10.1** Structured outcome summaries on every run — Spec 079 started; not fully wired
- [ ] **10.2** Improvement signal capture (retries, loops, flaky tests) — Constitution X mandates this
- [ ] **10.3** Reviewable improvement backlog / proposals queue — Task proposals exist; not fed by telemetry
- [ ] **10.4** Metrics / dashboards (run duration, success rate, cost) — No operational metrics endpoint
- [ ] **10.5** Structured logging enrichment (run IDs, worker IDs) — structlog in use; inconsistent enrichment

---

## Summary: Priority Ordering

The milestones below are ordered by **impact on delivering the README promise** (highest first):

| Priority | Milestone | Current Status | Remaining |
|----------|-----------|----------------|-----------|
| 🔴 P0 | **5 — Memory & Procedural Learning** | 📐 Designed only | 7 items |
| 🔴 P0 | **3 — Multi-Step Planning & Context** | 🔧 Partial | 4 items |
| 🟠 P1 | **4 — Resiliency (stuck detection, intervention)** | 🔧 Partial | 4 items |
| 🟠 P1 | **6 — Mission Control Dashboard** | 🔧 Partial | 6 items |
| 🟠 P1 | **2 — External Agent Coordination** | 🔧 Partial | 4 items |
| 🟡 P2 | **8 — Sandboxed Execution & Security** | 🔧 Partial | 4 items |
| 🟡 P2 | **10 — Observability & Improvement** | 🔧 Partial | 5 items |
| 🟡 P2 | **7 — Universal Integration (MCP)** | 🔧 Partial | 4 items |
| 🟢 P3 | **9 — Vendor Portability & Model Flexibility** | 🔧 Partial | 4 items |
| 🟢 P3 | **1 — Managed Agent Runtimes** | ✅ Shipped | 1 item |

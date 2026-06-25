# 🌙 MoonMind Roadmap

> Tracking the major milestones remaining to fully deliver on the README promise:
> **safety, resiliency, and observability for Claude Code and Codex CLI.**
>
> Last updated: 2026-06-20

---

## How to read this document

Each milestone maps to a specific claim in the [README](../README.md). Status uses:

| Tag | Meaning |
|-----|---------|
| ✅ Shipped | Functional and in the main branch |
| 🔧 Partial | Core exists but key pieces are missing |
| 📐 Designed | Architecture/spec exists, implementation not started |
| ⬜ Not Started | No implementation or spec yet |

Remaining items within each milestone are numbered **M.N** (milestone.item) and tracked with checkboxes:
- `[x]` = done
- `[ ]` = still remaining

The README is framed around three headline value propositions — **Safety** (Milestones 9, 12), **Resiliency** (Milestones 4, 13), and **Observability** (Milestones 11, 14) — layered on top of managed agent runtimes (Milestone 1). This update reconciles the roadmap with the current README, adopted docs, and main-branch work through PR #2543.

### Current snapshot

MoonMind is now a usable local control plane with Codex CLI as the first live workflow-scoped managed-session runtime, Mission Control as the operator UI, Temporal-backed durable workflows, artifact-first logs/evidence, provider profiles, RAG/memory foundations, and conservative Docker workload boundaries.

The highest-impact remaining gaps against the README promise are:

1. **Claude Code parity on the shared managed-session plane** — Claude contracts exist, but Claude does not yet enter the live `MoonMind.AgentSession` controller.
2. **Evidence-gated recovery as the default operator path** — step execution evidence and checkpoint primitives are present, but checkpoint resume is not yet the primary failed-run flow.
3. **Safety governance rollout** — outbound scanning, approved-scope PentestGPT, and SecretRef integration exist, but per-run policy enforcement, governance telemetry, risky-action review, and complete publish-boundary coverage remain.
4. **Deep observability rollout** — structured logs, live-log transport, session-aware viewer pieces, and cost primitives exist, but end-to-end OTel, per-step cost, and complete trace/log deep links are still pending.

Recent main-branch changes since the prior roadmap snapshot:
- MM-831 / PR #2541 completed the expanded Step Execution history UI in Mission Control.
- MM-839 / PR #2538 and MM-845 / PR #2539 hardened the curated `security.pentest.run` workload, added production rollout gates, and made discovery default-on while preserving conservative execution policy.
- PR #2531 surfaces workflow priority through API schemas and workflow detail.
- PR #2528 made “PR with Merge Automation” the single operator-facing publish mode.
- PR #2523 and PR #2524 hardened runtime/profile updates and managed-session resume status after session resets.
- MM-850–MM-852 / PRs #2537, #2540, and #2543 reduced generated-contract CI cost and moved heavyweight imports behind route/service boundaries.

---

## Milestone 1 — Managed Agent Runtimes & the Shared Managed-Session Plane 🔧

**README claim:** *"MoonMind runs owned CLI runtimes on your own infrastructure using your existing subscriptions or API keys. Codex CLI is the live first-class workflow-scoped managed-session runtime; Claude Code is a first-class managed-runtime target … on the path to the same live session controller."*

**Framing:** This milestone owns **one shared, runtime-neutral managed-session plane**. Codex CLI is the live binding today. Claude Code reaches parity by entering the same `MoonMind.AgentSession` controller through adapter seams, never by creating a parallel session architecture. Runtime parity must consume Milestone 12 safety contracts, emit Milestone 14 observability evidence, and preserve Milestone 13-compatible checkpoint/resume evidence.

### What's shipped
- Codex, Gemini, and Claude Temporal activity workers (`codex-worker`, `gemini-worker`, `claude-worker`).
- Runtime adapter pattern (`moonmind/agents/`, `moonmind/workflows/temporal/runtime/`).
- Codex CLI workflow-scoped managed sessions with per-session sidecar Docker daemon.
- Provider profile management UI in Mission Control; auto-seeding of default provider profiles on startup.
- Worker health checks and readiness probes; per-worker shard health view (MM-775).
- Graceful worker pause/unpause API and Settings Operations surface.
- MoonMind-native xterm.js OAuth terminal for provider login (spec 306), superseding the older Tmate-based design.
- OAuth runner bootstrap over PTY with session guardrails (specs 192, 245).
- Claude managed-session **domain contracts** — session records, surfaces, turns, decisions, policy envelopes, context snapshots, checkpoints, telemetry, and governance evidence — with boundary tests.
- Codex managed-session resilience fixes for stale provider profiles and session-epoch mismatch after clear/reset (#2523, #2524).

### Remaining work
- [x] **1.1–1.6** Auth parity, profile UI, health checks, API key gate removal, auto-seeding.
- [x] **1.7** Graceful worker pause/unpause.
- [x] **1.8** Universal OAuth sessions via the native xterm.js OAuth terminal.
- [ ] **1.9** Claude Code parity on the shared managed-session plane — Claude domain contracts exist (`docs/ManagedAgents/ClaudeCodeManagedSessions.md`) but Claude does not yet enter the live `MoonMind.AgentSession` controller.
  - [ ] **1.9a** Runtime-neutral managed-session contracts — neutral `ManagedSessionBinding`, runtime family/id, protocol, locator, control request, and artifact-publication types; preserve Codex replay compatibility while making `claude_code` representable without masquerading as Codex.
  - [ ] **1.9b** Claude launch/transport adapter MVP — Claude enters `MoonMind.AgentSession` through the adapter, starting with the smallest live shape (`local_process`).
  - [ ] **1.9c** Launch-time policy-envelope enforcement — compile and enforce a versioned Claude `PolicyEnvelope` before launch/control actions; fail closed and record governance evidence on denial.
  - [ ] **1.9d** Normalized turn/control lifecycle parity — start/resume/send/steer/interrupt/clear/cancel/terminate with explicit epochs and reset boundaries.
  - [ ] **1.9e** Checkpoint/resume/fork MVP — map Claude checkpoint metadata to MoonMind checkpoint refs and expose resume-from-last-known-good.
  - [ ] **1.9f** Observability parity minimum — session/turn lifecycle, decisions, checkpoints, context compaction, failures, and correlation IDs.
  - [ ] **1.9g** Cross-runtime managed-session conformance suite covering Codex and Claude adapters.

---

## Milestone 3 — Multi-Step Planning & Step-Based Context 🔧

**README claim:** *"Agents perform better on small, focused tasks. MoonMind injects the right context into each step and clears it between steps to prevent context-window pollution."*

### What's shipped
- Workflow presets, step templates, and step sequencing (`docs/Workflows/WorkflowPresetsSystem.md`, `WorkflowStepSystem.md`).
- Manifest-based workflow submission and workflow proposal queue with `proposal_generate`.
- Tracker-native proposal delivery and review — GitHub/Jira delivery records and process-tracker decision handling.
- Context clearing between ordered Codex managed-session steps via workflow-scoped AgentSession reset boundaries.
- Target-aware prepared inputs per step — prepared-input manifests selected at the runtime prompt boundary.
- Preset-driven scheduling — goal-only submissions mapped to seeded presets (MM-747).
- Schema-driven capability inputs and Create-page authoring validation.
- Workflow priority is persisted/surfaced through API and Mission Control detail (#2531).
- PR publishing and merge automation are represented as a single operator-facing publish mode (#2528).

### Remaining work
- [x] **3.1** Tracker-native proposal delivery/review.
- [ ] **3.2** Automatic RAG context injection per step — target-aware prepared file context is wired, but retrieval-backed context packs (`rag/context_pack.py`) are not yet injected into Temporal step execution (pairs with 5.3).
- [x] **3.3** Context clearing between steps.
- [x] **3.4** Multi-step workflow visualization in Mission Control.
- [x] **3.5 / 3.6** Preset-driven scheduling and schedules UI overhaul.

---

## Milestone 4 — Fire-and-Forget Resiliency ✅

**README claim:** *"Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Every run is backed by Temporal, so workflows survive container crashes, worker restarts, and host reboots."*

### What's shipped
- Temporal foundation, durable agent-run workflows, and crash recovery via replay.
- Recurring workflow schedules; fast cancellation (`TRY_CANCEL`) and force-terminate path.
- Runtime-specific resiliency policies, no-progress detection, intervention escalation, completion webhooks, and hardened managed-runtime no-progress handling.
- Provider rate-limit detection in live output with slot-based concurrency and cooldowns.
- Idempotent side effects keyed by deterministic execution tuples `(workflow_id, step_id, attempt)`.
- Managed-session and provider-profile update resilience fixes for in-flight workflow edits and cleared-session resume probes (#2523, #2524).

### Remaining work
- [x] **4.1–4.6** All shipped. Deeper recovery work continues in **Milestone 13 — Self-Healing Remediation & Recovery**.

---

## Milestone 5 — RAG & Document Retrieval 🔧

**README claim (supporting):** *Grounding agents with loaders for GitHub, Jira, Confluence, Google Drive, and local files.*

### What's shipped
- LlamaIndex + Qdrant pipeline; GitHub/Jira/Confluence/Drive/local indexers; manifest schema, CLI, and Temporal ingest workflow.
- Retrieval quality validation with golden smoke dataset (`hitRate@10` / `ndcg@10`).
- Index health monitoring in Mission Control (MM-758) and federated multi-collection retrieval.
- End-to-end manifest ingest integration test (MM-754).
- Retrieval transport separation and retrieval evidence guardrails.

### Remaining work
- [x] **5.1** End-to-end manifest ingest testing.
- [ ] **5.3** Context pack assembly wired into agent runs — primitives exist; not integrated into Temporal step execution (pairs with 3.2).
- [ ] **5.5** Incremental re-indexing — full reindex only; no delta path yet.

---

## Milestone 6 — Memory & Procedural Learning ✅

**README claim:** *"Switch providers without losing what your agents have learned."*

All six items shipped: run digests, fix patterns/error signatures, Mem0 long-term adapter, Beads planning adapter, token budgeting/provenance, and feature flags. See `docs/Memory/MemoryArchitecture.md`.

---

## Milestone 7 — Mission Control Dashboard 🔧

**README claim:** *"Track run status in real time, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI."*

### What's shipped
- Workflow console with live SSE status, workflow editing/cancel/resubmit, runtime/model/effort/priority display, and provider-profile summaries.
- Execution history/audit view (`/workflows/{workflowId}/runs`) and workflow detail subroute tabs.
- Intervention request monitoring via `intervention_requested` state.
- Settings unified into Mission Control with sparse overrides, server-side validation, settings backup, migrations, and MM-713 guardrail suite.
- Attachment upload/binding UX with recovery diagnostics by target; column filter popovers; mobile/accessibility stability.
- Worker fleet health dashboard (MM-775).
- Multi-step step DAG visualization.
- Expanded Step Execution history UI (MM-831 / PR #2541), including execution ordinal, lineage, reason, runtime child refs, context bundle ref, workspace policy, git disposition, gate verdict, output/diff refs, diagnostics refs, side effects, and terminal disposition.

### Remaining work
- [x] **7.1** Settings migrated to Mission Control.
- [ ] **7.2** Artifact browsing UI — API exists (`temporal_artifacts.py`); dashboard browsing of files/logs/patches is still partial.
- [ ] **7.5** Side-by-side comparison view — comparison runs preserve lineage, but no side-by-side UI.
- [x] **7.6** Multi-step / step DAG visualization.
- [ ] **7.7** Remediation panels - Tracked with 13.3.
- [x] **7.8** Expanded Step Execution history surface.

---

## Milestone 8 — Universal Integration (MCP & APIs) 🔧

**README claim (supporting):** *Connect any agent through MCP or standard API endpoints.*

### What's shipped
- `/mcp` Streamable HTTP endpoint (2025 spec) with resource and tool discovery.
- JSON helper routes.
- Webhook/callback API for integration events.
- OpenAI-compatible Chat Completions API.
- OpenAI-compatible Responses API (`/v1/responses`, limited subset) — OpenAI models route through `client.responses.create`; Google and Anthropic models are served through a normalized Chat Completions bridge, with RAG context injection and outbound secret scanning. Covered by unit tests.
- Executable-tool discovery includes schema-driven curated tools such as `security.pentest.run` when deployment policy allows them.

### Remaining work
- [ ] **8.4** Responses API feature parity — the `/v1/responses` route ships a single-turn subset; streaming, tool calls, conversation state (`conversation` / `previous_response_id`), and background mode are explicitly rejected and remain unimplemented.

---

## Milestone 9 — Sandboxed Execution & Security ✅

**README claim:** *"Managed runtime sessions and specialized workloads run in isolated Docker boundaries with strict capability routing. Ordinary sessions get a private sidecar Docker daemon — never the host socket. File allowlists restrict what a run may modify."*

### What's shipped
- Docker-socket-proxy with restricted endpoints for control-plane workloads; per-session sidecar daemon for ordinary managed sessions.
- Per-runtime managed-session Docker capability policy; explicit `no-docker` runtimes cannot inherit proxy access.
- File allowlist enforcement for sandbox command and patch activities.
- Network egress restriction for sandbox workers.
- Workspace mount session-boundary isolation for workload containers.
- Credential sanitization through `redact_sensitive_text` / `SecretRedactor` across runtime, remediation, and publish paths.
- Curated `security.pentest.run` specialized workload path — policy-gated Temporal executable tool using a MoonMind-owned runner image, approved-scope artifacts, provider profiles, report-first artifacts, and terminal cleanup metadata.
- PentestGPT production rollout gates — runner contract checks, self-test report validation, required OCI provenance labels, image-tag drift protection, and vulnerability threshold gate.

### Remaining work
- [x] **9.2** Credential sanitization from logs — runtime redaction shipped; outbound-boundary scanning continues in Milestone 12.
- [x] **9.3 / 9.4** Capability routing policy and sandbox egress.
- [x] **9.5** Curated PentestGPT workload safe default — discovery is on by default, but execution remains approved-scope and policy gated. Restricted-egress pentest networking is tracked under 12.6, not as a generic sandbox gap.

---

## Milestone 10 — Vendor Portability & Model Flexibility ✅

**README claim:** *"Swap between proprietary cloud models and local open-source models with a single configuration change. Use expensive models only for the steps that need them."*

All items shipped: per-step runtime/model/effort selection, cost tracking and billing-aware routing, comparison runs with source lineage, artifact/memory portability provenance, and current-generation default models.

---

## Milestone 11 — Observability & Continuous Improvement ✅

**README claim (Constitution X):** *"Every run MUST end with a structured outcome summary"* / *"The system SHOULD capture improvement signals and route them into a reviewable backlog."*

### What's shipped
- Structured outcome summaries wired into indexed execution projections.
- Improvement signal capture for retry, loop/no-progress, and flaky-test run quality.
- Telemetry fed into the proposal queue as a reviewable improvement backlog.
- Operational execution metrics and structured logging enrichment with run/worker correlation.
- Milestone closed out under MM-791. Deeper tracing, cost attribution, and live-log work continues in **Milestone 14**.

---

## Milestone 12 — Safety Guardrails & Governance 🔧

**README claim:** *"Typed policy envelopes that declare per-run what an agent may touch, governance telemetry that records every privileged action an agent took and why, and a complete audit trail for the secret lifecycle."*

### What's shipped
- High-security outbound scan contract — deterministic scan boundaries with `OutboundScanDecision` / `OutboundFinding` models (MM-811).
- Outbound scan adopted at Jira comment-posting and managed workspace git-push boundaries.
- SecretRef-based settings integration — durable contracts carry secret references, resolved only at launch boundaries.
- Claude OAuth guardrails and bootstrap-PTY session controls.
- GitHub token permission scoping.
- PR publishing gated on MoonSpec verification.
- Deliberately gated exceptional workloads.
- PentestGPT safety posture — approved-scope artifact requirement, conservative default operation modes, Claude OAuth runner profile allowlist, manual approval required for external targets by default, telemetry disabled by default, no arbitrary image/host-mount/raw Docker arguments, and report-first artifacts.
- PentestGPT runner supply-chain gates — provenance labels, tag-drift checks, runner self-test, upstream CLI contract check, and vulnerability threshold gate.

### Remaining work
- [ ] **12.1** Typed per-run policy envelopes — contracts specified, but not yet enforced in the launch path.
  *Done means:* envelopes compiled per run, enforced at launch/control boundaries, violations fail fast, adapter-boundary tests.
- [ ] **12.2** Governance telemetry — durable record of privileged agent actions with export sinks.
  *Done means:* privileged actions recorded with actor/action/target/decision and exportable, with boundary tests.
- [ ] **12.3** Secret lifecycle audit surface — who created/rotated/deleted a secret, which profiles reference it, and which launches resolved it.
  *Done means:* those questions answerable from Mission Control without exposing secret values.
- [ ] **12.4** Outbound scan coverage at all publish boundaries — adopt the MM-811 contract at GitHub PR/issue comments, remaining commit/push paths, artifact publication, and external tool calls under high-security mode.
  *Done means:* every send/post/push/publish boundary invokes the scan in high-security mode, with block-on-match tests per boundary.
- [ ] **12.5** Risk-gated action review policy — classify risky actions before execution and route them through deterministic policy, optional second-model review, or human approval. This must cover generic risky actions plus PentestGPT `full_authorized` and external-target enablement.
  *Done means:* risky actions classified pre-execution; review decision and rationale recorded as governance telemetry (12.2).
- [ ] **12.6** Restricted-egress profile for curated security workloads — `pentestgpt-claude-oauth` currently runs on Docker `bridge` and relies on approved-scope validation, not enforced egress. Add a reviewed network profile, egress proxy, firewall sidecar, or equivalent restricted-egress boundary before external-target operation is considered production-safe.
  *Done means:* operators can enable an approved restricted-egress runner profile whose network boundary is enforced and documented.

---

## Milestone 13 — Self-Healing Remediation & Recovery 🔧

**README claim:** *"Self-healing remediation workflows — a dedicated supervisor can target a failed run, read its durable evidence, and execute typed recovery actions with privilege separation and a full audit trail. The aspiration is a system where a failed run at 3 a.m. is diagnosed, repaired, and resumed before you wake up."*

### What's shipped
- Remediation action contracts and services — typed administrative actions with guard/ledger state.
- Bounded-evidence remediation context — remediation reads a target run's durable evidence under redaction.
- Canonical remediation submissions via `execution_remediation_links`.
- Remediation lifecycle repair prevention — locks/ledger prevent duplicate or conflicting repairs.
- Durable step ledger & checkpoints — step state, attempts, evidence refs, and latest-attempt evidence refs surfaced in the default ledger row.
- Resume foundations — distinct full-retry vs recovery actions, checkpoint-evidence gating, editable full retry, and resume-from-last-failed-step.
- Step Execution evidence manifests and checkpoint contracts are documented as the canonical substrate for semantic re-execution, checkpointed side effects, gated iteration, failed-step recovery, and autonomous story loops.
- Step Execution history is now visible in expanded Mission Control step rows (MM-831 / PR #2541).

### Remaining work
- [ ] **13.1** Resume-from-checkpoint as the default recovery path — checkpoint restore logic exists but is not yet the primary operator flow for failed runs.
  *Done means:* a failed run's default operator flow offers evidence-gated checkpoint resume with replay-safe cutover.
- [ ] **13.2** Queryable remediation audit events — publish remediation lifecycle audit through the control-event mechanism.
  *Done means:* remediation lifecycle events queryable per target run.
- [ ] **13.3** Mission Control remediation panels — operator-facing remediation status/action surfaces.
  *Done means:* operators can view remediation state and trigger typed actions from workflow detail.
- [ ] **13.4** Autonomous remediation supervisor — scheduled/triggered remediation that diagnoses and repairs failed runs without an operator prompt.
  **Gated on:** 12.1, 12.2, 12.3, and 14.1/14.3/14.4. Autonomous repair must not outrun the safety, audit, and post-hoc forensics substrate it depends on.

---

## Milestone 14 — Deep Observability: Tracing, Cost & Live Logs 🔧

**README claim:** *"End-to-end OpenTelemetry tracing from API request through workflow, activity, and provider call — with token and cost attribution per step. Any question about a run — what it changed, what it spent, why it failed — has a durable, queryable answer."*

### What's shipped
- Artifact-first durable run outputs — large content stored as immutable, content-addressed artifacts referenced from compact workflow payloads.
- Live-log spool transport with SSE delivery to Mission Control.
- Structured JSON logs with run/worker correlation context.
- Token cost estimates and pricing-aware routing metadata.
- Live Logs desired-state contract for a session-aware merged timeline, ANSI parsing, virtualized rendering, artifact-backed replay, and rollout gates.
- Mission Control viewer pieces for structured observability history, session snapshots, EventSource live follow, ANSI rendering, and virtualized timelines are present behind feature/config rollout.
- Incident reconstruction path (MM-884): every failed run emits a durable `reports/incident_reconstruction.json` manifest correlating the resilience policy, provider/profile/credential source, sanitized provider failure event, failed step, progress, workspace changes, accepted/blocked side effects, checkpoint restore candidate, cost-attribution settings + observed cost (where available), trace spans across every boundary, logs, and artifacts under one stable, replay-safe correlation (trace) id. The same trace id is stamped onto each step-execution manifest as a compact `traceRef`, and Mission Control/report surfaces link to the durable manifest (`incident_reconstruction_ref`) rather than duplicating its evidence.

### Remaining work
- [ ] **14.1** OpenTelemetry instrumentation — FastAPI middleware, Temporal client/worker interceptors, and activity-layer spans with provider/model/token attributes.
  *Done means:* API→workflow→activity→provider spans correlated end-to-end with bounded metric labels.
  *Substrate shipped (MM-884):* a stable per-run correlation id now propagates through the step manifests and the incident reconstruction trace-span enumeration; native OTel emission/export remains.
- [ ] **14.2** Per-step token/cost attribution in Mission Control — cost primitives exist but are not attributed and displayed per step.
  *Done means:* per-step cost visible in workflow detail and reconcilable with billing/routing estimates.
  *Contract shipped (MM-884):* the incident reconstruction manifest carries the cost-attribution settings and observed per-step token/cost where the runtime reports it; the Mission Control workflow-detail display remains.
- [ ] **14.3** Session-aware live-log timeline rollout — complete the LiveLogs.md rollout: merged stdout/stderr/system/session timeline, session epoch/reset markers, shared cross-process transport as authoritative path, artifact-backed replay, and "live-stream failure never fails run" behavior.
  *Done means:* live timeline plus replay are available for target managed runtimes without relying on SSE success.
- [ ] **14.4** Trace/log deep links from workflow detail — jump from a step in Mission Control to its correlated trace and log slice.
  *Done means:* every step row links to its trace and log slice via correlation IDs.
  *Substrate shipped (MM-884):* each step manifest now carries a `traceRef` (trace id + per-step span id) and the incident manifest links the durable log/artifact refs; the Mission Control deep-link UI remains.

---

## Housekeeping — Codebase Cleanup 🔧

- [x] **H.1–H.4** Legacy system removal, spec deduplication, legacy skill dispatch cleanup, and legacy docs deletion.
- [x] **H.5** Tasks→Workflows doc rename — legacy `Tasks/*` docs renamed to `docs/Workflows/*` with content updated.
- [x] **H.7** Generated-contract CI hygiene — OpenAPI-impact detection and contract jobs are now narrower/cheaper; heavy RAG/chat/document imports are delayed behind API route/service boundaries where safe.
- [ ] **H.6** Release/docs metadata hygiene — `pyproject.toml` (version `0.1.0`, MIT, "MoonMind RAG application…") and `package.json` (version `1.0.0`, ISC, legacy "chat, memory, and automation" description) still disagree with each other and with the README positioning.
  *Done means:* versions, license declarations, and public package descriptions align under one release/versioning policy.

---

## Summary: Priority Ordering

Milestones are ordered by **impact on delivering the README promise** (highest first).

| Priority | Milestone | Current Status | Remaining |
|----------|-----------|----------------|-----------|
| 🔴 P0 | **1 — Shared managed-session plane + Claude parity (1.9a–g)** | 🔧 Partial | 7 sub-items |
| 🔴 P0 | **13 — Operator-driven recovery (13.1–13.3)** | 🔧 Partial | 3 items |
| 🔴 P0 | **12 — Safety Guardrails & Governance** | 🔧 Partial | 6 items |
| 🟠 P1 | **14 — Deep Observability (OTel, cost, live logs)** | 🔧 Partial | 4 items |
| 🟠 P1 (gated) | **13.4 — Autonomous remediation supervisor** | 📐 Designed | Gated on 12.1–12.3, 14.1/14.3/14.4 |
| 🟠 P1 | **3 — Multi-Step Planning & Context** | 🔧 Partial | 1 item |
| 🟠 P1 | **7 — Mission Control Dashboard** | 🔧 Partial | 3 items |
| 🟡 P2 | **5 — RAG & Document Retrieval** | 🔧 Partial | 2 items |
| 🟡 P2 | **8 — Universal Integration (MCP & APIs)** | 🔧 Partial | 1 item |
| 🟡 P2 | **H — Housekeeping (H.6 metadata hygiene)** | 🔧 Partial | 1 item |
| 🟢 Done | **4, 6, 9, 10, 11** | ✅ Shipped | 0 items |

---

## `local-only handoffs` File Dispositions

| File | Disposition | Rationale |
|------|-------------|-----------|
| `Roadmap.md` | **Delete** | Superseded by this file (`docs/MoonMindRoadmap.md`), which is the living roadmap |
| `CancellationAnalysis.md` | **Delete** | Recommendations shipped; analysis preserved in Milestone 4 |
| `OrchestratorRemovalPlan.md` | **Deleted** | Superseded by spec 087 and in-repo removal work |
| `RagDocUpdates.md` | **Delete** | Spec merge plan fully executed (spec 088 shipped) |

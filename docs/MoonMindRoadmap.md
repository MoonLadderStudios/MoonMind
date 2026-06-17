# 🌙 MoonMind Roadmap

> Tracking the major milestones remaining to fully deliver on the README promise:
> **safety, resiliency, and observability for Claude Code and Codex CLI.**
>
> Last updated: 2026-06-16

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

The README was reframed (2026-06-09) around three headline value propositions — **Safety** (Milestones 9, 12), **Resiliency** (Milestones 4, 13), and **Observability** (Milestones 11, 14) — layered on top of managed agent runtimes (Milestone 1). Milestones 12–14 are new and carry the README's aspirational "where this is headed" commitments.

---

## Milestone 1 — Managed Agent Runtimes & the Shared Managed-Session Plane 🔧

**README claim:** *"MoonMind runs owned CLI runtimes on your own infrastructure using your existing subscriptions or API keys. Codex CLI is the live first-class workflow-scoped managed-session runtime; Claude Code is a first-class managed-runtime target … on the path to the same live session controller."*

**Framing:** This milestone owns **one shared, runtime-neutral managed-session plane** — not a control plane per runtime. Codex CLI is the live binding today; Claude Code reaches parity by entering the *same* `MoonMind.AgentSession` controller through adapter seams, never a second session architecture. Parity must be **safe** parity: a runtime is not complete until its launch/control boundaries consume the Milestone 12 safety contracts, emit the minimum Milestone 14 observability evidence, and preserve Milestone 13-compatible checkpoint/resume evidence. New behavior belongs in adapters, not in workflow-specific branches.

### What's shipped
- Codex, Gemini, and Claude Temporal activity workers (`codex-worker`, `gemini-worker`, `claude-worker`)
- Runtime adapter pattern (`moonmind/agents/`, `moonmind/workflows/temporal/runtime/`)
- Codex CLI workflow-scoped managed sessions with per-session sidecar Docker daemon
- Auth profile management UI in Mission Control; auto-seeding of default auth profiles on startup
- Worker health checks & readiness probes; per-worker shard health view (MM-775)
- Graceful worker pause / unpause — API (`system_operations.py`) + Settings Operations surface
- MoonMind-native xterm.js OAuth terminal for provider login (spec 306 `finalize-oauth-terminal`, `frontend/src/entrypoints/oauth-terminal.tsx`) — supersedes the earlier Tmate-based design
- OAuth runner bootstrap over PTY with session guardrails (specs 192, 245)
- Claude managed-session **domain contracts** — session records, surfaces, turns, decisions, policy envelopes, context snapshots, checkpoints, telemetry, and governance evidence, with boundary tests (`moonmind/schemas/managed_session_models.py`; `tests/unit/schemas/test_claude_policy_envelope.py`, `tests/integration/schemas/test_claude_decision_pipeline_boundary.py`). **Modeled, not live:** these are not yet wired into the live workflow-scoped session controller — `canonical_managed_session_runtime_id()` resolves only `codex_cli`, and `AgentExecutionRequest.managed_session` is still typed to the Codex binding.

### Remaining work
- [x] **1.1–1.6** Auth parity, profile UI, health checks, API key gate removal, auto-seeding — all shipped
- [x] **1.7** Graceful worker pause / unpause — API + Settings Operations wiring
- [x] **1.8** Universal OAuth sessions — Delivered as the native xterm.js OAuth terminal (spec 306); Tmate architecture retired
- [ ] **1.9** Claude Code parity on the shared managed-session plane — Claude domain contracts exist (see *What's shipped*; `docs/ManagedAgents/ClaudeCodeManagedSessions.md`) but Claude does not yet enter the live `MoonMind.AgentSession` controller. The work is *runtime-neutralizing the plane and adding Claude through it* — not building a parallel session stack. Top runtime-parity gap against the README headline. Acceptance sub-items:
  - [ ] **1.9a** Runtime-neutral managed-session contracts — introduce neutral `ManagedSessionBinding`, runtime family/id, protocol, locator, control-request, and artifact-publication types (today the session literals resolve to `codex_cli` only and `ManagedSessionBinding` is an alias of the Codex binding). Preserve Codex replay compatibility via patch/version markers; `codex_cli` stays green while `claude_code` is representable without masquerading as Codex; no Claude-specific fields leak into generic workflow code except through adapter-owned metadata.
  - [ ] **1.9b** Claude launch/transport adapter (MVP) — a Claude controller/adapter that enters `MoonMind.AgentSession`. Start with the smallest live shape (local/headless `local_process`); mark remote-control/cloud/team semantics as modeled-not-live until wired. Runtime-specific complexity stays in the adapter, not in `MoonMind.AgentRun`.
  - [ ] **1.9c** Launch-time policy-envelope enforcement — compile a versioned Claude `PolicyEnvelope` before session launch, fail closed where configured, block risky controls that cannot obtain required interactive approval (rather than silently downgrading), and record a policy event + governance decision for every launch/control denial. Envelope tests already cover precedence, fail-closed, risky controls, and visibility metadata; this wires them into the launch path. (Depends on Milestone 12 for the general policy substrate.)
  - [ ] **1.9d** Normalized turn & control lifecycle parity — Claude supports the shared verbs (start/resume/send/steer/interrupt/clear/cancel/terminate) with explicit session epochs and reset boundaries; the workflow sees normalized turns, work items, decisions, and artifact refs, not Claude-native internals. Match the contract the live Codex path wires through the `agent_runtime.*` session-control activities (`launch_session`, `send_turn`, `steer_turn`, `interrupt_turn`, `clear_session`, `terminate_session`, `fetch_session_summary`, `publish_session_artifacts`).
  - [ ] **1.9e** Checkpoint / resume / fork (MVP) — map Claude checkpoint metadata to MoonMind checkpoint refs; expose resume-from-last-known-good as an operator action; keep full transcript/checkpoint payloads pointer-based or runtime-local unless explicitly exported (per the design's non-goal of centrally storing every transcript/diff); represent fork/handoff lineage without overclaiming cloud/remote execution. The code already models checkpoint-capture decisions, checkpoint indexes, and rewind requests — this is live integration, not more modeling.
  - [ ] **1.9f** Observability parity minimum — session/turn lifecycle, decisions, checkpoints, context compaction, and failures emit artifact-first evidence; logs/diagnostics carry workflow/run/session/step correlation IDs; live-log delivery failure never fails the agent run. Per-step token/cost attribution can stay in Milestone 14, but Milestone 1 must emit the IDs/metadata Milestone 14 will consume.
  - [ ] **1.9g** Cross-runtime managed-session conformance suite — one shared suite runs against the Codex *and* Claude adapters, covering launch, clear/reset epoch, interrupt, timeout, no-progress detection, policy fail-closed, redaction, artifact publication, and replay compatibility; Claude cases keep proving no Codex terms (e.g. `threadId`) leak into Claude wire contracts. Promotes the existing Claude boundary tests into an explicit acceptance gate.

---

## Milestone 2 — External / Black-Box Agent Coordination 🔧

**README claim:** *"Cloud-hosted agents like Jules and Codex Cloud are coordinated through external-agent adapters. MoonMind tracks status, injects context, and closes the feedback loop."*

**Framing:** The core external-agent architecture and the Codex Cloud adapter are now in place, so this milestone is **hardening, not new bespoke integrations** — the goal is to make external providers safe, retry-safe, observable, and boring to add. Remains P2.

### What's shipped
- Jules end-to-end external event workflow — adapter, event wiring, multi-step `sendMessage` flow, status polling
- Generic external-agent adapter pattern (MM-741) — shared contract, registry-based provider selection, polling and streaming-gateway execution styles
- Generic integration callback receiver with correlation lookup and polling fallback (MM-779)
- External runs integrated into the main workflow console
- Codex Cloud core external-agent adapter — `CodexCloudAgentAdapter` (canonical start/status/fetch/cancel), runtime gate (`moonmind/codex_cloud/settings.py`), registration in the default registry when the gate is enabled, and four Temporal activities (`integration.codex_cloud.{start,status,fetch_result,cancel}`). Provider status is normalized at the adapter boundary with unknown-status rejection (`normalize_codex_cloud_status` / `raise_unsupported_status`).
- Canonical external contracts hardened — `ProviderCapabilityDescriptor` (callbacks / cancel / result-fetch / poll-hint / execution-style) and `AgentExecutionRequest` validators that reject raw credential keys in `parameters` / `workspaceSpec`.

### Remaining work
- [ ] **2.3** Codex Cloud production-readiness & contract validation — fake-provider/contract-test E2E across start → poll → fetch → cancel; runtime-gate diagnostics visible in Settings/Mission Control; explicit, tested provider→canonical status mapping; bounded unknown-status behavior (reject at the boundary or enter a diagnosed/intervention state after a bounded wait); move API-key config toward SecretRef/profile-backed resolution rather than long-term raw-env reliance.
  *Done means:* a CI-runnable contract test exercises the full lifecycle and the gate's enabled/disabled state is operator-visible.
- [ ] **2.4** External-agent conformance suite — every provider passes the same tests: gate disabled/enabled, start/status/fetch/cancel, polling vs callback, timeout + cancellation cleanup, canonical metadata shape, provider errors mapped to `failureClass`/`providerErrorCode`/diagnostics, and no provider-native top-level payload above the adapter/activity boundary.
- [ ] **2.5** Durable idempotency & correlation — persist `idempotencyKey` and `correlationId` with the external run handle so retries across Temporal activity attempts cannot create duplicate provider jobs where the provider supports idempotency; where it does not, record the limitation and expose a recovery/intervention path; keep callback correlation keys stable and auditable. (Today the base adapter's in-memory cache only guards a single activity attempt — make cross-attempt dedup explicit.)
- [ ] **2.6** Safety at external-agent boundaries — route external prompts, metadata, feedback messages, comments, PR publishing, artifact publication, and merge/push-like actions through the high-security outbound scan where applicable; reject raw credential keys from request `parameters`/`workspaceSpec` (validator shipped — extend coverage); scrub provider errors before they enter logs/artifacts; allow risky external follow-ups to route through governance/review instead of auto-send. (Full outbound-scan rollout is Milestone 12; this names the external boundaries that must adopt it.)
- [ ] **2.7** External-agent observability contract — every external run records provider name, external URL, provider status, normalized status, poll hint, callback support, cancellation semantics, last progress signature, and diagnostics refs; Mission Control shows "cancel unsupported," "awaiting callback," "awaiting feedback," and "intervention requested" truthfully; lifecycle events stay trace/log-correlatable for Milestone 14.
- [ ] **2.8** Simplify workflow-side provider handling — retire the legacy `_coerce_external_start_status()` repair path in `MoonMind.AgentRun` except as a clearly named replay-compatibility shim; route by provider capability, not payload shape; prevent new providers from adding workflow-side parsing branches.
- [ ] **2.9** Provider capability matrix — document, per provider: runtime gate, execution style, callbacks, cancel support, result-fetch support, expected terminal statuses, idempotency support, and known limitations (backed by `ProviderCapabilityDescriptor`).

---

## Milestone 3 — Multi-Step Planning & Step-Based Context 🔧

**README claim:** *"Agents perform better on small, focused tasks. MoonMind injects the right context into each step and clears it between steps to prevent context-window pollution."*

### What's shipped
- Workflow presets, step templates, and step sequencing (`docs/Workflows/WorkflowPresetsSystem.md`, `WorkflowStepSystem.md`)
- Manifest-based workflow submission; workflow proposal queue with `proposal_generate`
- Tracker-native proposal delivery and review — GitHub/Jira delivery records and process-tracker decision handling (specs 312, 313, 357)
- Context clearing between ordered Codex managed-session steps via the workflow-scoped AgentSession reset boundary (MM-745)
- Target-aware prepared inputs per step — prepared-input manifests selected per step at the runtime prompt boundary (specs 325, 349; `moonmind/workflows/executions/prepared_context.py`)
- Preset-driven scheduling — goal-only submissions deterministically mapped to seeded presets (MM-747)
- Schema-driven capability inputs and Create-page authoring validation (specs 308, 340)

### Remaining work
- [x] **3.1** Tracker-native proposal delivery/review — specs 312/313 shipped; `/proposals` remains admin/recovery coverage
- [ ] **3.2** Automatic RAG context injection per step — Target-aware *prepared file* context is wired (specs 325/349), but retrieval-backed context packs (`rag/context_pack.py`) are still not injected into step execution (tracked with 5.3)
- [x] **3.3** Context clearing between steps — MM-745
- [x] **3.4** Multi-step workflow visualization in Mission Control — Workflow detail Steps renders the step ledger as a visible dependency DAG with explicit edge labels (MM-746)
- [x] **3.5 / 3.6** Preset-driven scheduling; schedules UI overhaul — shipped

---

## Milestone 4 — Fire-and-Forget Resiliency ✅

**README claim:** *"Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Every run is backed by Temporal, so workflows survive container crashes, worker restarts, and host reboots."*

### What's shipped
- Temporal foundation, durable agent-run workflows, crash recovery via replay
- Recurring workflow schedules; fast cancellation (`TRY_CANCEL`) and force-terminate path
- Runtime-specific resiliency policies, generic no-progress detection, intervention escalation, completion webhooks (MM-749); hardened managed-runtime no-progress handling (#2389)
- Provider rate-limit detection in live output with slot-based concurrency and cooldowns (`max_parallel_runs`, `cooldown_after_429_seconds`)
- Idempotent side effects keyed by deterministic execution tuples `(workflow_id, step_id, attempt)`

### Remaining work
- [x] **4.1–4.6** All shipped. Deeper recovery work continues in **Milestone 13 — Self-Healing Remediation & Recovery**.

---

## Milestone 5 — RAG & Document Retrieval 🔧

**README claim (supporting):** *Grounding agents with loaders for GitHub, Jira, Confluence, Google Drive, and local files.*

### What's shipped
- LlamaIndex + Qdrant pipeline; GitHub/Jira/Confluence/Drive/local indexers; manifest schema, CLI, and Temporal ingest workflow
- Retrieval quality validation — golden smoke dataset with `hitRate@10` / `ndcg@10` baselines
- Index health monitoring in Mission Control (MM-758); federated multi-collection retrieval
- End-to-end manifest ingest integration test (`tests/integration/temporal/test_manifest_pipeline_e2e.py`, MM-754)
- Retrieval transport separation and retrieval evidence guardrails (specs 256, 257)

### Remaining work
- [x] **5.1** End-to-end manifest ingest testing — MM-754
- [ ] **5.3** Context pack assembly wired into agent runs — Primitives exist; not integrated into Temporal step execution (pairs with 3.2)
- [ ] **5.5** Incremental re-indexing — Full reindex only; no delta path

---

## Milestone 6 — Memory & Procedural Learning ✅

**README claim:** *"Switch providers without losing what your agents have learned."*

All six items (run digests, fix patterns/error signatures, Mem0 long-term adapter, Beads planning adapter, token budgeting/provenance, feature flags) shipped under MM-761/MM-762. See `docs/Memory/MemoryArchitecture.md`.

---

## Milestone 7 — Mission Control Dashboard 🔧

**README claim:** *"Track run status in real time, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI."*

### What's shipped
- Workflow console with live SSE status, workflow editing/cancel/resubmit, runtime/model/effort selection
- Execution history / audit view (`/workflows/{workflowId}/runs`, MM-772); workflow detail subroute tabs (MM-801)
- Intervention request monitoring via `intervention_requested` state
- Settings unified into Mission Control — sparse overrides, server-side validation, settings backup & migration services, MM-713 guardrail suite (specs 339, 341, 358, 359; `api_service/services/settings_backup.py`, `settings_migrations.py`)
- Attachment upload/binding UX with recovery diagnostics by target (specs 321, 329); column filter popovers; mobile/accessibility stability (specs 301, 304)
- Worker fleet health dashboard (MM-775)

### Remaining work
- [x] **7.1** Settings migrated to Mission Control — specs 339/341/358/359
- [ ] **7.2** Artifact browsing UI — API exists (`temporal_artifacts.py`); dashboard browsing of files/logs/patches still partial
- [ ] **7.5** Side-by-side comparison view — Comparison runs preserve lineage (MM-773), but no side-by-side UI
- [x] **7.6** Multi-step / step DAG visualization — Mission Control workflow detail Steps renders step nodes and dependency edges from the step ledger (MM-746)
- [ ] **7.7** Remediation panels - Tracked with 13.3

---

## Milestone 8 — Universal Integration (MCP & APIs) 🔧

**README claim (supporting):** *Connect any agent through MCP or standard API endpoints.*

### What's shipped
- `/mcp` Streamable HTTP endpoint (2025 spec, MM-777) with resource & tool discovery; JSON helper routes
- Webhook/callback API for external agents (MM-779); OpenAI-compatible chat API

### Remaining work
- [ ] **8.4** OpenAI Responses API compatibility — Only Chat Completions format supported

---

## Milestone 9 — Sandboxed Execution & Security ✅

**README claim:** *"Managed runtime sessions and specialized workloads run in isolated Docker boundaries with strict capability routing. Ordinary sessions get a private sidecar Docker daemon — never the host socket. File allowlists restrict what a run may modify."*

### What's shipped
- Docker-socket-proxy with restricted endpoints for control-plane workloads; per-session sidecar daemon for ordinary managed sessions
- Per-runtime managed-session Docker capability policy (MM-784); explicit `no-docker` runtimes cannot inherit proxy access
- File allowlist enforcement for sandbox command and patch activities (MM-782)
- Network egress restriction for sandbox workers (MM-785)
- Workspace mount session-boundary isolation for workload containers (spec 251)
- Credential sanitization — `redact_sensitive_text` / `SecretRedactor` applied across runtime, remediation, and publish paths

### Remaining work
- [x] **9.2** Credential sanitization from logs — runtime redaction shipped; outbound-boundary scanning continues in Milestone 12
- [x] **9.3 / 9.4** Capability routing policy; sandbox egress — shipped

---

## Milestone 10 — Vendor Portability & Model Flexibility ✅

**README claim:** *"Swap between proprietary cloud models and local open-source models with a single configuration change. Use expensive models only for the steps that need them."*

All items shipped: per-step runtime/model/effort selection (MM-786/787), cost tracking and billing-aware routing (MM-788), comparison runs with source lineage (MM-773), artifact/memory portability provenance (MM-790). Default models updated to current generation (MM-802).

---

## Milestone 11 — Observability & Continuous Improvement ✅

**README claim (Constitution X):** *"Every run MUST end with a structured outcome summary"* / *"The system SHOULD capture improvement signals and route them into a reviewable backlog."*

### What's shipped
- Structured outcome summaries wired into indexed execution projections (MM-792)
- Improvement signal capture for retry, loop/no-progress, and flaky-test run quality (MM-793)
- Telemetry fed into the proposal queue as a reviewable improvement backlog (MM-794)
- Operational execution metrics (MM-795); structured logging enrichment with run/worker correlation (MM-796)
- Milestone closed out under MM-791. Deeper tracing, cost attribution, and live-log work continues in **Milestone 14**.

---

## Milestone 12 — Safety Guardrails & Governance 🔧 *(new)*

**README claim:** *"Typed policy envelopes that declare per-run what an agent may touch, governance telemetry that records every privileged action an agent took and why, and a complete audit trail for the secret lifecycle."*

### What's shipped
- High-security outbound scan contract — deterministic scan boundaries with `OutboundScanDecision` / `OutboundFinding` models (MM-811, `moonmind/security/outbound_scan.py`); per-caller adoption is follow-up scope under 12.4
- Outbound scan adopted at the Jira comment-posting boundary (MM-812, `moonmind/integrations/jira/tool.py`) — GitHub comment boundaries were *not* changed in MM-812
- Outbound scan adopted at the managed workspace git-push boundary (MM-813, `moonmind/workflows/temporal/activity_runtime.py`) — high-security mode scans commit metadata and changed content before MoonMind invokes `git push`
- SecretRef-based settings integration — durable contracts carry secret references, resolved only at launch boundaries (spec `001-secretref-settings-integration`, `docs/Security/SecretsSystem.md`)
- Claude OAuth guardrails and bootstrap-PTY session controls (specs 192, 245)
- GitHub token permission scoping (spec 294)
- PR publishing gated on MoonSpec verification (#2386)
- Deliberately gated exceptional workloads — approved-scope artifact loading and dedicated activity handling keep high-risk workloads behind explicit operator approval

### Remaining work
- [ ] **12.1** Typed per-run policy envelopes — Compact runtime contracts declaring what a run may touch (spec 185 `claude-policy-envelope`); contracts specified, not yet enforced in the launch path.
  *Done means:* envelopes compiled per run, enforced at launch/control boundaries, violations fail fast, adapter-boundary tests.
- [ ] **12.2** Governance telemetry — Durable record of privileged agent actions with export sinks (spec 191 `claude-governance-telemetry`); spec-only.
  *Done means:* privileged actions recorded with actor/action/target/decision and exportable, with boundary tests.
- [ ] **12.3** Secret lifecycle audit surface — Who created/rotated/deleted a secret, which profiles reference it, which launches resolved it (`docs/Security/SecretsSystem.md` §13); contract defined, operator surface missing.
  *Done means:* those questions answerable from Mission Control without exposing secret values.
- [ ] **12.4** Outbound scan coverage at all publish boundaries — Adopt the MM-811 contract at GitHub PR/issue comments, remaining commit/push paths, artifact publication, and external tool calls under high-security mode (Jira comments and managed workspace git pushes are adopted).
  *Done means:* every send/post/push/publish boundary invokes the scan in high-security mode, with block-on-match tests per boundary.
- [ ] **12.5** Risk-gated action review policy — Classify risky actions before execution and route them through deterministic policy, optional second-model review, or human approval. The current step-review path is a non-blocking placeholder (`moonmind/workflows/temporal/activities/step_review.py`).
  *Done means:* risky actions classified pre-execution; the review decision and its rationale recorded as governance telemetry (12.2).

---

## Milestone 13 — Self-Healing Remediation & Recovery 🔧 *(new)*

**README claim:** *"Self-healing remediation workflows — a dedicated supervisor can target a failed run, read its durable evidence, and execute typed recovery actions with privilege separation and a full audit trail. The aspiration is a system where a failed run at 3 a.m. is diagnosed, repaired, and resumed before you wake up."*

### What's shipped
- Remediation action contracts and services — typed administrative actions with guard/ledger state (spec 320; `moonmind/workflows/temporal/remediation_actions.py`, `remediation_context.py`, `remediation_tools.py`)
- Bounded-evidence remediation context — remediation reads a target run's durable evidence under redaction (spec 232); live remediation runs are merging real fixes (#2346, #2347)
- Canonical remediation submissions via `execution_remediation_links` (specs 226, 317)
- Remediation lifecycle repair prevention — locks/ledger prevent duplicate or conflicting repairs (spec 322)
- Durable step ledger & checkpoints — step state, attempts, and evidence refs persisted as artifacts (`step_ledger.py`, `step_checkpoints.py`; specs 345, 716, `001-step-attempt-manifest`); latest-attempt evidence refs surfaced in the default ledger row (MM-815)
- Resume foundations — distinct full-retry vs. recovery actions (spec 326), checkpoint-evidence gating for resume availability (spec 327), editable full retry (spec 343), resume-from-last-failed-step (spec 310)

### Remaining work
- [ ] **13.1** Resume-from-checkpoint as the default recovery path — checkpoint restore logic exists but is not yet the primary operator flow for failed runs.
  *Done means:* a failed run's default operator flow offers evidence-gated checkpoint resume (spec 327) with replay-safe cutover.
- [ ] **13.2** Queryable remediation audit events — publish remediation lifecycle audit through the control-event mechanism (spec 323).
  *Done means:* remediation lifecycle events queryable per target run.
- [ ] **13.3** Mission Control remediation panels — operator-facing remediation status/action surfaces (spec 324); partial wiring in `workflow-detail.tsx`.
  *Done means:* operators can view remediation state and trigger typed actions from workflow detail.
- [ ] **13.4** Autonomous remediation supervisor — scheduled/triggered remediation that diagnoses and repairs failed runs without an operator prompt (the README's "3 a.m." aspiration); currently remediation runs are operator-initiated.
  **Gated on:** 12.1 (enforced policy envelopes), 12.2 (governance telemetry), 12.3 (secret lifecycle audit), and 14.1/14.3/14.4 (post-hoc forensics). Autonomous repair must not outrun the safety and audit substrate it depends on.

---

## Milestone 14 — Deep Observability: Tracing, Cost & Live Logs 🔧 *(new)*

**README claim:** *"End-to-end OpenTelemetry tracing from API request through workflow, activity, and provider call — with token and cost attribution per step. Any question about a run — what it changed, what it spent, why it failed — has a durable, queryable answer."*

### What's shipped
- Artifact-first durable run outputs — large content stored as immutable, content-addressed artifacts referenced from compact workflow payloads
- Live-log spool transport (`moonmind/observability/transport.py` — `SpoolLogPublisher` / `SpoolLogReader`) with SSE delivery to Mission Control
- Structured JSON logs with run/worker correlation context (MM-796)
- Token cost estimates and pricing-aware routing metadata (MM-788, `moonmind/billing/costs.py`)
- Live logs desired-state contract — session-aware merged timeline, ANSI parsing, virtualized rendering, artifact-backed replay (`docs/Observability/LiveLogs.md`)

### Remaining work
- [ ] **14.1** OpenTelemetry instrumentation — FastAPI middleware, Temporal client/worker interceptors, activity-layer spans with provider/model/token attributes (`docs/Observability/OpenTelemetrySystem.md`); spec complete, no instrumentation code yet.
  *Done means:* API→workflow→activity→provider spans correlated end-to-end with bounded metric labels.
- [ ] **14.2** Per-step token/cost attribution in Mission Control — cost primitives exist (MM-788) but are not attributed and displayed per step.
  *Done means:* per-step cost visible in workflow detail and reconcilable with MM-788 estimates.
- [ ] **14.3** Session-aware live-log timeline viewer — complete the LiveLogs.md rollout: merged stdout/stderr/system/session timeline, session epoch/reset markers, shared cross-process transport as the authoritative path.
  *Done means:* live timeline plus artifact-backed replay; live-stream failure never fails a run.
- [ ] **14.4** Trace/log deep links from workflow detail — jump from a step in Mission Control to its correlated trace and log slice.
  *Done means:* every step row links to its trace and log slice via correlation IDs.

---

## Housekeeping — Codebase Cleanup 🔧

- [x] **H.1–H.4** Legacy system removal, spec deduplication, legacy skill dispatch cleanup, legacy docs deletion — complete (MM-797 through MM-800)
- [x] **H.5** Tasks→Workflows doc rename — legacy `Tasks/*` docs renamed to `docs/Workflows/*` with content updated (#2395)
- [ ] **H.6** Release/docs metadata hygiene — `pyproject.toml` (version `0.1.0`, MIT, "RAG application…" description) and `package.json` (version `1.0.0`, ISC, legacy "chat, memory, and automation" description) disagree with each other and with the README positioning. Align versions, license declarations, and public descriptions under one release/versioning policy.

---

## Summary: Priority Ordering

Milestones ordered by **impact on delivering the README promise** (highest first). The three new milestones carry the README's aspirational safety/resiliency/observability commitments and lead the queue.

| Priority | Milestone | Current Status | Remaining |
|----------|-----------|----------------|-----------|
| 🔴 P0 | **1 — Shared managed-session plane + Claude parity (1.9a–g)** | 🔧 Partial | 7 sub-items |
| 🔴 P0 | **13 — Operator-driven recovery (13.1–13.3)** | 🔧 Partial | 3 items |
| 🔴 P0 | **12 — Safety Guardrails & Governance** | 🔧 Partial | 5 items |
| 🟠 P1 | **14 — Deep Observability (OTel, cost, live logs)** | 🔧 Partial | 4 items |
| 🟠 P1 (gated) | **13.4 — Autonomous remediation supervisor** | 📐 Designed | Gated on 12.1–12.3, 14.1/14.3/14.4 |
| 🟠 P1 | **3 — Multi-Step Planning & Context** | 🔧 Partial | 2 items |
| 🟠 P1 | **7 — Mission Control Dashboard** | 🔧 Partial | 4 items |
| 🟡 P2 | **5 — RAG & Document Retrieval** | 🔧 Partial | 2 items |
| 🟡 P2 | **2 — External-agent hardening (Codex Cloud core shipped)** | 🔧 Partial | 7 items |
| 🟡 P2 | **8 — Universal Integration (MCP)** | 🔧 Partial | 1 item |
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

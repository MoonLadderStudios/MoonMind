# 🌙 MoonMind Roadmap

> Roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**, held to the omnipresent goals of **safety, resilience, and observability**.
>
> The destination is the Omnigent host. Safety, resilience, and observability are the properties every milestone must satisfy on the way there — acceptance lenses applied to all work below, not separate milestones.
>
> **Document class:** this roadmap is an *imperative execution tracker* (milestones, tasks, status). Per `docs/Workflows/MoonSpecDocumentModel.md`, durable desired state lives in the canonical declarative `docs/` files each milestone names in its `X.0` task — not here. When the two disagree, the declarative docs win.
>
> Last updated: 2026-07-11

---

## Direction of travel

MoonMind is shifting from owning separate direct Codex CLI and Claude Code managed-session controllers toward using **Omnigent host** as the runtime boundary for Codex, Claude Code, and future harnesses.

The target split is:

- **MoonMind owns** the dashboard, create/edit flows, Temporal orchestration, workflow/run identity, policy selection, checkpoint/resume/branching, remediation, retrieval, durable artifacts, and operator audit evidence.
- **Omnigent host owns** live harness execution, Codex/Claude process lifecycle inside the host environment, host-side workspace resources, live session events, and harness-specific launch details.
- **The MoonMind Omnigent bridge owns** the compatibility boundary between those systems: session creation/attachment, event streaming, Workflow Detail chat projection, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex/Claude managed-session code remains compatibility substrate** until the bridge and host path can fully replace it. New roadmap work should land through the Omnigent host/bridge path or produce evidence compatible with that path.

The critical path is intentionally ordered:

1. reuse the Claude and Codex OAuth profiles created in Settings in one profile-bound Omnigent host at a time;
2. stabilize the Omnigent bridge and Workflow Detail communication model against that credentialed host;
3. let workflows request retry-safe, on-demand Omnigent host containers through the same profile and bridge contracts; and
4. checkpoint Omnigent sessions from bridge/artifact evidence so recovery does not depend on one container remaining alive.

Completed historical milestones have been removed from the active roadmap. The sections below track only remaining work that materially advances the Omnigent-host direction.

---

## Omnipresent goals (apply to every milestone)

These three properties are not milestones; they are cross-cutting acceptance lenses. Every milestone's **Done means** is additionally gated by all three, and each milestone's declarative design (below) must state how it satisfies them.

- **Safety.** Runtime, credential, filesystem, network, publish, and approval boundaries are enforced by the substrate and fail fast with actionable errors. Anchored in `docs/Security/` (`ProviderProfiles.md`, `SecretsSystem.md`, `SettingsSystem.md`) and, once it exists, the Omnigent policy model. New capabilities add safety at the boundary — never as hidden prerequisites inside a reusable asset.
- **Resilience.** Runs prefer retry, reroute, degraded mode, or evidence-gated resume over silent failure, and never silently substitute credentials, provider profiles, billing-relevant values, or less-constrained execution paths. Anchored in `docs/Workflows/CheckpointBranchSystem.md`, `docs/Temporal/ErrorTaxonomy.md`, and `docs/Workflows/WorkflowCancellation.md`.
- **Observability.** Live state, terminal outcomes, and durable evidence are inspectable through bridge/chat projections, artifacts, and telemetry rather than second-source dashboards. Anchored in `docs/Observability/LiveLogs.md`, `docs/Observability/OpenTelemetrySystem.md`, and the artifact/evidence model.

## Declarative design first (every milestone)

Per the **Canonical docs are durable and declarative** principle and `docs/Workflows/MoonSpecDocumentModel.md`, each milestone **starts** by creating or updating the canonical declarative document(s) that own its target-state contracts. This roadmap is the imperative execution tracker; the durable desired state lives in `docs/`. Each milestone below opens with an `X.0` declarative-design task naming the doc(s) that must exist and be correct before implementation begins. Implementation that discovers drift ends with a doc-reconciliation pass back into those files.

---

## Status tags

| Tag | Meaning |
| --- | --- |
| 🚧 Active | Primary implementation track |
| 🔧 Partial | Useful substrate exists, but the product path is incomplete |
| 📐 Designed | Desired-state or rollout design exists, implementation is limited |
| ⬜ Not started | No meaningful implementation yet |
| 🔒 Gated | Intentionally waits on another milestone |

---

## Baseline substrate retained from earlier work

These are not active roadmap milestones, but they are important assumptions for the new plan:

- Omnigent is registered as the canonical external agent identity `agentKind=external`, `agentId=omnigent`; Omnigent-specific selection lives under `parameters.omnigent`.
- `integration.omnigent.execute` exists as the v1 streaming-gateway activity and can create or reattach to an Omnigent session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult`.
- `omnigent_bridge_sessions` is the canonical durable store for Omnigent bridge/session state, first-message idempotency, terminal refs, and normalized event indexing.
- Omnigent terminal evidence can include normalized/raw stream artifacts, initial/final snapshots, capture manifests, diagnostics, changed files, workspace files, optional diffs, session files, child-session evidence, and `checkpoint.omnigent.external_state.json`.
- The run workflow records per-step Omnigent external-agent identity and passes it into checkpoint policy resolution, so Omnigent checkpoint captures select the `external_state_ref` lane.
- The Settings/OAuth Session path already creates or reuses named Claude and Codex auth volumes, verifies CLI credential state, and registers connected Provider Profiles; Omnigent host reuse and hard OAuth concurrency enforcement remain unfinished.
- `ProviderProfileManager` already owns durable per-runtime profile leases, cooldowns, stale-lease recovery, and `max_parallel_runs`; the Omnigent path must use that same capacity ledger rather than creating a parallel host-only counter.
- A dedicated local Claude Omnigent host Compose overlay proves the basic volume/home-path wiring, but supported profile routing, lifecycle ownership, Codex parity, and zero-manual-step startup remain incomplete.
- Workflow RAG already has the core ContextPack, gateway/direct transport, Qdrant, multi-collection, overlay, budgeting, and artifact/ref model used by the current managed-session path.
- Dashboard list/detail display modes exist for Workflows and Recurring Schedules through the shared collection workspace and sidebar primitives.
- The Checkpoint Branch API and persistence model already support branch create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, instruction digest, workspace policy, turn ids, git binding, and remediation-created branches; remaining work is the operator/UI/default-flow and Omnigent runtime handoff.
- The remediation context builder writes a restricted `reports/remediation_context.json` artifact during remediation execution creation; remaining work is Omnigent-specific evidence enrichment, tools, typed actions, and UI.

---

## Milestone 1 — Dashboard Navigation, Shared Sidebars & Detail Frames ✅

**Goal:** Establish one far-left application rail, one reusable collection-sidebar system for Workflows, Recurring, and Skills, and one shared detail-frame language for Workflow and recurring schedule detail pages.

**Why it matters:** Operators should experience MoonMind as one console. Top-level navigation must stay at the viewport edge; local collection navigation must start at the content edge; and related detail pages must reuse recognizable structure instead of inheriting centered wrappers or page-specific shells.

### Shipped

- [x] **1.0 Declarative design first** — `docs/UI/CollectionWorkspaceLayout.md` is canonical for far-left shell geometry, shared collection sidebars, and the Workflow/Recurring entity-detail frame, with related UI documents reconciled to that target.
- [x] **1.1 Far-left application shell** — the responsive application rail sits at the viewport's far-left edge and owns primary navigation for Workflows, Create, Recurring, Skills, Omnigent surfaces when enabled, Remediation when enabled, Artifacts/Observability, and Settings.
- [x] **1.2 Reusable collection workspace** — `CollectionWorkspace` places an optional collection sidebar immediately right of the application rail and keeps the primary pane fluid rather than centered in a max-width wrapper.
- [x] **1.3 Shared sidebar component system** — `CollectionSidebar` owns common header, filter, row metrics, active/focus states, pinned-current row, divider, scrolling, loading/empty/error states, accessibility, and responsive behavior.
- [x] **1.4 Required collection sidebars** — Workflow detail/Create, Recurring detail, and Skills preview/create use the shared sidebar primitive with entity-specific adapters.
- [x] **1.5 Shared Workflow/Recurring detail frame** — Workflow and recurring schedule detail render through the shared `EntityDetailFrame` structure while retaining entity-specific content.
- [x] **1.6 Reusable list display modes** — Workflows and Recurring use `hidden`, `sidebar`, and `table` display modes with per-collection preferences and route-owned coercion.
- [x] **1.7 Full-page list and route inventory** — dashboard route families are classified between shared workspace/list surfaces and focused single-pane pages, with backend SPA exclusions for non-UI routes.
- [x] **1.8 Preferences, responsiveness, and accessibility** — route preferences, deep links, selection, focus, mobile fallbacks, and reduced-motion behavior are covered by route/component tests.
- [x] **1.9 UI regression coverage** — tests cover far-left geometry, common sidebar anatomy, required Recurring/Skills sidebars, shared Workflow/Recurring detail regions, direct deep links, localized failures, backend route exclusions, and mobile accessibility.

**Done means:** every major area is reachable from one far-left application rail; Workflow, Recurring, and Skills use the same sidebar shell at the content edge; Workflow and recurring schedule detail pages share a recognizable detail frame; no split workspace is centered with a large left margin; and routes, preferences, accessibility, and mobile fallbacks remain correct.

---

## Milestone 2 — Omnigent Host OAuth from MoonMind Settings 🚧

**Goal:** Claude Code / Anthropic and Codex CLI / OpenAI OAuth configured through MoonMind Settings can be used by profile-bound Omnigent hosts with one global concurrent consumer per OAuth profile.

**Why it matters:** OAuth-backed CLI homes contain mutable access, refresh, and account state. Omnigent host can become the unified runtime only if it consumes the same verified Provider Profiles and volumes as direct MoonMind execution while preventing concurrent refresh writers, second login ceremonies, ambient credentials, and manual host selection.

### Remaining work

- [x] **2.0 Declarative design first** — `docs/Omnigent/OmnigentHostOAuth.md` defines the Settings-to-Provider-Profile-to-host desired state, the global concurrency-one invariant, profile-bound host model, bridge/checkpoint handoff, and acceptance criteria.
- [ ] **2.1 OAuth concurrency invariant** — enforce `max_parallel_runs = 1` for supported Claude/Codex `oauth_volume` profiles in OAuth finalization, Provider Profile create/update APIs, Settings UI, migrations/readiness, and the Provider Profile Manager; do not allow workflow-level overrides.
- [ ] **2.2 Shared credential-capacity lane** — make direct managed execution, Omnigent execution, and reconnect/disconnect maintenance acquire the same backing Provider Profile lease so only one active consumer can read/refresh/write the OAuth identity.
- [ ] **2.3 Runtime-neutral host credential binding** — implement safe `AuthVolumeRef`, `CredentialMountRef`, profile-to-host binding, credential-generation, and host-lease references without putting credential bodies into Temporal, bridge, checkpoint, or artifact payloads.
- [ ] **2.4 Claude Code host OAuth** — bind the selected Claude OAuth volume to one dedicated host at the correct home path, clear competing credentials, manage `.claude.json` separately, run `claude auth status` in the exact host environment, and allow at most one active runner.
- [ ] **2.5 Codex CLI host OAuth** — provide equivalent Codex volume/home/config shaping and login-status verification, with no ambient API-key/custom-provider override and at most one active runner.
- [ ] **2.6 Profile-aware host routing** — let Omnigent requests select `executionProfileRef`; resolve the compatible harness and exact host binding automatically, and remove manual `hostId` extraction or workflow JSON editing from the operator path.
- [ ] **2.7 Supported local Compose path** — make init, UID/GID repair, state-volume setup, OAuth readiness wait, stable host identity, registration, restart behavior, Claude/Codex service activation, and diagnostics part of Compose/init scripts rather than manual runbook steps.
- [ ] **2.8 Reconnect, disconnect, and generation drain** — block or explicitly terminate active consumers before credential mutation, increment credential generation after successful reconnect, drain stale hosts, and fail closed rather than silently falling back to API-key auth.
- [ ] **2.9 Boundary and end-to-end tests** — cover one direct-versus-Omnigent lease, second-host rejection/queueing, wrong-runtime volume isolation, refresh-state persistence, reconnect drain, missing/revoked auth, host restart, retry-safe reuse, and credential non-leakage.

**Done means:** a user who completes Claude or Codex OAuth in Settings can select that Provider Profile for an Omnigent-backed workflow without logging in again; MoonMind automatically binds one compatible host, allows only one active consumer/host/session for the OAuth identity, persists authorized refresh-state writes, and proves the profile/lease/host/session refs without exposing credential values.

---

## Milestone 3 — Omnigent Bridge Communication & Workflow Detail Chat 🚧

**Goal:** Credentialed Omnigent hosts and MoonMind communicate through the Omnigent bridge, and Workflow Detail chat with Omnigent hosts works similarly to other cloud agents.

**Why it matters:** The bridge should be stabilized against a known profile-bound host before MoonMind automates host provisioning. Operators should not care whether a run is backed by a direct managed process, a cloud agent, or an Omnigent host; Workflow Detail should show the conversation, events, approvals, resources, diagnostics, and artifacts through one familiar model.

### Remaining work

- [ ] **3.0 Declarative design reconciliation** — reconcile `docs/Omnigent/OmnigentBridge.md`, `docs/Omnigent/OmnigentHostOAuth.md`, `docs/Security/SettingsSystem.md`, `docs/UI/WorkflowChatPanel.md`, and `docs/UI/WorkflowDetailsPage.md` around profile-aware host routing, bridge ownership, failed-start evidence, and the unchanged-host proxy-first topology.
- [ ] **3.1 Proxy-mode bridge routes** — implement or complete the MoonMind bridge facade for Omnigent-shaped session, event, stream, agent, host, and resource routes while proxying to a stock Omnigent server/host.
- [ ] **3.2 Profile/lease-aware session creation** — require the selected Provider Profile and active lease when targeting an OAuth-bound host; persist profile, binding, host lease, host, session, endpoint, and idempotency refs before posting the first message.
- [ ] **3.3 Bridge event normalizer** — normalize host/session events into durable MoonMind event records while preserving raw event journals as artifacts.
- [ ] **3.4 Bridge session projection API** — expose `GET /api/omnigent/bridge-sessions/{bridge_session_id}/events` and `/stream` as the canonical Workflow Chat/read-model surface.
- [ ] **3.5 Workflow Detail chat projection** — render Omnigent sent messages, assistant deltas, tool/session events, elicitations, approvals, interrupts, stop events, resource notices, terminal outcomes, diagnostics, and artifact links before falling back to legacy logs.
- [ ] **3.6 Artifact/resource harvesting in chat** — link changed files, diffs, workspace files, session files, snapshots, and capture manifests directly from chat and step detail.
- [ ] **3.7 Failed-launch visibility** — create visible bridge diagnostics and a Chat timeline when profile resolution, lease acquisition, OAuth preflight, host registration, or session creation fails before a normal terminal stream exists.
- [ ] **3.8 Direct Codex compatibility producer** — during migration, have direct Codex managed sessions emit bridge-compatible events so Workflow Detail no longer depends on runtime-specific observability records.
- [ ] **3.9 Conformance and smoke tests** — add fake Omnigent server tests, proxy-mode route tests, profile/lease authorization tests, event-normalization tests, chat projection tests, and live combined-stack smoke coverage.
- [ ] **3.10 Omnigent-compatible MoonMind server auth shim** 🔒 — for embedded compatibility mode, reuse the existing upstream Omnigent server/host auth verifier as a narrow library dependency or vendored module with a MoonMind adapter layer. Map verified Omnigent host/API principals to MoonMind service principals and bridge-session ownership; keep Omnigent token parsing, signature/JWKS validation, host-runner credential semantics, and conformance fixtures unchanged where possible. Add only config/SecretRef resolution, audit redaction, and FastAPI dependency glue in MoonMind. Do not reimplement the auth protocol, fork the host, or forward MoonMind user JWT/cookie headers as Omnigent credentials.
- [ ] **3.11 Embedded compatibility mode** 🔒 — implement MoonMind-as-Omnigent-compatible host/server surface only after proxy mode has conformance and live smoke evidence.

**Done means:** an OAuth-backed workflow can be created against a profile-bound stock Omnigent host through MoonMind-owned routing; Workflow Detail provides durable conversation replay, failure visibility, and artifact links even after the host is gone; and embedded mode, when enabled later, remains Omnigent-compatible rather than a MoonMind-specific host fork.

---

## Milestone 4 — Workflow-Requested Omnigent Host Containers 🚧

**Goal:** MoonMind can launch, supervise, assign, and clean up an Omnigent host container on demand when a workflow requests an Omnigent-backed run.

**Why it matters:** Static Compose hosts are useful for proving OAuth and bridge contracts, but they are not the managed-runtime product. Once credential binding and bridge semantics are stable, MoonMind should provision exactly the host capacity a workflow/profile lease requires without orphaning containers or bypassing policy.

### Remaining work

- [ ] **4.0 Declarative design first** — update `docs/Omnigent/OmnigentAdapter.md`, `docs/Omnigent/OmnigentHostOAuth.md`, and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` with the supported host-launch model, durable host lease, registration/readiness contract, capacity layers, mount/network policy, and cleanup semantics before launch code.
- [ ] **4.1 First launch model** — support one local Docker `omnigent-host` container per active Provider Profile lease and Omnigent session; preserve `maxHosts = 1` and `maxSessionsPerHost = 1` for OAuth profiles.
- [ ] **4.2 Launch activity/service** — add a MoonMind-owned launch path that creates an unchanged Omnigent host with endpoint refs, profile/binding refs, credential mounts, workspace policy, network policy, resource limits, labels, deterministic identity, and idempotency keys.
- [ ] **4.3 Durable host lease and retry reconciliation** — persist container/host/session ownership, credential generation, state transitions, heartbeat timestamps, and expiry so Temporal retries reattach or replace safely rather than launch duplicates.
- [ ] **4.4 Host registration and bridge readiness** — wait for exact host registration/heartbeat, ownership, configured harness, OAuth preflight, and bridge reachability before session creation, and surface each readiness stage in Workflow Detail.
- [ ] **4.5 Capacity hierarchy** — combine Provider Profile capacity, host-pool/session capacity, and machine/Docker capacity without creating conflicting counters; provider-account capacity remains authoritative in `ProviderProfileManager`.
- [ ] **4.6 Workspace, artifact, auth, and cache mounts** — standardize workflow-scoped repositories, temporary work areas, profile-bound OAuth volumes, artifact handoff paths, optional caches, UID/GID, and safe mount targets.
- [ ] **4.7 Lifecycle operations and janitor** — support interrupt, stop, terminate, log/resource harvest, host removal, stale-lease reconciliation, credential-generation drain, and orphan cleanup through typed MoonMind actions.
- [ ] **4.8 Static-to-managed compatibility** — retain the supported static Compose host as a local/bootstrap mode while routing both static and on-demand hosts through the same binding, bridge, diagnostics, and artifact contracts.
- [ ] **4.9 Cutover compatibility** — keep direct Codex/Claude managed-session execution behind feature gates until on-demand Omnigent launch is reliable for those harnesses.
- [ ] **4.10 End-to-end launch tests** — cover launch, registration, session start, worker restart, duplicate prevention, cancellation, terminal harvest, host cleanup, OAuth reconnect drain, profile cooldown, and retry after partial failure.

**Done means:** a workflow can request an Omnigent-backed Claude or Codex run without a pre-provisioned host; MoonMind acquires the selected profile, launches exactly one policy-bound host, observes it through the bridge, runs and harvests one session, cleans the host up, and releases all leases idempotently.

---

## Milestone 5 — Omnigent Host Session Checkpoints, Resume & Branching 🚧

**Goal:** Omnigent-backed sessions can be checkpointed, resumed, retried, and branched from MoonMind evidence whether the original host container is still alive or has been replaced.

**Why it matters:** Omnigent owns live session execution, while MoonMind owns durable recovery semantics. Checkpoints must be built from bridge/session/artifact evidence and selected Provider Profile identity, not from an assumption that one host container or mutable OAuth home snapshot remains available forever.

### Remaining work

- [ ] **5.0 Declarative design first** — update `docs/Workflows/CheckpointBranchSystem.md` and `docs/Steps/StepExecutionsAndCheckpointing.md` to define Omnigent bridge external-state completeness, profile/host/session refs, live-reattach versus cold-restore semantics, branch isolation, and local-vs-external restore rules before code.
- [ ] **5.1 Checkpoint boundary and completeness** — capture checkpoints at defined bridge/session boundaries with `externalStateRef`, `idempotencyKey`, `bridgeSessionId`, `omnigentSessionId`, endpoint/profile/binding/host-lease refs, credential generation, diagnostics/terminal refs, workspace or diff refs, and patch availability metadata.
- [ ] **5.2 Host-independent restore contract** — treat the host container as replaceable execution infrastructure; define what must be artifact-backed so a checkpoint can restore onto a newly launched profile-bound host without copying OAuth credential bodies into the checkpoint.
- [ ] **5.3 Live reattach versus cold resume** — reattach only when the original session, host, bridge binding, profile lease, and idempotency evidence remain valid; otherwise acquire the same profile again and start a fresh session from validated checkpoint evidence.
- [ ] **5.4 Resume-from-checkpoint default flow** — make failed-run recovery default to evidence-gated resume when checkpoint evidence is valid, with clear reasons when live reattach, cold restore, or any resume is unavailable.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — connect the existing Checkpoint Branch API to Workflow Detail actions, Provider Profile selection, Omnigent agent/harness selection, publish-mode selection, and host-launch evidence without duplicating branch endpoints.
- [ ] **5.6 Omnigent branch execution** — acquire a new profile/host lease and start a fresh Omnigent session for a branch turn, carrying corrected instructions and validated external-state evidence without mutating the original workflow input or concurrently reusing its OAuth lease.
- [ ] **5.7 Local versus external restore semantics** — split or normalize ambiguous workspace refs so local sandbox paths, host-local paths, and provider-owned external-state artifact refs cannot be confused.
- [ ] **5.8 UI flows** — add Workflow Detail actions for resume, retry, branch, compare branch, inspect checkpoint evidence, and understand why the original or replacement host was selected.
- [ ] **5.9 Replay and idempotency tests** — cover worker restart, Temporal retry, live session reattach, cold host recreation, duplicate first-message prevention, stale credential generations, checkpoint validation failures, branch duplicate prevention, lease cleanup, and unsupported restore attempts.

**Done means:** failed Omnigent workflows can resume from validated checkpoints by default; a live session can be reattached when safe, a removed host can be recreated for cold restore, operators can intentionally branch with new instructions or runtime/profile settings, and all recovery evidence remains MoonMind-owned artifact/ref state rather than host-local mutable state.

---

## Milestone 6 — Remediation Workflows & Evidence-Based Repair 🚧

**Goal:** The remediation system is fully implemented, including custom instructions like a normal Create workflow and access to all artifacts needed for diagnosis and repair.

**Why it matters:** Remediation is where checkpoints, artifacts, chat, policies, and Omnigent runtime control come together. It should be an operator-grade workflow, not a special-case retry button.

### Remaining work

- [ ] **6.0 Declarative design first** — update `docs/Workflows/WorkflowRemediation.md` and `docs/Workflows/RemediationVerificationCadence.md` with the create-path authoring model, typed-action registry, and Omnigent evidence contract before implementation.
- [ ] **6.1 Create-path remediation authoring** — let operators create remediation workflows from the normal Create experience with target workflow/run, custom instructions, runtime/profile selection, authority mode, approval policy, and evidence policy.
- [ ] **6.2 Omnigent remediation context enrichment** — extend the existing `reports/remediation_context.json` builder with Omnigent capture refs, bridge/session event summaries, branch refs, incident/recovery manifests, policy snapshots, and bounded evidence needed by host-backed repair runs.
- [ ] **6.3 Artifact and log access tools** — provide typed remediation tools/API calls for reading target artifacts, diagnostics, step evidence, managed/bridge event streams, and bounded logs without scraping dashboard pages.
- [ ] **6.4 Typed action registry** — implement allowlisted remediation actions such as resume, branch, retry with provenance, interrupt, stop, stale lease cleanup, host cleanup, profile lease eviction, and verification.
- [ ] **6.5 Corrected-instruction repair through Checkpoint Branches** — any remediation repair that changes instructions, branch, runtime, model, or publish mode must create a branch turn instead of mutating the original workflow input.
- [ ] **6.6 Omnigent-backed remediation semantics** — v1 remediation must consume Omnigent artifacts and start fresh corrective sessions; same-session hidden follow-up waits for typed v2 bridge activities.
- [ ] **6.7 Dashboard remediation panels** — show inbound/outbound remediations, context bundle, action log, locks, approvals, verification state, created branches, and prevention PRs from Workflow Detail.
- [ ] **6.8 Audit and loop prevention** — record diagnosis, action request/result, verification, policy decision, and prevention output while preventing duplicate/conflicting healers.
- [ ] **6.9 Autonomous remediation gate** 🔒 — scheduled/automatic remediation remains gated until operator-driven remediation, policy enforcement, audit, and observability are reliable.

**Done means:** an operator can start a remediation workflow with custom instructions, the remediator can inspect the target’s durable evidence and artifacts, execute only typed policy-bound actions, branch when instructions change, verify the result, and leave a complete audit trail.

---

## Milestone 7 — RAG for Omnigent Host Agents 🔧

**Goal:** RAG features are usable with Omnigent host agents.

**Why it matters:** Omnigent host should not regress context quality. The same MoonMind-owned ContextPack, retrieval gateway, scope filters, and artifact evidence used by managed sessions must work for Omnigent-backed Codex, Claude, and future host agents.

### Remaining work

- [ ] **7.0 Declarative design first** — update `docs/Rag/WorkflowRag.md` to define Omnigent first-message context delivery, host-initiated retrieval, scoped retrieval credentials, and budgets as target state before code.
- [ ] **7.1 Initial context injection for Omnigent** — resolve retrieval before or at step start, persist a ContextPack artifact, and deliver retrieved context through the Omnigent first-message/instruction-ref path with prompt-injection safety framing.
- [ ] **7.2 Session-facing retrieval capability** — expose a MoonMind-owned retrieval tool/gateway surface that Omnigent host agents can call for follow-up context within policy.
- [ ] **7.3 Scoped retrieval credentials** — issue bounded retrieval tokens or equivalent bridge credentials to host sessions without exposing embedding provider secrets or raw Qdrant credentials.
- [ ] **7.4 Filters and budgets** — enforce repository/workspace/run/tenant scope, collection selection, top-k, max context, latency, token, and overlay policies for Omnigent retrieval.
- [ ] **7.5 Retrieval evidence in Omnigent artifacts** — link ContextPack refs, retrieval metadata, fallback reason, truncation, budgets, and retrieval telemetry from step manifests, bridge events, and Workflow Detail.
- [ ] **7.6 UI configuration** — expose RAG enablement, collection/scope selection, and budget knobs from Create, Omnigent agent profiles, and remediation authoring where appropriate.
- [ ] **7.7 Quality and fallback tests** — cover automatic retrieval, follow-up retrieval, unavailable gateway, local fallback, policy denial, stale/host-edited overlay behavior, and multi-collection retrieval.

**Done means:** Omnigent-backed agents receive the same durable, policy-bounded, artifact-backed retrieval support as direct managed sessions, including follow-up retrieval during execution.

---

## Milestone 8 — Omnigent Policy Management UI 📐

**Goal:** Omnigent policies are customizable from the MoonMind UI.

**Why it matters:** Omnigent host broadens the runtime surface. Operators need first-class policy editing for hosts, sessions, auth materialization, network/workspace boundaries, capture requirements, approvals, and risky actions.

### Remaining work

- [ ] **8.0 Declarative design first** — create the canonical Omnigent policy model doc (e.g. `docs/Omnigent/OmnigentPolicyModel.md`, cross-linked from `docs/Security/SettingsSystem.md`) defining versioned policy objects, enforcement points, and snapshot semantics before building the editor UI.
- [ ] **8.1 Policy model** — define versioned Omnigent policy objects for host launch, session creation, workspace mounts, auth volumes, network access, tool permissions, resource capture, retention, and risky-action approval.
- [ ] **8.2 Policy editor UI** — add dashboard list/detail/edit surfaces for Omnigent policies with validation, diffing, clone, disable, and rollback behavior.
- [ ] **8.3 Enforcement points** — compile selected policies into host launch, bridge session creation, control actions, resource harvest, outbound boundaries, remediation actions, and checkpoint branches.
- [ ] **8.4 Policy snapshots** — stamp the exact policy version/ref onto workflow runs, bridge sessions, checkpoints, remediation context, and audit events.
- [ ] **8.5 Approval integration** — route policy-gated risky actions through deterministic policy, optional reviewer/human approval, or denial with durable rationale.
- [ ] **8.6 Policy diagnostics** — make denial reasons and misconfiguration actionable in Create, Workflow Detail, host launch diagnostics, and remediation panels.
- [ ] **8.7 Migration from env-only configuration** — replace or layer over Omnigent environment flags with UI-backed endpoint/policy/profile configuration while preserving local-dev simplicity.

**Done means:** an operator can define, select, audit, and revise Omnigent policies from MoonMind, and every Omnigent-backed run records the policy version that governed it.

---

## Milestone 9 — Omnigent Agent Profiles UI 📐

**Goal:** Omnigent agent profiles are customizable from the MoonMind UI.

**Why it matters:** Operators should be able to choose and configure Omnigent-backed Codex, Claude, and custom agents without editing environment variables or raw YAML for every run.

### Remaining work

- [ ] **9.0 Declarative design first** — create the canonical Omnigent agent-profile model doc (e.g. `docs/Omnigent/OmnigentAgentProfiles.md`), building on `docs/Security/ProviderProfiles.md` and `docs/Omnigent/OmnigentAdapter.md`, before profile CRUD UI.
- [ ] **9.1 Agent/profile data model** — define MoonMind-owned Omnigent agent profiles that reference endpoint, upstream agent id/name, harness, default host type, auth volume refs, policy ref, model/reasoning defaults, workspace defaults, capture policy, and RAG defaults.
- [ ] **9.2 Agent discovery and sync** — list upstream Omnigent agents through `/api/agents`, cache/sync metadata, and show availability/health in the dashboard.
- [ ] **9.3 Profile CRUD UI** — create, clone, edit, disable, and delete MoonMind Omnigent agent profiles with validation against the selected endpoint and policy.
- [ ] **9.4 Bundle/custom agent support** — support uploading or referencing Omnigent agent bundles when no upstream agent id exists yet, with artifact-backed provenance.
- [ ] **9.5 Create/schedule/remediation selectors** — make profiles selectable anywhere a runtime/agent is chosen, including normal Create, recurring schedules, branch turns, and remediation workflows.
- [ ] **9.6 Profile smoke validation** — provide a “test profile” flow that validates endpoint reachability, agent resolution, auth volume compatibility, host readiness, policy compilation, and minimal session start.
- [ ] **9.7 Decommission env defaults** — migrate `OMNIGENT_DEFAULT_AGENT_NAME` and related single-default behavior into profile selection while retaining env fallback for bootstrap/local dev.

**Done means:** users can manage Omnigent-backed agent choices from MoonMind UI, bind them to auth volumes and policies, and select them consistently across workflows, schedules, checkpoints, and remediation.

---

## Milestone 10 — Cutover, Documentation & Compatibility Cleanup 🔧

**Goal:** Finish the roadmap cutover by aligning public docs, architecture docs, tests, and compatibility shims with the Omnigent-host direction.

### Remaining work

- [ ] **10.0 Declarative reconciliation first** — treat this milestone as the doc-reconciliation pass: confirm `README.md`, `docs/MoonMindArchitecture.md`, `docs/Omnigent/CombinedStackValidationAndRollback.md`, and each per-milestone canonical doc above reflect shipped desired state before publishing the conformance matrix.
- [ ] **10.1 README and architecture repositioning** — update public positioning from direct Codex/Claude managed sessions as the product center to Omnigent host as the unified managed runtime boundary, while explaining current compatibility state honestly.
- [ ] **10.2 Direct runtime compatibility policy** — document which direct Codex/Claude managed-session paths remain supported, which are migration shims, and what evidence they must emit while they exist.
- [ ] **10.3 Obsolete roadmap/doc cleanup** — archive or remove old local-only handoffs and completed milestone tracking that no longer guides implementation.
- [ ] **10.4 Combined-stack validation** — keep MoonMind + Omnigent local validation and rollback docs current as the host launch, bridge, auth-volume, and profile flows evolve.
- [ ] **10.5 Release metadata hygiene** — align package versions, license declarations, and public descriptions with the actual MoonMind + Omnigent runtime story.
- [ ] **10.6 Conformance matrix** — publish a small matrix showing supported combinations of runtime/harness, auth mode, host type, RAG, checkpoint/resume, remediation, and policy/profile UI support.

**Done means:** the repository no longer presents completed historical work as active roadmap debt, and operators/contributors can understand the current Omnigent-host product direction from README, docs, tests, and roadmap without reconciling conflicting narratives.

---

## Milestone 11 — Pentest De-scoped; External-Egress Safety Gate Retained 🔒

**Goal:** Pentest is no longer a first-class product feature. Reduce it to a thin skill/preset over the generic one-shot Docker workload path, keep it disabled by default and lab-only, and retain the external-target egress gate as a safety blocker until the substrate enforces restricted egress.

**Why the change:** Pentest was intended as a minor capability, but the dedicated runner image plus MoonMind-specific scope-governance, provider-lease, heartbeat, settings, and scope-authoring UI machinery grew into a large maintenance surface (~12–13k lines) disproportionate to its value. Under **Orchestrate, don't recreate agents** and **Portable capabilities over MoonMind coupling**, MoonMind should consume the upstream PentestGPT container through the generic workload launcher (`moonmind/workloads/*`, which already has no pentest coupling) rather than carry a bespoke execution and governance stack. What must not regress is safety: the runner still runs on Docker `bridge`, which is not an enforced egress boundary, so external targets stay gated.

**Recommended disposition (product decision pending):** keep a thin version — the runner image plus a small skill/preset that launches it through the generic workload path and publishes one `security_pentest_report` artifact — and delete the MoonMind-specific pentest machinery (the bulk of `moonmind/integrations/pentest/models.py`, the provider-lease/heartbeat/orphan-cleanup block in `activity_runtime.py`, most of `pentest_activities.py` and `PentestSettings`, and the scope-authoring UI). Fold enforced egress into the generic launched-workload/host **network-policy** substrate (Milestone 4 host launch and the omnipresent **safety** goal) so it is a substrate property, not a pentest-only feature. If the decision is instead full removal, delete the capability, its docs (`docs/Steps/PentestTool.md`, `docs/Security/PentestOperations.md`), and its pinned docs-policy tests in one cohesive change per the **Pre-release means delete, don't deprecate** policy.

### Retained safety gate

- [ ] **11.1 Restricted egress boundary for PentestGPT external targets** — implement and document a network-enforced egress boundary (a generic launched-workload/host capability: dedicated Docker network, egress proxy, or firewall sidecar) that can reach only approved lab/provider endpoints before external targets can be enabled.
- [ ] **11.2 External-target enablement gate** — fail fast when an operator enables external pentest targets without a validated restricted-egress profile and recorded security review.
- [ ] **11.3 Enforcement tests and diagnostics** — cover egress-denied launches, missing network attachment, approval metadata, and dashboard/runbook warnings.

**Done means:** pentest carries no bespoke first-class product machinery beyond a thin skill over the generic workload path, and external-target runs cannot be enabled unless the deployment has validated restricted egress, explicit approval evidence, and operator-visible diagnostics proving the enforced network posture.

---

## Re-assessed items from the previous roadmap

| Previous theme | New disposition |
| --- | --- |
| Claude Code parity on the direct shared managed-session plane | Reframed as Claude Code via Settings-created Omnigent host OAuth, bridge events/Workflow Chat, workflow-requested host launch, and host-independent checkpoint recovery. A parallel direct Claude controller is no longer the primary path. |
| Automatic RAG context injection and RAG context packs | Moved into Milestone 7, with Omnigent first-message delivery and host-initiated retrieval gateway support as the acceptance path. |
| Dashboard artifact browsing and remediation panels | Split across Milestone 1 for reusable navigation/list surfaces, Milestone 3 for Omnigent chat/artifact projection, and Milestone 6 for remediation-specific panels. |
| Resume-from-checkpoint and recovery actions | Consolidated into Milestone 5 as Omnigent host-session checkpoint resume/branching, with Milestone 6 owning remediation’s custom-instruction branch flow. |
| Safety guardrails, governance telemetry, and secret lifecycle | Reframed as the omnipresent **safety** goal applied to every milestone, and embedded in Milestones 2, 3, 4, 6, and 8 as OAuth concurrency/auth-volume boundaries, bridge authorization, host launch/network policy, remediation audit, and Omnigent policy enforcement. Standalone safety work should be added only when it is not tied to those paths. |
| PentestGPT restricted egress and external target safety | Pentest is de-scoped from a first-class feature to a thin skill over the generic workload path (Milestone 11). Enforced egress becomes a generic launched-workload/host safety property under the omnipresent safety goal; the current Docker `bridge` runner is not a restricted-egress boundary, so external targets stay gated until enforcement exists. |
| Deep observability, live logs, and trace/log links | Embedded into Milestones 3, 5, and 6 as bridge chat replay, checkpoint evidence, remediation evidence access, and artifact diagnostics. Full OTel/cost expansion can follow after Omnigent-host execution is stable. |
| Responses API feature parity | Not on the critical path for Omnigent host cutover unless a concrete Omnigent/cloud-agent integration requires it. |
| Completed resiliency, sandbox, memory, vendor portability, and baseline observability milestones | Removed from active roadmap tracking. They remain part of the product substrate and should be documented elsewhere, not tracked as unfinished roadmap items. |

---

## Priority ordering

| Priority | Milestone | Status | Primary dependency |
| --- | --- | --- | --- |
| 🔴 P0 | 1 — Dashboard navigation, sidebars, and full-page lists | ✅ Complete | Can proceed in parallel |
| 🔴 P0 | 2 — Omnigent host OAuth from MoonMind Settings | 🚧 Active | Required for credentialed Claude/Codex host execution |
| 🔴 P0 | 3 — Omnigent bridge communication and Workflow Detail chat | 🚧 Active | Builds on a profile-bound OAuth host; required before dynamic launch |
| 🔴 P0 | 4 — Workflow-requested Omnigent host containers | 🚧 Active | Depends on 2 for credentials and 3 for registration/session communication |
| 🔴 P0 | 5 — Omnigent host-session checkpoints, resume, and branching | 🚧 Active | Depends on stable bridge evidence and host lifecycle from 3 and 4 |
| 🔴 P0 | 6 — Remediation workflows and evidence-based repair | 🚧 Active | Depends on 3 and 5 for full power |
| 🟠 P1 | 7 — RAG for Omnigent host agents | 🔧 Partial | Depends on basic Omnigent execution and profile selection |
| 🟠 P1 | 8 — Omnigent policy management UI | 📐 Designed | Depends on launch/bridge enforcement points |
| 🟠 P1 | 9 — Omnigent agent profiles UI | 📐 Designed | Depends on endpoint/profile/policy data model |
| 🟡 P2 | 10 — Cutover, docs, and compatibility cleanup | 🔧 Partial | Follows P0/P1 stabilization |
| 🔒 Gate | 11 — Pentest de-scoped; external-egress safety gate retained | 🔒 Gated | Not first-class product work; external targets blocked until substrate egress enforcement |

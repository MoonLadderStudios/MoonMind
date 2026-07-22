# 🌙 MoonMind Roadmap

> Roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**, with a **Codex-first cutover** held to the omnipresent goals of **safety, resilience, and observability**.
>
> The immediate destination is Codex CLI running through profile-bound Omnigent hosts. Claude Code support through Omnigent is intentionally deferred to a late milestone and is not on the current critical path.
>
> **Document class:** this roadmap is an *imperative execution tracker* (milestones, tasks, status). Per `docs/Workflows/MoonSpecDocumentModel.md`, durable desired state lives in the canonical declarative `docs/` files each milestone names in its `X.0` task — not here. When the two disagree, the declarative docs win.
>
> Last updated: 2026-07-18

---

## Direction of travel

MoonMind is moving from direct Codex CLI managed sessions toward **Omnigent host as the primary Codex runtime boundary**. The Codex OAuth profile created in MoonMind Settings can now be reused by an Omnigent host, and the host can register with the Omnigent server without a second login ceremony.

The target split is:

- **MoonMind owns** the dashboard, create/edit flows, Temporal orchestration, workflow/run identity, Provider Profile selection and capacity, policy selection, checkpoint/resume/branching, remediation, retrieval, durable artifacts, and operator audit evidence.
- **Omnigent host owns** live Codex process lifecycle inside the host environment, host-side workspace resources, live session events, and harness-specific launch details.
- **The MoonMind Omnigent bridge owns** the compatibility boundary between those systems: profile-authorized session creation/attachment, event streaming, Workflow Detail chat projection, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex managed-session code remains compatibility substrate** until the Codex Omnigent path is reliable enough to cut over. New Codex roadmap work should land through the Omnigent host/bridge path or emit evidence compatible with it.
- **Claude Code remains outside the current Omnigent critical path.** Existing direct Claude support and the already-supported static `omnigent-host-claude` Compose slice remain, but new Omnigent parity work belongs only in the late Claude milestone.

The critical path is intentionally ordered:

1. stabilize the bridge and Workflow Detail communication model against the verified profile-bound Codex host;
2. productize workflow-requested Codex host containers using the shipped profile lease, host lease, registration, and cleanup substrate;
3. complete host-independent checkpoint, resume, retry, and branch flows;
4. finish remediation, retrieval, policy, and agent-profile product surfaces on the Codex path;
5. cut over documentation and compatibility policy to Codex-through-Omnigent as the primary managed-runtime story; and
6. add Claude Code Omnigent parity later without blocking the Codex cutover.

Completed historical milestones have been removed from the active roadmap. Milestone numbers below are compact execution order and are re-compacted as milestones complete; the durable acceptance-claim identifiers pinned by documentation contract tests are listed under [Durable acceptance-claim identifiers](#durable-acceptance-claim-identifiers) and stay stable across that renumbering.

---

## Omnipresent goals (apply to every milestone)

These three properties are not milestones; they are cross-cutting acceptance lenses. Every milestone's **Done means** is additionally gated by all three, and each milestone's declarative design must state how it satisfies them.

- **Safety.** Runtime, credential, filesystem, network, publish, and approval boundaries are enforced by the substrate and fail fast with actionable errors. Anchored in `docs/Security/` (`ProviderProfiles.md`, `SecretsSystem.md`, `SettingsSystem.md`) and, once it exists, the Omnigent policy model. New capabilities add safety at the boundary — never as hidden prerequisites inside a reusable asset.
- **Resilience.** Runs prefer retry, reroute, degraded mode, or evidence-gated resume over silent failure, and never silently substitute credentials, Provider Profiles, billing-relevant values, or less-constrained execution paths. Anchored in `docs/Workflows/CheckpointBranchSystem.md`, `docs/Temporal/ErrorTaxonomy.md`, and `docs/Workflows/WorkflowCancellation.md`.
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

## Baseline substrate retained from completed work

These are not active roadmap milestones. They are shipped assumptions for the remaining Codex-first plan:

- The completed dashboard-navigation milestone has been removed. The far-left application rail, shared collection sidebars, shared Workflow/Recurring detail frame, list display modes, responsive behavior, accessibility, and regression coverage remain product substrate.
- Omnigent is registered as the canonical external agent identity `agentKind=external`, `agentId=omnigent`; Omnigent-specific selection lives under `parameters.omnigent`.
- `integration.omnigent.execute` can create or reattach to an Omnigent session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult`.
- `omnigent_bridge_sessions` is the canonical durable store for Omnigent bridge/session state, first-message idempotency, profile authorization, terminal refs, lifecycle evidence, and normalized event indexing.
- Omnigent terminal evidence can include normalized/raw stream artifacts, initial/final snapshots, capture manifests, diagnostics, changed files, workspace files, optional diffs, session files, child-session evidence, and `checkpoint.omnigent.external_state.json`.
- The Settings/OAuth Session path creates or reuses the Codex auth volume, verifies Codex credential state, and registers a connected Provider Profile with hard OAuth capacity of one.
- Direct Codex execution, Omnigent execution, OAuth connect/reconnect/disconnect, validation, and repair use the same purpose-aware Provider Profile capacity ledger. The mutable OAuth identity cannot be consumed by several execution substrates at once.
- Safe profile-bound Codex host bindings, credential mounts, host leases, credential generations, lifecycle transitions, and redacted preflight evidence are persisted without placing credential bodies into Temporal, bridge, checkpoint, or artifact payloads.
- `executionProfileRef` is routed through a retry-safe profile-bound execution coordinator. It acquires the Provider Profile lease, persists authorization before session creation, starts or checks the exact host, records the host/session identity before the first message, and releases capacity only after host cleanup.
- The Codex host path supports both a static bootstrap host and deterministic on-demand Docker hosts. Initialization repairs state-volume ownership, mounts the Codex OAuth home at `/home/app/.codex`, removes competing provider credentials, verifies `codex login status`, waits for the exact `codex-native` host registration, and cleans up lease-owned containers and state volumes.
- The Codex OAuth host has been live-verified to reuse MoonMind-managed credentials and automatically register with the Omnigent server.
- Dedicated OAuth hosts now live in the canonical `docker-compose.yaml` behind Compose profiles such as `omnigent-host-codex`. Supported startup uses `COMPOSE_PROFILES`; the superseded platform-sensitive Compose overlays are removed.
- Static and on-demand hosts use the published Omnigent host image selection, run as UID/GID `1000:1000` from `/home/app`, keep Omnigent registration credentials separate from provider OAuth, and retain explicit image/tag and complete image-reference overrides.
- The on-demand runtime uses deterministic names and labels, a lease-owned Omnigent state volume, a read-only root filesystem with bounded temporary storage, a workflow workspace mount, and the configured MoonMind/Omnigent network.
- A janitor workflow and generation-drain paths exist for expired, missing, orphaned, or stale-credential hosts.
- Host-independent checkpoint identity and recovery-decision primitives exist for live reattach, cold restore, and branch isolation; the full operator and default-recovery flows remain roadmap work.
- The run workflow records per-step Omnigent external-agent identity and passes it into checkpoint policy resolution, so Omnigent checkpoint captures select the `external_state_ref` lane.
- The generic Container Jobs/workload service plane now owns canonical `WorkspaceLocator` semantics, daemon-visible workspace resolution, bounded/redacted logs, declared-output manifests, runtime diagnostics, lifecycle projections, cancellation, and cleanup for one-shot Docker workloads. Omnigent host work should reuse those shared primitives where compatible while preserving the distinct long-lived host/session lease model.
- Workflow RAG already has the core ContextPack, gateway/direct transport, Qdrant, multi-collection, overlay, budgeting, and artifact/ref model used by the current managed-session path.
- The Checkpoint Branch API and persistence model already support branch create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, instruction digest, workspace policy, turn ids, git binding, and remediation-created branches.
- The remediation context builder writes a restricted `reports/remediation_context.json` artifact during remediation execution creation.

---

## Durable acceptance-claim identifiers

Milestone numbers above are compact execution order and are re-compacted as milestones complete. A small set of acceptance claims, however, carry **durable identifiers pinned by documentation contract tests** (`tests/unit/docs/test_final_docs_cleanup_policy.py` and `tests/integration/docs/test_final_docs_cleanup_contract.py`). These identifiers encode safety and evidence invariants — the checkpoint-resume, remediation-evidence, RAG-injection, and PentestGPT external-egress-gate acceptance claims — so they stay stable even when the milestone numbers change. Each maps to its current execution task:

- [ ] **5.1 Checkpoint boundary and completeness** — now tracked as Milestone 3.1.
- [ ] **5.4 Resume-from-checkpoint default flow** — now tracked as Milestone 3.4.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — now tracked as Milestone 3.5.
- [ ] **6.2 Omnigent remediation context enrichment** — now tracked as Milestone 4.2.
- [ ] **7.1 Initial context injection for Omnigent** — now tracked as Milestone 5.1.
- [ ] **11.1 Restricted egress boundary for PentestGPT external targets** — now tracked as Milestone 10.2.

Changing any identifier above is a deliberate, owner-approved invariant change: update the pinning contract tests in the same change rather than dropping the identifier to make a roadmap edit pass.

---

## Milestone 1 — Omnigent Bridge Communication & Workflow Detail Chat 🚧

**Goal:** Profile-bound Codex hosts and MoonMind communicate through the Omnigent bridge, and Workflow Detail presents Codex Omnigent sessions through the same durable conversation and evidence model used by other agents.

**Why it matters:** Credential and host registration are now proven. The next product boundary is communication: operators should not need an Omnigent-side dashboard or runtime-specific logs to understand a Codex run, its failures, its resources, or its artifacts.

### Remaining work

- [ ] **1.0 Declarative design reconciliation** — reconcile `docs/Omnigent/OmnigentBridge.md`, `docs/Omnigent/OmnigentHostOAuth.md`, `docs/Security/SettingsSystem.md`, `docs/UI/WorkflowChatPanel.md`, and `docs/UI/WorkflowDetailsPage.md` around the shipped Codex profile/host lifecycle, canonical Compose path, profile authorization, failed-start evidence, and unchanged-host proxy-first topology.
- [ ] **1.1 Proxy-mode bridge completion** — complete the MoonMind bridge facade for the required Omnigent-shaped session, event, stream, agent, host, and resource routes while proxying to a stock Omnigent server/host.
- [ ] **1.2 Durable event normalization** — normalize host/session events into durable MoonMind event records while preserving raw event journals as artifacts and retaining the already-persisted profile/lease/host/session authorization chain.
- [ ] **1.3 Bridge session projection API** — expose the canonical bridge-session event page and stream used by Workflow Detail, with replay, cursoring, ownership enforcement, and terminal fallback behavior.
- [ ] **1.4 Workflow Detail chat projection** — render sent messages, assistant deltas, tool/session events, elicitations, approvals, interrupts, stop events, resource notices, terminal outcomes, diagnostics, and artifact links before falling back to legacy logs.
- [ ] **1.5 Artifact/resource harvesting in chat** — link changed files, diffs, workspace files, session files, snapshots, capture manifests, bounded logs, and diagnostics directly from chat and step detail.
- [ ] **1.6 Failed-launch visibility** — turn the shipped lifecycle events for profile resolution, lease acquisition, OAuth preflight, host registration, bridge authorization, session creation, and cleanup into an operator-visible Chat timeline with actionable diagnostics.
- [ ] **1.7 Direct Codex compatibility producer** — during migration, have direct Codex managed sessions emit bridge-compatible events so Workflow Detail no longer depends on runtime-specific observability records.
- [ ] **1.8 Conformance and live smoke tests** — cover fake Omnigent server behavior, proxy routes, profile/lease authorization, event normalization, chat projection, static-profile startup, on-demand host startup, and live combined-stack Codex smoke coverage.
- [ ] **1.9 Omnigent-compatible MoonMind server auth shim** 🔒 — for embedded compatibility mode, reuse the upstream Omnigent server/host auth verifier through a narrow adapter. Do not reimplement the auth protocol, fork the host, or forward MoonMind user JWT/cookie headers as Omnigent credentials.
- [ ] **1.10 Embedded compatibility mode** 🔒 — implement MoonMind-as-Omnigent-compatible host/server surface only after proxy mode has conformance and live smoke evidence.

**Done means:** a Codex OAuth-backed workflow can be created against a profile-bound stock Omnigent host through MoonMind-owned routing; Workflow Detail provides durable conversation replay, failure visibility, logs, resources, and artifact links even after the host is gone; and embedded mode, if enabled later, remains Omnigent-compatible rather than a MoonMind-specific host fork.

---

## Milestone 2 — Workflow-Requested Codex Omnigent Host Containers 🚧

**Goal:** Productize the shipped host-lifecycle substrate so a workflow can request, observe, control, and clean up a policy-bound Codex Omnigent host without pre-provisioning or environment-only operator choices.

**Why it matters:** Static Compose and deterministic on-demand launch already share durable profile, binding, host-lease, registration, session, evidence, and cleanup substrate. The remaining work is to make those capabilities a first-class product contract: explicitly policy-selected, workspace-authoritative, machine-capacity-aware, resource-bounded, operator-controlled, and cutover-ready.

### Shipped entering this milestone

- The profile-bound coordinator already requires a launch-ready Codex OAuth `executionProfileRef`, acquires the shared purpose-aware Provider Profile lease, creates an idempotent host lease, persists bridge authorization before session creation, binds the exact registered `codex-native` host, and releases provider capacity only after host cleanup.
- The canonical `omnigent-host-codex` Compose profile and deterministic on-demand Docker path already use the same durable binding, bridge, readiness, first-message, artifact-harvest, credential-generation, and terminal cleanup contracts.
- On-demand hosts already use deterministic names and labels, a lease-owned Omnigent state volume, the canonical `/home/app/.codex` OAuth mount, UID/GID `1000:1000`, `/home/app`, a read-only root, bounded temporary storage, a workflow workspace, immutable Skill/tool projections, and the configured MoonMind/Omnigent network.
- Failed-launch and terminal lifecycle stages now record explicit boundary starts and outcomes and project into Workflow Detail even when the provider emits zero stream events; the janitor reconciles expired, missing, orphaned, and stale-generation hosts.
- The versioned live-conformance runner, immutable server/host image-reference support, isolated no-volume cleanup, and durable externally resolved evidence contract now exist. Passing credentialed provider environments and publishing their evidence remains release work.

### Remaining work

The execution slices below consume the canonical [Codex via Omnigent Create-to-host contract](Omnigent/CodexCreateToHostContract.md) established for MoonLadderStudios/MoonMind#3449; design completion does not mark these implementation or credentialed-conformance slices complete.

- [x] **2.0 Declarative design reconciliation** — `docs/Omnigent/OmnigentAdapter.md`, `docs/Omnigent/OmnigentHostOAuth.md`, `docs/Omnigent/CombinedStackValidationAndRollback.md`, and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` now define the actual hybrid ownership model, canonical Compose plus deterministic on-demand paths, durable host lease, registration/readiness contract, capacity hierarchy, workspace/mount/network boundaries, evidence authority, immutable-image conformance boundary, and cleanup-before-release semantics.
- [ ] **2.1 Product-owned host launch selection** — replace the bootstrap-only `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` decision with an explicit versioned policy/profile host mode that selects static Compose or on-demand Docker without workflow JSON editing, manual `hostId` handling, or silent fallback.
- [ ] **2.2 Workspace and shared Docker substrate convergence** — move the current private hashed workspace and raw Docker path boundary behind canonical `WorkspaceLocator`, owning-worker authority checks, daemon-visible mount translation, bounded/redacted logs, output manifests, runtime diagnostics, artifact handoff, and reusable cleanup primitives while preserving the distinct long-lived host/session lease model.
- [ ] **2.3 Machine, resource, and network policy completion** — retain the shipped Provider Profile and one-host/one-session limits while adding explicit machine/Docker admission, CPU, memory, process, timeout, temporary-storage, image, network, and enforced-egress policy with an immutable effective launch snapshot.
- [ ] **2.4 Mount, artifact, and cache contract completion** — standardize static/on-demand parity for workflow repositories, temporary work areas, the profile-bound OAuth home, separate Omnigent state, Skill/tool projections, artifact gateway or spool paths, optional caches, UID/GID, safe targets, retention, and local-versus-daemon path translation.
- [ ] **2.5 Typed lifecycle controls and reconciliation hardening** — build operator-authorized interrupt, stop, terminate, harvest, drain, remove, generation-reconcile, stale-lease, and orphan-cleanup controls on the shipped lifecycle evidence and janitor; prove idempotency across worker crashes and partially completed Docker operations.
- [ ] **2.6 Direct Codex cutover gate** — keep direct Codex managed-session execution behind a compatibility feature gate until policy-selected on-demand launch, canonical workspace resolution, cancellation, evidence harvest, cleanup, and lease release meet the same reliability bar.
- [ ] **2.7 Publish the credentialed conformance matrix** — the scheduled and protected-environment-gated `.github/workflows/omnigent-live-conformance.yml` now executes `tools/run_omnigent_live_conformance.py` across published-stock, static restart/replay, on-demand, and failure modes with digest-pinned images, an already-enrolled OAuth profile, a real operator action adapter, and independently resolved secret-scanned evidence refs. It publishes a combined matrix artifact only after every mode passes. Keep this item open until that credentialed artifact records the first complete passing matrix; workflow existence alone is not provider proof. (MoonLadderStudios/MoonMind#3368)

**Done means:** a workflow selects Codex Omnigent plus a Provider Profile and policy without a pre-provisioned host; MoonMind resolves a canonical workspace, acquires provider and machine capacity, realizes one policy-bounded static or on-demand stock host, proves registration and readiness, runs and harvests one session, exposes typed controls and durable evidence, cleans only owned resources, and releases all leases idempotently with the Provider Profile lease last.

---

## Milestone 3 — Omnigent Host Session Checkpoints, Resume & Branching 🔧

**Goal:** Codex Omnigent sessions can be checkpointed, resumed, retried, and branched from MoonMind evidence whether the original host container is still alive or has been replaced.

**Why it matters:** The identity and recovery-decision primitives now exist, but operators still need a complete default recovery flow. Checkpoints must remain host-independent and artifact-backed rather than treating one Docker container or mutable OAuth home as durable workflow state.

### Remaining work

- [ ] **3.0 Declarative design reconciliation** — update `docs/Workflows/CheckpointBranchSystem.md` and `docs/Steps/StepExecutionsAndCheckpointing.md` for the shipped profile/host/session refs, live-reattach versus cold-restore decision model, branch isolation, canonical `WorkspaceLocator` semantics, and credential-generation rules.
- [ ] **3.1 Checkpoint boundary and completeness** — wire the shipped identity model into checkpoint capture at defined bridge/session boundaries with `externalStateRef`, `idempotencyKey`, `bridgeSessionId`, `omnigentSessionId`, endpoint/profile/binding/host-lease refs, credential generation, diagnostics/terminal refs, workspace or diff refs, and patch availability metadata.
- [ ] **3.2 Host-independent restore contract** — define and implement the artifact-backed workspace, diff, instruction, session, and resource evidence required to restore onto a newly launched profile-bound host without copying OAuth credential bodies into the checkpoint.
- [ ] **3.3 Live reattach and cold resume integration** — use the existing decision primitives in the production recovery path: reattach only when every original authority remains valid; otherwise reacquire the same profile and start a fresh session from validated checkpoint evidence.
- [ ] **3.4 Resume-from-checkpoint default flow** — make failed-run recovery default to evidence-gated resume when checkpoint evidence is valid, with clear reasons when live reattach, cold restore, or any resume is unavailable.
- [ ] **3.5 Checkpoint Branch UI and runtime-profile gaps** — connect the existing Checkpoint Branch API to Workflow Detail actions, Provider Profile selection, Codex Omnigent agent selection, publish-mode selection, and host-launch evidence without duplicating branch endpoints.
- [ ] **3.6 Omnigent branch execution** — acquire a new profile/host lease and start a fresh Omnigent session for a branch turn, carrying corrected instructions and validated external-state evidence without mutating the original workflow input or concurrently reusing its OAuth lease.
- [ ] **3.7 Local versus external workspace semantics** — use the canonical `WorkspaceLocator` discriminated union and resolver so MoonMind sandbox paths, daemon-visible host paths, and provider-owned external-state artifact refs cannot be confused.
- [ ] **3.8 Operator UI flows** — add Workflow Detail actions for resume, retry, branch, compare branch, inspect checkpoint evidence, and understand why the original or replacement host was selected.
- [ ] **3.9 Replay and idempotency tests** — cover worker restart, Temporal retry, live session reattach, cold host recreation, duplicate first-message prevention, stale credential generations, checkpoint validation failures, branch duplicate prevention, lease cleanup, and unsupported restore attempts.

**Done means:** failed Codex Omnigent workflows resume from validated checkpoints by default; a live session can be reattached when safe, a removed host can be recreated for cold restore, operators can intentionally branch with new instructions or runtime/profile settings, and all recovery evidence remains MoonMind-owned artifact/ref state rather than host-local mutable state.

---

## Milestone 4 — Remediation Workflows & Evidence-Based Repair 🚧

**Goal:** The remediation system is fully implemented, including custom instructions through the normal Create experience and access to all durable evidence needed for diagnosis and repair.

**Why it matters:** Remediation is where checkpoints, artifacts, chat, policies, and Codex Omnigent runtime control come together. It should be an operator-grade workflow, not a special-case retry button.

### Remaining work

- [ ] **4.0 Declarative design first** — update `docs/Workflows/WorkflowRemediation.md` and `docs/Workflows/RemediationVerificationCadence.md` with the create-path authoring model, typed-action registry, and Codex Omnigent evidence contract before implementation.
- [ ] **4.1 Create-path remediation authoring** — let operators create remediation workflows from the normal Create experience with target workflow/run, custom instructions, runtime/profile selection, authority mode, approval policy, and evidence policy.
- [ ] **4.2 Omnigent remediation context enrichment** — extend `reports/remediation_context.json` with Omnigent capture refs, bridge/session event summaries, host/profile/lease refs, branch refs, incident/recovery manifests, policy snapshots, and bounded evidence needed by host-backed repair runs.
- [ ] **4.3 Artifact and log access tools** — provide typed remediation tools/API calls for reading target artifacts, diagnostics, step evidence, bridge event streams, Container Job/host logs, and bounded runtime journals without scraping dashboard pages.
- [ ] **4.4 Typed action registry** — implement allowlisted remediation actions such as resume, branch, retry with provenance, interrupt, stop, stale lease cleanup, host cleanup, profile lease eviction, and verification.
- [ ] **4.5 Corrected-instruction repair through Checkpoint Branches** — any remediation repair that changes instructions, branch, runtime, model, or publish mode must create a branch turn instead of mutating the original workflow input.
- [ ] **4.6 Omnigent-backed remediation semantics** — v1 remediation consumes Omnigent artifacts and starts fresh corrective sessions; same-session hidden follow-up waits for typed v2 bridge activities.
- [ ] **4.7 Dashboard remediation panels** — show inbound/outbound remediations, context bundle, action log, locks, approvals, verification state, created branches, and prevention PRs from Workflow Detail.
- [ ] **4.8 Audit and loop prevention** — record diagnosis, action request/result, verification, policy decision, and prevention output while preventing duplicate or conflicting healers.
- [ ] **4.9 Autonomous remediation gate** 🔒 — scheduled or automatic remediation remains gated until operator-driven remediation, policy enforcement, audit, and observability are reliable.

**Done means:** an operator can start a remediation workflow with custom instructions, the remediator can inspect the target's durable evidence and artifacts, execute only typed policy-bound actions, branch when instructions change, verify the result, and leave a complete audit trail.

---

## Milestone 5 — RAG for Codex Omnigent Host Agents 🔧

**Goal:** MoonMind RAG features are usable by Codex running through Omnigent hosts.

**Why it matters:** Moving Codex behind Omnigent must not regress context quality. The same MoonMind-owned ContextPack, retrieval gateway, scope filters, budgets, and artifact evidence used by direct managed sessions must work for the Codex host path.

### Remaining work

- [ ] **5.0 Declarative design first** — update `docs/Rag/WorkflowRag.md` to define Omnigent first-message context delivery, host-initiated retrieval, scoped retrieval credentials, and budgets as target state before code.
- [ ] **5.1 Initial context injection for Omnigent** — resolve retrieval before or at step start, persist a ContextPack artifact, and deliver retrieved context through the Omnigent first-message/instruction-ref path with prompt-injection safety framing.
- [ ] **5.2 Session-facing retrieval capability** — expose a MoonMind-owned retrieval tool/gateway surface that the Codex host session can call for follow-up context within policy.
- [ ] **5.3 Scoped retrieval credentials** — issue bounded retrieval tokens or equivalent bridge credentials to host sessions without exposing embedding-provider secrets or raw Qdrant credentials.
- [ ] **5.4 Filters and budgets** — enforce repository/workspace/run/tenant scope, collection selection, top-k, max context, latency, token, and overlay policies for Omnigent retrieval.
- [ ] **5.5 Retrieval evidence in Omnigent artifacts** — link ContextPack refs, retrieval metadata, fallback reason, truncation, budgets, and retrieval telemetry from step manifests, bridge events, and Workflow Detail.
- [ ] **5.6 UI configuration** — expose RAG enablement, collection/scope selection, and budget knobs from Create, Codex Omnigent agent profiles, and remediation authoring where appropriate.
- [ ] **5.7 Quality and fallback tests** — cover automatic retrieval, follow-up retrieval, unavailable gateway, local fallback, policy denial, stale or host-edited overlay behavior, and multi-collection retrieval.

**Done means:** Codex Omnigent sessions receive the same durable, policy-bounded, artifact-backed retrieval support as direct managed sessions, including follow-up retrieval during execution.

---

## Milestone 6 — Omnigent Policy Management UI 📐

**Goal:** Omnigent policies are customizable from the MoonMind UI.

**Why it matters:** Codex host execution broadens the runtime surface. Operators need first-class policy editing for hosts, sessions, auth materialization, Docker/network/workspace boundaries, capture requirements, approvals, and risky actions.

### Remaining work

- [ ] **6.0 Declarative design first** — create the canonical Omnigent policy model doc, cross-linked from `docs/Security/SettingsSystem.md`, defining versioned policy objects, enforcement points, and snapshot semantics before building the editor UI.
- [ ] **6.1 Policy model** — define versioned Omnigent policy objects for host mode, Docker launch, session creation, workspace mounts, auth volumes, network access, tool permissions, resource capture, retention, and risky-action approval.
- [ ] **6.2 Policy editor UI** — add dashboard list/detail/edit surfaces for Omnigent policies with validation, diffing, clone, disable, and rollback behavior.
- [ ] **6.3 Enforcement points** — compile selected policies into host launch, bridge session creation, control actions, resource harvest, outbound boundaries, remediation actions, and checkpoint branches.
- [ ] **6.4 Policy snapshots** — stamp the exact policy version/ref onto workflow runs, bridge sessions, host leases, checkpoints, remediation context, and audit events.
- [ ] **6.5 Approval integration** — route policy-gated risky actions through deterministic policy, optional reviewer/human approval, or denial with durable rationale.
- [ ] **6.6 Policy diagnostics** — make denial reasons and misconfiguration actionable in Create, Workflow Detail, host launch diagnostics, and remediation panels.
- [ ] **6.7 Migration from env-only configuration** — replace or layer over Omnigent environment flags with UI-backed endpoint, host-mode, policy, and profile configuration while preserving local-dev simplicity.

**Done means:** an operator can define, select, audit, and revise Omnigent policies from MoonMind, and every Codex Omnigent run records the exact policy version that governed it.

---

## Milestone 7 — Omnigent Agent Profiles UI 📐

**Goal:** Codex and custom Omnigent agent profiles are customizable from the MoonMind UI.

**Why it matters:** Operators should choose and configure Codex-backed or custom Omnigent agents without editing environment variables or raw YAML for every run. Claude-specific profile support is deferred to Milestone 9.

### Remaining work

- [ ] **7.0 Declarative design first** — create the canonical Omnigent agent-profile model doc, building on `docs/Security/ProviderProfiles.md` and `docs/Omnigent/OmnigentAdapter.md`, before profile CRUD UI.
- [ ] **7.1 Agent/profile data model** — define MoonMind-owned Omnigent agent profiles that reference endpoint, upstream agent id/name, harness, default host mode, Provider Profile/auth-volume refs, policy ref, model/reasoning defaults, workspace defaults, capture policy, and RAG defaults.
- [ ] **7.2 Agent discovery and sync** — list upstream Omnigent agents through `/api/agents`, cache/sync metadata, and show availability, harness compatibility, and health in the dashboard.
- [ ] **7.3 Profile CRUD UI** — create, clone, edit, disable, and delete MoonMind Omnigent agent profiles with validation against the selected endpoint, Codex Provider Profile, host mode, and policy.
- [ ] **7.4 Bundle/custom agent support** — support uploading or referencing Omnigent agent bundles when no upstream agent id exists yet, with artifact-backed provenance.
- [ ] **7.5 Create/schedule/remediation selectors** — make profiles selectable anywhere a runtime/agent is chosen, including normal Create, recurring schedules, branch turns, and remediation workflows.
- [ ] **7.6 Profile smoke validation** — provide a test-profile flow that validates endpoint reachability, agent resolution, Codex auth-volume compatibility, static/on-demand host readiness, policy compilation, and minimal session start.
- [ ] **7.7 Decommission env defaults** — migrate `OMNIGENT_DEFAULT_AGENT_NAME`, launch-profile flags, and related single-default behavior into profile selection while retaining env fallback for bootstrap/local development.

**Done means:** users can manage Codex and custom Omnigent-backed agent choices from MoonMind UI, bind them to Provider Profiles and policies, and select them consistently across workflows, schedules, checkpoints, and remediation.

---

## Milestone 8 — Codex Cutover, Documentation & Compatibility Cleanup 🔧

**Goal:** Finish the Codex cutover by aligning public docs, architecture docs, validation, tests, and compatibility shims with Codex-through-Omnigent as the primary managed-runtime path.

### Remaining work

- [ ] **8.0 Declarative reconciliation first** — confirm `README.md`, `docs/MoonMindArchitecture.md`, `docs/Omnigent/CombinedStackValidationAndRollback.md`, and each per-milestone canonical doc above reflect shipped desired state before publishing the conformance matrix.
- [ ] **8.1 README and architecture repositioning** — update public positioning from direct Codex managed sessions as the product center to Codex through Omnigent host as the primary runtime boundary, while stating clearly that Claude Omnigent parity is deferred.
- [ ] **8.2 Direct Codex compatibility policy** — document which direct Codex managed-session paths remain supported, which are migration shims, what bridge-compatible evidence they must emit, and the conditions for removal.
- [ ] **8.3 Obsolete roadmap and documentation cleanup** — archive or remove old local-only handoffs, superseded Compose-overlay instructions, completed milestone tracking, and incompatible Docker launch guidance.
- [ ] **8.4 Combined-stack validation and rollback** — keep the canonical single-file Compose path current: `COMPOSE_PROFILES`, published host image/tag selection, `/home/app` working directory, static Codex profile startup, on-demand Docker launch, host registration, credential preflight, diagnostics, cleanup, and rollback.
- [ ] **8.5 Release metadata hygiene** — align package versions, license declarations, deployment defaults, and public descriptions with the actual MoonMind plus Omnigent Codex runtime story.
- [ ] **8.6 Codex conformance matrix** — publish a small matrix showing supported combinations of auth mode, host mode, bridge mode, RAG, checkpoint/resume, remediation, and policy/profile UI support for Codex.

**Done means:** the repository presents Codex-through-Omnigent as the primary managed-runtime path, no longer shows completed work as active debt, and gives operators one accurate startup, validation, rollback, compatibility, and support story.

---

## Milestone 9 — Claude Code Omnigent Parity 🔒

**Goal:** Add Claude Code to the Omnigent runtime only after the Codex path has completed its core product and cutover milestones.

**Why it is late:** Codex now has verified OAuth reuse and automatic Omnigent host registration. Splitting focus would slow the bridge, host lifecycle, checkpoint, and product-surface work needed to make that path reliable. Existing direct Claude support remains available, and the static `omnigent-host-claude` Compose slice is already a supported host per `docs/Omnigent/OmnigentHostOAuth.md` and `docs/Omnigent/CombinedStackValidationAndRollback.md` (which the roadmap defers to when the two disagree). This milestone therefore scopes the remaining Claude parity work *beyond* that already-supported static slice rather than re-litigating it.

### Deferred work

- [ ] **9.0 Declarative design reconciliation** — revisit `docs/Omnigent/OmnigentHostOAuth.md`, `docs/ManagedAgents/ClaudeAnthropicOAuth.md`, bridge, checkpoint, RAG, remediation, and UI docs after the Codex contracts stabilize.
- [ ] **9.1 Claude OAuth host binding** — reuse the shared Provider Profile capacity and host-lease framework for the Claude OAuth volume, correct home/config paths, `.claude.json` handling, competing-credential removal, exact-environment `claude auth status`, and credential-generation drain.
- [ ] **9.2 Static Claude host parity hardening** — the static `omnigent-host-claude` Compose slice is already a supported host in the canonical docs; extend it to full Codex parity for automatic registration evidence, readiness checks, diagnostics, restart behavior, and no second login ceremony under the shared host-lease model.
- [ ] **9.3 Profile-aware Claude routing** — resolve Claude Provider Profiles and `claude-native` harness compatibility automatically through `executionProfileRef`, with the same authorization and first-message ordering as Codex.
- [ ] **9.4 On-demand Claude host parity** — add deterministic on-demand launch, workspace/artifact mounts, policy enforcement, cleanup, janitor reconciliation, and real-Docker tests using the shared host lifecycle substrate.
- [ ] **9.5 Bridge and Workflow Detail parity** — prove Claude sessions produce the same normalized chat, approval, resource, diagnostic, and artifact projections as Codex.
- [ ] **9.6 Checkpoint, RAG, and remediation parity** — support host-independent resume/branching, ContextPack delivery and follow-up retrieval, and evidence-based remediation for Claude-backed sessions.
- [ ] **9.7 Claude cutover and conformance** — document direct-Claude compatibility policy, publish a Claude conformance matrix, and update public positioning only after live credentialed verification.

**Done means:** a Settings-created Claude OAuth Provider Profile can launch or bind one policy-controlled Omnigent host, register automatically, run through the same bridge/chat/evidence model as Codex, recover from MoonMind-owned checkpoints, and pass a live conformance matrix without requiring a second login.

---

## Milestone 10 — Pentest De-scoped; External-Egress Safety Gate Retained 🔒

**Goal:** Pentest is not a first-class product feature. Keep only a thin skill/preset over the generic one-shot Container Jobs/workload path, keep it disabled by default and lab-only, and retain the external-target egress gate until the shared Docker/network substrate enforces restricted egress; external targets stay gated until enforcement exists.

**Why the change:** The bespoke runner, scope-governance, provider-lease, heartbeat, settings, and scope-authoring machinery grew into a maintenance surface disproportionate to its value. MoonMind should consume the upstream PentestGPT container through the generic workload launcher rather than carry a separate execution and governance stack. What must not regress is safety: Docker `bridge` alone is not an enforced egress boundary.

### Retained safety gate

- [ ] **10.0 Declarative reconciliation first** — update `docs/Steps/PentestTool.md`, `docs/Security/PentestOperations.md`, and the generic workload/network-policy docs so they describe either the thin-skill disposition or cohesive removal, with external targets still disabled until restricted egress is enforced.
- [ ] **10.1 Thin-skill disposition** — keep only the runner image plus a small skill/preset that launches through the generic workload path and publishes one `security_pentest_report` artifact, or remove the capability and its docs/tests cohesively if the product decision changes.
- [ ] **10.2 Restricted egress boundary for external targets** — implement and document a network-enforced egress boundary as a generic workload/host capability, such as a dedicated network, egress proxy, or firewall sidecar that can reach only approved lab/provider endpoints.
- [ ] **10.3 External-target enablement gate** — fail fast when an operator enables external pentest targets without a validated restricted-egress profile and recorded security review.
- [ ] **10.4 Enforcement tests and diagnostics** — cover egress-denied launches, missing network attachment, approval metadata, and dashboard/runbook warnings.

**Done means:** pentest carries no bespoke first-class product machinery beyond a thin skill over the generic workload path, and external-target runs cannot be enabled unless the deployment has validated restricted egress, explicit approval evidence, and operator-visible diagnostics proving the enforced network posture.

---

## Scope decisions in this refresh

| Theme | Disposition |
| --- | --- |
| Completed dashboard/navigation work | Removed from active milestones and retained only as baseline substrate. |
| Codex OAuth reuse and host registration | Treated as shipped baseline: Settings-created Codex credentials work in Omnigent hosts, shared capacity and durable binding/lease contracts exist, and host registration is live-verified. |
| Docker deployment model | Canonical `docker-compose.yaml` plus `COMPOSE_PROFILES` is the supported static/bootstrap path; separate OAuth-host overlays are obsolete. Managed execution uses deterministic on-demand Docker hosts and should converge with shared workload primitives where compatible. |
| Claude Code through Omnigent | Removed from all near-term acceptance criteria and consolidated into late, gated Milestone 9. |
| Automatic RAG context injection and RAG context packs | Milestone 5, with Omnigent first-message delivery and host-initiated retrieval as the Codex acceptance path. |
| Resume-from-checkpoint and recovery actions | Milestone 3, using the shipped host-independent identity and live-reattach/cold-restore decision primitives. |
| Safety, governance telemetry, and secret lifecycle | Omnipresent acceptance goals plus concrete enforcement in host launch, bridge authorization, policy UI, remediation audit, and checkpoint evidence. |
| Responses API feature parity | Not on the Codex Omnigent critical path unless a concrete integration requires it. |
| PentestGPT external-target safety | Milestone 10; restricted egress is a generic Docker/workload/host substrate property, not a pentest-only feature. |

---

## Priority ordering

| Priority | Milestone | Status | Primary dependency |
| --- | --- | --- | --- |
| 🔴 P0 | 1 — Omnigent Bridge Communication & Workflow Detail Chat | 🚧 Active | Builds on the shipped profile-bound Codex host and registration path |
| 🔴 P0 | 2 — Workflow-Requested Codex Omnigent Host Containers | 🚧 Active | Uses Milestone 1 for durable communication and visibility |
| 🔴 P0 | 3 — Omnigent Host Session Checkpoints, Resume & Branching | 🔧 Partial | Depends on stable bridge evidence and host lifecycle from 1 and 2 |
| 🔴 P0 | 4 — Remediation Workflows & Evidence-Based Repair | 🚧 Active | Depends on 1 and 3 for full power |
| 🟠 P1 | 5 — RAG for Codex Omnigent Host Agents | 🔧 Partial | Depends on basic Codex Omnigent execution and profile selection |
| 🟠 P1 | 6 — Omnigent Policy Management UI | 📐 Designed | Depends on launch and bridge enforcement points |
| 🟠 P1 | 7 — Omnigent Agent Profiles UI | 📐 Designed | Depends on endpoint, host-mode, Provider Profile, and policy data models |
| 🟡 P2 | 8 — Codex Cutover, Documentation & Compatibility Cleanup | 🔧 Partial | Follows P0/P1 stabilization |
| 🔒 Later | 9 — Claude Code Omnigent Parity | 🔒 Gated | Starts only after Codex cutover contracts are stable |
| 🔒 Gate | 10 — Pentest De-scoped; External-Egress Safety Gate Retained | 🔒 Gated | External targets blocked until shared substrate egress enforcement exists |

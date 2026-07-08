# 🌙 MoonMind Roadmap

> Roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**.
>
> Last updated: 2026-07-08

---

## Direction of travel

MoonMind is shifting from owning separate direct Codex CLI and Claude Code managed-session controllers toward using **Omnigent host** as the runtime boundary for Codex, Claude Code, and future harnesses.

The target split is:

- **MoonMind owns** the dashboard, create/edit flows, Temporal orchestration, workflow/run identity, policy selection, checkpoint/resume/branching, remediation, retrieval, durable artifacts, and operator audit evidence.
- **Omnigent host owns** live harness execution, Codex/Claude process lifecycle inside the host environment, host-side workspace resources, live session events, and harness-specific launch details.
- **The MoonMind Omnigent bridge owns** the compatibility boundary between those systems: session creation/attachment, event streaming, Workflow Detail chat projection, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex/Claude managed-session code remains compatibility substrate** until the bridge and host path can fully replace it. New roadmap work should land through the Omnigent host/bridge path or produce evidence compatible with that path.

Completed historical milestones have been removed from the active roadmap. The sections below track only remaining work that materially advances the Omnigent-host direction.

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
- Workflow RAG already has the core ContextPack, gateway/direct transport, Qdrant, multi-collection, overlay, budgeting, and artifact/ref model used by the current managed-session path.
- Dashboard list/detail display modes exist for Workflows and Recurring Schedules, but they are not yet a reusable application-wide pattern.
- The Checkpoint Branch API and persistence model already support branch create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, instruction digest, workspace policy, turn ids, git binding, and remediation-created branches; remaining work is the operator/UI/default-flow and Omnigent runtime handoff.
- The remediation context builder writes a restricted `reports/remediation_context.json` artifact during remediation execution creation; remaining work is Omnigent-specific evidence enrichment, tools, typed actions, and UI.

---

## Milestone 1 — Dashboard Navigation, Sidebars & Full-Page Lists 🚧

**Goal:** Implement appropriate sidebar and full-page list functionality across all major MoonMind pages.

**Why it matters:** Omnigent host makes MoonMind more than a workflow list. Operators need consistent navigation and list/detail behavior for workflows, schedules, skills, manifests/RAG, Omnigent agents, policies, remediations, artifacts, and settings instead of each page inventing its own layout.

### Remaining work

- [ ] **1.1 Shared application shell and information architecture** — replace the header-only route navigation with a responsive sidebar/rail pattern that can group core areas: Workflows, Create, Schedules, Skills, RAG/Manifests, Omnigent Agents, Omnigent Policies, Remediation, Artifacts/Observability, and Settings.
- [ ] **1.2 Reusable list/detail layout primitive** — generalize the current Workflows/Recurring display modes into a shared component that supports full-page table/list, list sidebar, hidden sidebar, remembered selection, and route-owned coercion.
- [ ] **1.3 Page inventory and route mapping** — classify every major dashboard page as list-only, detail-only, list/detail, or settings-section, and document the canonical URL behavior for each.
- [ ] **1.4 Full-page list surfaces for major collections** — add or harden full-page list routes for workflows, recurring schedules, skills, manifests/RAG sources, Omnigent agents, Omnigent policies, remediations, and artifact/report collections.
- [ ] **1.5 Sidebar detail surfaces** — ensure detail pages that benefit from local navigation can show the relevant collection sidebar without losing deep-linking, refresh, or mobile usability.
- [ ] **1.6 Preferences and accessibility** — persist list/sidebar choices per collection, keep keyboard navigation and screen-reader labels correct, and avoid preference cross-talk between unrelated pages.
- [ ] **1.7 UI regression coverage** — add shared layout tests plus representative route tests for workflow, schedule, skill, Omnigent agent, policy, and remediation pages.

**Done means:** an operator can navigate all major MoonMind product areas from one consistent shell, open each major collection as either a full-page list or detail-with-sidebar where appropriate, deep-link to detail pages, refresh safely, and retain per-page display preferences.

---

## Milestone 2 — Omnigent Host Auth Volumes & Runtime Profile Contract 🚧

**Goal:** Codex and Claude Code auth volumes can be used safely and predictably with Omnigent hosts.

**Why it matters:** Omnigent host becomes the unified runtime only if it can consume the same operator-authenticated Codex and Claude credentials that MoonMind currently manages, without leaking raw secrets into workflow payloads or making auth state ambient across runtimes.

### Remaining work

- [ ] **2.1 Auth materialization inventory** — document how Codex and Claude OAuth/API-key auth state is currently created, stored, refreshed, redacted, and mounted by direct MoonMind managed sessions.
- [ ] **2.2 Host auth volume contract** — define a runtime-neutral `AuthVolumeRef`/`CredentialMountRef` contract that maps MoonMind provider profiles to Omnigent host-visible credential volumes.
- [ ] **2.3 Codex host volume support** — make Codex auth volumes available to Omnigent hosts with correct mount paths, ownership, permissions, refresh boundaries, and redaction metadata.
- [ ] **2.4 Claude Code host volume support** — make Claude Code auth volumes available to Omnigent hosts with equivalent lifecycle, isolation, and validation guarantees.
- [ ] **2.5 SecretRef-only durable state** — ensure workflow, checkpoint, bridge, and remediation payloads carry only profile refs, endpoint refs, and auth-volume refs; raw tokens and generated credential bodies must remain launch-boundary only.
- [ ] **2.6 Local compose/dev profile** — add local Docker Compose and documented development configuration that can run MoonMind + Omnigent host with Codex/Claude auth volumes mounted through the supported contract.
- [ ] **2.7 Host auth diagnostics** — expose actionable diagnostics when auth volume creation, mounting, refresh, or runtime detection fails.
- [ ] **2.8 Boundary tests** — cover credential non-leakage, wrong-runtime isolation, revoked/missing auth, volume cleanup, and retry-safe remount behavior.

**Done means:** a user who has authenticated Codex or Claude Code through MoonMind can run the corresponding harness inside an Omnigent host, and MoonMind can prove which profile/ref was used without exposing credential values.

---

## Milestone 3 — MoonMind-Launched Omnigent Host Containers 🚧

**Goal:** MoonMind can launch Omnigent host agent containers as needed.

**Why it matters:** Treating Omnigent only as an already-running external server is not enough for a managed runtime product. MoonMind needs to provision, supervise, and clean up host containers while preserving the boundary that Omnigent owns live harness execution.

### Remaining work

- [ ] **3.1 Host launch model** — define the supported host types MoonMind can launch first: local Docker host container, compose-managed host, and any explicitly supported remote/managed host profile.
- [ ] **3.2 Launch activity/service** — add a MoonMind-owned launch path that creates an Omnigent host container with endpoint refs, auth-volume refs, workspace policy, network policy, labels, and idempotency keys.
- [ ] **3.3 Host registration and readiness** — wait for host registration/heartbeat with the Omnigent server or bridge before session creation, and surface readiness diagnostics in the dashboard.
- [ ] **3.4 Capacity and lease model** — model host slots, active sessions, cooldowns, stale leases, and cleanup so repeated workflows do not orphan host containers or overrun local resources.
- [ ] **3.5 Workspace and artifact mounts** — standardize what is mounted into a host: repository workspace, temporary work area, auth volumes, tool/cache volumes, and artifact handoff paths.
- [ ] **3.6 Lifecycle operations** — support stop, interrupt, terminate, cleanup, and log/diagnostic harvest through typed MoonMind actions.
- [ ] **3.7 Cutover compatibility** — keep direct Codex/Claude managed-session execution available behind feature gates until Omnigent host launch is reliable for those harnesses.
- [ ] **3.8 End-to-end launch tests** — cover launch, registration, session start, cancellation, terminal harvest, cleanup, and retry after MoonMind worker restart.

**Done means:** a workflow can request an Omnigent-backed Codex or Claude run without a manually pre-provisioned host, and MoonMind can launch the host, bind credentials/workspace/policy, observe it, and clean it up.

---

## Milestone 4 — Omnigent Bridge Communication & Workflow Detail Chat 🚧

**Goal:** Omnigent hosts and MoonMind communicate through the Omnigent bridge, and Workflow Detail chat with Omnigent hosts works similarly to other cloud agents.

**Why it matters:** Operators should not care whether a run is backed by a direct managed process, a cloud agent, or an Omnigent host. Workflow Detail should show the conversation, events, approvals, resources, diagnostics, and artifacts through one familiar model.

### Remaining work

- [ ] **4.1 Proxy-mode bridge routes** — implement or complete the MoonMind bridge facade for Omnigent-shaped session, event, stream, agent, and resource routes while proxying to a stock Omnigent server/host.
- [ ] **4.2 Bridge event normalizer** — normalize host/session events into durable MoonMind event records while preserving raw event journals as artifacts.
- [ ] **4.3 Bridge session projection API** — expose `GET /api/omnigent/bridge-sessions/{bridge_session_id}/events` and `/stream` as the canonical Workflow Chat/read-model surface.
- [ ] **4.4 Workflow Detail chat projection** — render Omnigent sent messages, assistant deltas, tool/session events, elicitations, approvals, interrupts, stop events, resource notices, terminal outcomes, diagnostics, and artifact links before falling back to legacy logs.
- [ ] **4.5 Artifact/resource harvesting in chat** — link changed files, diffs, workspace files, session files, snapshots, and capture manifests directly from chat and step detail.
- [ ] **4.6 Failed-launch visibility** — create visible bridge diagnostics and a Chat timeline even when host launch or session creation fails before a normal terminal stream exists.
- [ ] **4.7 Direct Codex compatibility producer** — during migration, have direct Codex managed sessions emit bridge-compatible events so Workflow Detail no longer depends on runtime-specific observability records.
- [ ] **4.8 Conformance and smoke tests** — add fake Omnigent server tests, proxy-mode route tests, event-normalization tests, chat projection tests, and live combined-stack smoke coverage.
- [ ] **4.9 Embedded compatibility mode** 🔒 — implement MoonMind-as-Omnigent-compatible host/server surface only after proxy mode has conformance and live smoke evidence.

**Done means:** an Omnigent-backed workflow produces a Workflow Detail chat experience equivalent to existing cloud-agent conversations, with durable replay and artifact links even after the host container is gone.

---

## Milestone 5 — Checkpoints, Resume & Branching 🚧

**Goal:** The checkpoint system is fully implemented with resume and branching for Omnigent-backed executions and existing local execution paths.

**Why it matters:** Omnigent v1 owns live session execution, but MoonMind still owns durable recovery semantics. Resume and branching must work from MoonMind evidence, not from raw host internals.

### Remaining work

- [ ] **5.1 External-state checkpoint completeness** — ensure every relevant Omnigent boundary captures `externalStateRef`, `idempotencyKey`, `omnigentSessionId`, diagnostics refs, terminal refs, and patch/diff availability metadata.
- [ ] **5.2 Resume-from-checkpoint default flow** — make failed-run recovery default to evidence-gated resume when checkpoint evidence is valid, with clear reasons when resume is unavailable.
- [ ] **5.3 Checkpoint Branch UI and runtime-profile gaps** — connect the existing Checkpoint Branch API to Workflow Detail actions, runtime/profile selection, publish-mode selection, and Omnigent-compatible launch evidence without duplicating branch endpoints.
- [ ] **5.4 Omnigent branch execution** — start a fresh Omnigent session for a branch turn, carrying corrected instructions and validated external-state evidence without mutating the original workflow input.
- [ ] **5.5 Local vs external restore semantics** — split or normalize ambiguous workspace refs so local sandbox paths and provider-owned external-state artifact refs cannot be confused.
- [ ] **5.6 UI flows** — add Workflow Detail actions for resume, retry, branch, compare branch, and inspect checkpoint evidence.
- [ ] **5.7 Replay and idempotency tests** — cover worker restart, Temporal retry, duplicate first-message prevention, checkpoint validation failures, branch duplicate prevention, and unsupported restore attempts.

**Done means:** failed workflows can resume from validated checkpoints by default, operators can intentionally branch with new instructions or runtime/profile settings, and Omnigent external state is handled as MoonMind artifact evidence rather than host-local mutable state.

---

## Milestone 6 — Remediation Workflows & Evidence-Based Repair 🚧

**Goal:** The remediation system is fully implemented, including custom instructions like a normal Create workflow and access to all artifacts needed for diagnosis and repair.

**Why it matters:** Remediation is where checkpoints, artifacts, chat, policies, and Omnigent runtime control come together. It should be an operator-grade workflow, not a special-case retry button.

### Remaining work

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

- [ ] **10.1 README and architecture repositioning** — update public positioning from direct Codex/Claude managed sessions as the product center to Omnigent host as the unified managed runtime boundary, while explaining current compatibility state honestly.
- [ ] **10.2 Direct runtime compatibility policy** — document which direct Codex/Claude managed-session paths remain supported, which are migration shims, and what evidence they must emit while they exist.
- [ ] **10.3 Obsolete roadmap/doc cleanup** — archive or remove old local-only handoffs and completed milestone tracking that no longer guides implementation.
- [ ] **10.4 Combined-stack validation** — keep MoonMind + Omnigent local validation and rollback docs current as the host launch, bridge, auth-volume, and profile flows evolve.
- [ ] **10.5 Release metadata hygiene** — align package versions, license declarations, and public descriptions with the actual MoonMind + Omnigent runtime story.
- [ ] **10.6 Conformance matrix** — publish a small matrix showing supported combinations of runtime/harness, auth mode, host type, RAG, checkpoint/resume, remediation, and policy/profile UI support.

**Done means:** the repository no longer presents completed historical work as active roadmap debt, and operators/contributors can understand the current Omnigent-host product direction from README, docs, tests, and roadmap without reconciling conflicting narratives.

---

## Milestone 11 — Pentest Restricted-Egress Safety Blocker 🔒

**Goal:** External-target pentest workflows remain gated until a real network-enforced restricted-egress boundary exists.

**Why it matters:** `security.pentest.run` discovery is enabled by default, and `pentestgpt-claude-oauth` currently runs on Docker `bridge`. Approved-scope validation is necessary, but it is not network enforcement. External-target enablement must stay blocked until MoonMind can prove the runner can reach only approved lab/provider endpoints.

### Remaining work

- [ ] **11.1 Restricted egress boundary for PentestGPT external targets** — implement and document a dedicated Docker network, egress proxy, or firewall sidecar that can reach only approved lab/provider endpoints before external targets can be enabled.
- [ ] **11.2 External-target enablement gate** — fail fast when an operator enables external pentest targets without a validated restricted-egress profile and recorded security review.
- [ ] **11.3 Enforcement tests and diagnostics** — cover egress-denied launches, missing network attachment, approval metadata, and dashboard/runbook warnings.

**Done means:** external-target pentest runs cannot be enabled unless the deployment has validated restricted egress, explicit approval evidence, and operator-visible diagnostics proving the enforced network posture.

---

## Re-assessed items from the previous roadmap

| Previous theme | New disposition |
| --- | --- |
| Claude Code parity on the direct shared managed-session plane | Reframed as Claude Code via Omnigent host auth volumes, profiles, host launch, bridge events, and Workflow Chat. A parallel direct Claude controller is no longer the primary path. |
| Automatic RAG context injection and RAG context packs | Moved into Milestone 7, with Omnigent first-message delivery and host-initiated retrieval gateway support as the acceptance path. |
| Dashboard artifact browsing and remediation panels | Split across Milestone 1 for reusable navigation/list surfaces, Milestone 4 for Omnigent chat/artifact projection, and Milestone 6 for remediation-specific panels. |
| Resume-from-checkpoint and recovery actions | Consolidated into Milestone 5 as checkpoint resume/branching, with Milestone 6 owning remediation’s custom-instruction branch flow. |
| Safety guardrails, governance telemetry, and secret lifecycle | Embedded into Milestones 2, 3, 6, 8, and 11 as auth-volume boundaries, host launch policy, remediation audit, Omnigent policy enforcement, and PentestGPT restricted egress. Standalone safety work should be added only when it is not tied to those product paths. |
| PentestGPT restricted egress and external target safety | Kept as standalone Milestone 11 because the current Docker `bridge` runner is not a restricted-egress boundary; external targets stay gated until enforcement exists. |
| Deep observability, live logs, and trace/log links | Embedded into Milestones 4, 5, and 6 as bridge chat replay, checkpoint evidence, remediation evidence access, and artifact diagnostics. Full OTel/cost expansion can follow after Omnigent-host execution is stable. |
| Responses API feature parity | Not on the critical path for Omnigent host cutover unless a concrete Omnigent/cloud-agent integration requires it. |
| Completed resiliency, sandbox, memory, vendor portability, and baseline observability milestones | Removed from active roadmap tracking. They remain part of the product substrate and should be documented elsewhere, not tracked as unfinished roadmap items. |

---

## Priority ordering

| Priority | Milestone | Status | Primary dependency |
| --- | --- | --- | --- |
| 🔴 P0 | 1 — Dashboard navigation, sidebars, and full-page lists | 🚧 Active | Can proceed in parallel |
| 🔴 P0 | 2 — Omnigent host auth volumes and runtime profile contract | 🚧 Active | Required for Codex/Claude host parity |
| 🔴 P0 | 3 — MoonMind-launched Omnigent host containers | 🚧 Active | Depends on 2 for credentialed harnesses |
| 🔴 P0 | 4 — Omnigent bridge communication and Workflow Detail chat | 🚧 Active | Required for usable operator experience |
| 🔴 P0 | 5 — Checkpoints, resume, and branching | 🚧 Active | Required for resilient Omnigent execution |
| 🔴 P0 | 6 — Remediation workflows and evidence-based repair | 🚧 Active | Depends on 4 and 5 for full power |
| 🔴 P0 | 11 — Pentest restricted-egress safety blocker | 🔒 Gated | Required before external-target pentest enablement |
| 🟠 P1 | 7 — RAG for Omnigent host agents | 🔧 Partial | Depends on basic Omnigent execution and profile selection |
| 🟠 P1 | 8 — Omnigent policy management UI | 📐 Designed | Depends on launch/bridge enforcement points |
| 🟠 P1 | 9 — Omnigent agent profiles UI | 📐 Designed | Depends on endpoint/profile/policy data model |
| 🟡 P2 | 10 — Cutover, docs, and compatibility cleanup | 🔧 Partial | Follows P0/P1 stabilization |

---

## Near-term implementation sequence

1. **Land the bridge operator slice:** proxy-mode bridge routes, event normalizer, bridge-session projection API, and Workflow Detail chat rendering for Omnigent session events.
2. **Make host launch real:** define auth-volume/profile contracts, launch Omnigent host containers from MoonMind, and validate Codex/Claude auth inside those hosts.
3. **Finish recovery primitives:** complete Omnigent external-state checkpoint evidence, make resume the default failed-run path, and wire the existing Checkpoint Branch API into operator/runtime flows.
4. **Complete remediation authoring:** create remediation workflows from the normal Create flow, enrich the remediation context artifact for Omnigent evidence, expose target artifact/log tools, and route corrective execution through Checkpoint Branches.
5. **Unify operator UI:** generalize sidebar/full-list behavior across all major collection pages while adding Omnigent agent and policy management surfaces.
6. **Restore context parity:** wire Workflow RAG into Omnigent first-message and host-initiated retrieval gateway paths.
7. **Clean up the story:** update README/architecture docs and compatibility guidance once Omnigent-host execution is the preferred path.
8. **Keep pentest external-target gates closed:** do not enable external-target PentestGPT runs until restricted egress, security review, tests, and operator diagnostics exist.

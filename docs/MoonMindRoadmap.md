# 🌙 MoonMind Roadmap

> Roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**, with a **Codex-first cutover** held to the cross-cutting goals of **safety, resilience, and observability**.
>
> The immediate destination is Codex CLI running through profile-bound Omnigent hosts from the normal MoonMind Workflow interface. Claude Code support through Omnigent remains deliberately deferred until the Codex contracts and cutover evidence are stable.
>
> **Document class:** this roadmap is an *imperative execution tracker*. Durable desired state lives in the canonical declarative `docs/` files named by each milestone. When this tracker and a canonical design disagree, the declarative design wins.
>
> Last updated: 2026-07-26

---

## Direction of travel

MoonMind has moved beyond the initial Omnigent plumbing phase. The stock-host bridge, durable event journal, Workflow Detail conversation/evidence projection, profile-bound Codex OAuth host lifecycle, static and on-demand launch modes, normal Workflow Create selection, readiness catalog, runtime compiler, controls, resource harvesting, and direct-Codex compatibility event producer are now shipped substrate.

The remaining Codex critical path is narrower:

1. finish authoritative normal-workflow workspace materialization and publish real browser-to-host acceptance evidence;
2. turn the shipped checkpoint identity and decision primitives into the default resume and Checkpoint Branch product flow;
3. complete operator-grade remediation;
4. add initial and in-session RAG to Omnigent;
5. persist policies and agent profiles and replace declared-only egress with real network enforcement;
6. cut over defaults and retire redundant direct-Codex launch paths without breaking historical reads or Temporal replay;
7. graduate embedded compatibility mode only after stock-host conformance; and
8. add Claude Code parity later without destabilizing the Codex path.

The target ownership split remains:

- **MoonMind owns** Workflow authoring, Temporal orchestration, workflow/run/step identity, Provider Profile selection and capacity, policy/profile selection, canonical workspaces, checkpoint/resume/branching, remediation, retrieval, durable artifacts, publication, and operator audit evidence.
- **Omnigent host owns** the live provider process inside the authorized host environment, host-side session resources, provider/harness interactions, and live upstream events.
- **The MoonMind Omnigent bridge owns** profile-authorized session creation or attachment, event normalization and replay, Workflow Detail projection, controls, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex remains migration compatibility substrate** until the evidence-based cutover in #3518. Historical direct provenance must remain truthful, and explicit Omnigent selection must never silently fall back to direct Codex.
- **The stock proxy topology remains the primary supported acceptance path.** Embedded mode exists but remains experimental until #3519 passes.
- **Claude Code remains outside the current Omnigent critical path.** Existing direct Claude support and the static Claude host slice remain available while #3520 stays gated on the Codex cutover.

Completed historical milestones have been removed from the active roadmap. Milestone numbers below reflect current execution order; the durable acceptance-claim identifiers pinned by documentation contract tests remain stable across renumbering.

---

## Completion and evidence rules

Roadmap status follows evidence, not issue bookkeeping:

- A merged implementation or closed issue may establish useful substrate without satisfying its full acceptance claim.
- A task that requires credentialed, protected, browser-originated, restart, or network-enforcement evidence remains open until that evidence is independently resolvable and linked.
- A workflow file, fake provider, semantic action stub, or caller-supplied expected event list is not live provider proof.
- “Supported” means the support matrix links passing evidence for that exact combination. “Implemented” and “designed” are weaker states.
- Normal product paths must fail closed rather than silently substitute a Provider Profile, host mode, policy, network, credential, runtime, or repository state.
- Issue closure never rewrites historical runtime provenance or removes the obligation to preserve Temporal replay.

---

## Omnipresent goals

Every milestone is additionally gated by these properties:

- **Safety.** Credential, filesystem, network, publish, approval, and control boundaries are enforced at trusted substrate boundaries. Workflows and hosts receive capabilities, refs, and immutable snapshots—not raw infrastructure authority or reusable credential bodies.
- **Resilience.** Runs prefer idempotent retry, evidence-gated resume, branch isolation, bounded degraded mode, and durable cleanup over silent restart-from-scratch. Provider Profiles, billing-relevant settings, and constraints are never silently substituted.
- **Observability.** Live state, terminal outcomes, denials, degraded behavior, artifacts, cleanup, and recovery decisions are inspectable through MoonMind-owned bridge projections, artifacts, manifests, audit events, and telemetry rather than a second-source runtime dashboard.

---

## Declarative design first

Each milestone begins by reconciling the canonical declarative documents that own its target-state contracts. Implementation that discovers drift ends with a documentation reconciliation pass. This roadmap tracks execution and evidence; it is not the sole architecture specification.

---

## Status tags

| Tag | Meaning |
| --- | --- |
| 🚧 Active | Primary implementation track |
| 🔧 Partial | Important substrate exists, but the complete product path is unfinished |
| 📐 Designed | Target state or a narrow built-in implementation exists, but persistent product management is unfinished |
| 🧪 Evidence gate | Implementation exists; required production-shaped or protected evidence does not |
| 🔒 Gated | Intentionally waits on another milestone |

---

## Baseline substrate retained from completed work

These are shipped assumptions, not active milestones:

- The dashboard application rail, collection sidebars, Workflow/Recurring detail frame, responsive behavior, accessibility foundations, and shared list/detail patterns are product substrate.
- Omnigent uses the canonical external-agent identity `agentKind=external`, `agentId=omnigent`; Omnigent-specific authored values remain under `parameters.omnigent`.
- `integration.omnigent.execute` can create or reattach to a session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult`.
- `omnigent_bridge_sessions` is the canonical durable session/authorization/event index. Raw and normalized event evidence remains artifact-backed.
- The bridge facade, event normalization, cursor/page/SSE projection, Workflow Detail chat/lifecycle projection, resource links, failed-launch visibility, and runtime-neutral controls are implemented for the Codex Omnigent path.
- Direct Codex managed sessions emit incremental bridge-compatible events with explicit `codex_direct_compat` provenance. This is temporary migration substrate, not an Omnigent identity.
- The Settings OAuth flow creates or reuses the Codex auth volume, validates credential state, and registers a Provider Profile with shared purpose-aware capacity.
- Direct Codex, Omnigent execution, OAuth validation/repair, and related consumers use the same Provider Profile capacity ledger. The mutable OAuth identity cannot be consumed concurrently by competing execution substrates.
- Profile authorization, provider leases, host bindings, host leases, credential generations, lifecycle transitions, and redacted preflight evidence are durable without placing credential bodies in Temporal, bridge, checkpoint, workspace, or artifact payloads.
- `executionProfileRef` is routed through the profile-bound coordinator, which persists authorization before session creation, starts or checks the exact host, records host/session identity before the first message, and releases Provider Profile capacity only after host cleanup.
- Built-in versioned Codex execution and launch policy definitions support static Compose and deterministic on-demand Docker selection. Normal Workflow Create exposes Codex via Omnigent only when deployment, policy, backend, host, and Provider Profile readiness permit it.
- The normal Workflow request compiler emits canonical `external/omnigent` execution, immutable input evidence, selected Provider Profile, execution profile, launch policy, and Omnigent parameters without manual host IDs or raw JSON editing.
- Static and on-demand hosts use complete image references, UID/GID `1000:1000`, `/home/app`, separate provider OAuth and Omnigent state, read-only root filesystems, bounded temporary storage, deterministic ownership labels, and explicit cleanup.
- Host lifecycle controls, terminal harvest, cleanup evidence, credential-generation drain, and janitor reconciliation exist for expired, missing, orphaned, or stale-generation hosts.
- The generic workload plane supplies canonical `WorkspaceLocator` semantics, daemon-visible resolution, bounded and redacted process output, runtime diagnostics, declared-output manifests, cancellation, and cleanup primitives. The Omnigent path has adopted part of this substrate but still has the completion work tracked by #3507.
- The Codex OAuth host implements MoonMind-managed credential reuse and automatic Omnigent-server registration. Per the evidence rules above this remains **unverified substrate**: no independently resolvable, secret-scanned live-run artifact is linked here, so the behavior stays open until #3508 publishes that proof.
- Host-independent checkpoint identity, split session/workspace authority, credential-generation validation, and live-reattach versus cold-restore decisions exist. The coordinator exposes recovery and branch methods, but production orchestration remains #3509 and #3510.
- The run workflow records per-step Omnigent identity, so Omnigent checkpoint captures select the `external_state_ref` lane.
- The Checkpoint Branch API and persistence model already support create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, immutable instruction digests, workspace policy, git binding, and remediation-created branches.
- Remediation uses normal Workflow identity, creates restricted context artifacts, has an authority/action catalog, and preserves cumulative workspace progress across attempts. Normal Create draft consumption, Omnigent evidence/tools/actions, and release-grade UI/verification remain #3511 and #3512.
- Workflow RAG already has `ContextPack`, gateway/direct transport, Qdrant, multi-collection search, run-scoped overlays, budgets, filters, artifact refs, and prompt-injection safety framing on the direct managed-session path.
- The current Omnigent policy and agent inventories are read-only projections over built-in/runtime data. Persistent immutable policy versions and reusable agent profiles remain #3515 and #3517.
- An embedded compatibility implementation and narrow upstream-auth adapter exist, but embedded mode remains experimental until the stock-host matrix in #3519 passes.
- The live-conformance runner, protected-workflow scaffolding, immutable image inputs, evidence schemas, and secret scanning exist. The prior closed gate issues did not produce the required complete credentialed browser-originated matrix, which remains #3508.

---

## Durable acceptance-claim identifiers

These exact identifiers are pinned by `tests/unit/docs/test_final_docs_cleanup_policy.py` and `tests/integration/docs/test_final_docs_cleanup_contract.py`. They remain stable even when active milestones are renumbered:

- [ ] **5.1 Checkpoint boundary and completeness** — tracked by #3509 in Milestone 2.
- [ ] **5.4 Resume-from-checkpoint default flow** — tracked by #3510 in Milestone 2.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — tracked by #3510 in Milestone 2.
- [ ] **6.2 Omnigent remediation context enrichment** — tracked by #3511 in Milestone 3.
- [ ] **7.1 Initial context injection for Omnigent** — tracked by #3513 in Milestone 4.
- [ ] **11.1 Restricted egress boundary for PentestGPT external targets** — tracked by #3516 in Milestone 5 and retained as a cross-project safety gate.

Changing an identifier above is a deliberate owner-approved invariant change. Update the pinning contract tests in the same change rather than deleting an identifier to make a roadmap edit pass.

---

## Milestone 1 — Complete the Normal Codex Product Path and Prove It 🚧 🧪

**Goal:** A normal UI-authored repository workflow executes in the exact authorized workspace through a policy-selected stock Codex Omnigent host, and a protected browser-to-host matrix proves the complete lifecycle.

**Why it remains open:** The ordinary selection, readiness, runtime compilation, host lifecycle, bridge projection, controls, and evidence plumbing are implemented. The merged workspace-convergence work explicitly left repository/branch/attachment/checkpoint materialization and some shared runtime primitives incomplete, and prior conformance issues closed without the required protected live run.

### Remaining work

- [ ] **1.0 Declarative reconciliation** — update the Create-to-host, workspace, adapter, host OAuth, combined-stack validation, and managed/external execution docs to match the completed implementation and the exact remaining authority boundaries.
- [ ] **1.1 Authoritative normal-workflow workspace and host lifecycle** — complete repository, branch, attachment, Skill/tool, checkpoint/external-state, publication, output-manifest, diagnostics, partial-start reconciliation, static/on-demand parity, and shared-runtime behavior in #3507.
- [ ] **1.2 Real browser-to-host acceptance matrix** — run `/workflows/new` through a real enrolled Codex OAuth profile and stock host, covering static, restart/replay, on-demand, repository read/mutation, failure, cancellation, cleanup, and janitor evidence in #3508.
- [ ] **1.3 Release linkage** — link the passing matrix from #3448, the roadmap, combined-stack validation, and readiness/rollout gates; keep the product path gated when qualifying evidence is missing or stale.

**Done means:** a browser-originated normal Workflow request materializes the authored repository state, reaches the exact policy/profile-bound stock host, posts the first message once, produces durable Workflow Detail and artifact evidence, cleans only owned resources, releases Provider Profile capacity last, and passes the independently resolvable protected matrix.

---

## Milestone 2 — Host-Independent Checkpoint, Resume, and Branching 🔧

**Goal:** Failed Codex Omnigent work resumes from validated MoonMind-owned evidence by default, whether the original host survives or must be replaced, and corrected instructions execute through isolated Checkpoint Branches.

### Remaining work

- [ ] **2.0 Declarative reconciliation** — update `docs/Steps/StepExecutionsAndCheckpointing.md`, `docs/Workflows/CheckpointBranchSystem.md`, and Omnigent adapter docs for split session/workspace/host authority, complete manifests, and production recovery orchestration.
- [ ] **2.1 Complete checkpoint capture and restore evidence** — publish and validate the full session, workspace, host, profile, policy, lineage, cursor, first-message, artifact, and credential-generation manifest in #3509.
- [ ] **2.2 Evidence-gated default resume** — wire production orchestration to choose safe live reattach, cold restore, branch-required, or explicit unavailable outcomes in #3510.
- [ ] **2.3 Omnigent Checkpoint Branch execution and UI** — use the existing branch APIs for isolated new host/session turns, immutable corrected instructions, profile/policy/publish selectors, compare, promote, and archive in #3510.
- [ ] **2.4 Replay and failure proof** — cover worker restart, Temporal retry/replay, stale generations, duplicate first-message prevention, partial artifacts, capacity contention, cancellation, cleanup, and duplicate branch suppression.

**Done means:** the original host is optional, credentials never enter checkpoint artifacts, recovery mode is evidence-derived and operator-visible, retries remain idempotent, and changed instructions or runtime choices always produce a branch rather than mutating original workflow input.

---

## Milestone 3 — Operator-Grade Remediation 🚧

**Goal:** Operators author remediation through normal Create, remediators receive complete bounded Omnigent evidence, repairs use typed policy-bound actions and Checkpoint Branches, and every action is verified and auditable.

### Remaining work

- [ ] **3.0 Declarative reconciliation** — update `docs/Workflows/WorkflowRemediation.md` and `docs/Workflows/RemediationVerificationCadence.md` for normal authoring, Omnigent evidence, executable action adapters, cumulative attempts, approvals, verification, and rollout.
- [ ] **3.1 Authoring, context, evidence tools, and typed repair actions** — consume the stored remediation draft, enrich `reports/remediation_context.json`, add bounded artifact/event/log tools, execute allowed Omnigent controls, and require branches for changed instructions in #3511.
- [ ] **3.2 Product UI, approvals, verification, audit, and loop prevention** — complete target/remediator panels, durable approvals, fresh-evidence verification, locks, cooldowns, cumulative no-progress detection, prevention reporting, and operator acceptance proof in #3512.
- [ ] **3.3 Autonomous remediation gate** — keep automatic or scheduled mutation disabled until #3512’s operator-driven matrix, policy enforcement, action verification, conflict controls, telemetry, and cancellation gates pass.

**Done means:** an operator can understand exactly what failed, what evidence was available, what authority was granted, what changed, whether the target actually recovered, what prevention work remains, and why autonomous action is or is not permitted.

---

## Milestone 4 — RAG for Codex Omnigent Sessions 🔧

**Goal:** Moving Codex behind Omnigent does not reduce context quality or expose retrieval infrastructure credentials.

### Remaining work

- [ ] **4.0 Declarative reconciliation** — update `docs/Rag/WorkflowRag.md` and Omnigent first-message/tool docs for initial context, in-session retrieval, scoped capability exchange, budgets, evidence, degraded behavior, and authoring.
- [ ] **4.1 Initial `ContextPack` injection** — resolve and persist retrieval before first-message commitment, safely frame bounded context, reuse it across retry, and link it to the exact first-message digest in #3513.
- [ ] **4.2 Scoped in-session retrieval** — add a host/session-bound MoonMind retrieval capability, server-enforced scope and budgets, durable result evidence, explicit delivery semantics, and controls across Create, schedules, profiles, branches, and remediation in #3514.

**Done means:** Omnigent sessions receive artifact-backed initial context and can request bounded follow-up context without raw embedding, Qdrant, artifact-store, or general MoonMind credentials; every result, denial, fallback, budget, and delivery outcome is observable.

---

## Milestone 5 — Persistent Policies, Enforced Egress, and Agent Profiles 📐

**Goal:** Operators manage reusable Omnigent policy and agent-profile versions from MoonMind, and selected network constraints are actually enforced rather than merely declared.

### Remaining work

- [ ] **5.0 Declarative model reconciliation** — create the canonical policy and agent-profile documents and reconcile Settings, Provider Profile, adapter, workspace, checkpoint, remediation, RAG, and observability contracts.
- [ ] **5.1 Persistent versioned policies** — add immutable policy versions, validation, compilation, snapshots, approvals, CRUD/version UI, diagnostics, and environment-default migration in #3515.
- [ ] **5.2 Real restricted-egress enforcement** — implement and attest a network-layer enforcement backend with DNS/IP/IPv6/redirect/bypass protections, fail-closed readiness, evidence, and negative conformance tests in #3516.
- [ ] **5.3 Persistent agent profiles and upstream sync** — add immutable profile versions, upstream agent discovery, custom bundle provenance, readiness validation, CRUD UI, and selectors across every authoring surface in #3517.

**Done means:** every run records the exact policy and agent-profile versions that governed it; the runtime realizes those snapshots without silent widening; and `enforcedNetworkRefs` represent verified backend enforcement rather than a configured Docker network.

---

## Milestone 6 — Codex Cutover and Direct-Runtime Retirement 🔧

**Goal:** Make Codex through Omnigent the primary supported runtime through a staged, reversible, evidence-gated rollout, then retire redundant direct launch code without breaking historical evidence or in-flight workflows.

### Remaining work

- [x] **6.0 Compatibility and cutover design reconciliation** — the canonical [cutover policy](Omnigent/CodexCutoverPolicy.md) classifies every direct-Codex surface and defines evidence-based retirement.
- [x] **6.1 Versioned support matrix** — the versioned [Codex support matrix](Omnigent/CodexSupportMatrix.md) distinguishes implemented substrate from combinations backed by independently resolvable protected-live evidence.
- [ ] **6.2 Staged default and telemetry rollout** — move cohorts, Create defaults, schedules, and presets only when readiness, live evidence, and objective rollback thresholds pass.
- [ ] **6.3 Historical and Temporal compatibility** — preserve truthful direct provenance, Workflow Detail reads, schema decoders, recorded histories, and mixed-version worker replay.
- [ ] **6.4 Controlled retirement** — disable direct scheduling before removing launch/UI/configuration code; retain the compatibility event/read model until its explicit history and rollback gates pass.
- [x] **6.5 Public documentation and release-metadata reconciliation** — README, architecture, cutover policy, versioned support matrix, conformance and release notes share the evidence-gated Codex story; Claude parity remains deferred.

**Done means:** the repository tells one accurate Codex-through-Omnigent story, every supported row has evidence, explicit Omnigent selection never silently falls back, rollback remains available during migration, and direct code is removed only after historical and in-flight contracts are safe.

---

## Milestone 7 — Embedded Compatibility Mode Graduation 🔒

**Goal:** Support MoonMind’s embedded Omnigent-compatible surface only after an unmodified stock host passes the real auth, registration, session, restart, rotation, replay, and rollback matrix.

### Gated work

- [ ] **7.0 Declarative compatibility reconciliation** — version the supported upstream protocol/auth contract and preserve strict separation between MoonMind user auth and Omnigent host auth.
- [ ] **7.1 Stock-host embedded conformance** — prove fresh registration, restart/reconnect, credential rotation/revocation, session/events/resources/controls, static/on-demand behavior where supported, failure cases, and immutable evidence in #3519.
- [ ] **7.2 Rollout and upgrade discipline** — keep proxy mode default until evidence passes; pin compatible upstream versions, require conformance on upgrade, and preserve a tested rollback version.

**Done means:** embedded mode is supported because a stock host proved compatibility—not because MoonMind has an implementation—and no host fork, browser credential forwarding, second provider login, or silent proxy/embedded substitution is required.

---

## Milestone 8 — Claude Code Omnigent Parity 🔒

**Goal:** Reuse the stable Codex-era provider-neutral contracts for Claude only after the Codex cutover is complete.

### Deferred work

- [ ] **8.0 Declarative reconciliation after Codex cutover** — identify shared contracts and genuine Claude-specific OAuth, home/config, harness, event, and recovery differences.
- [ ] **8.1 Profile-bound Claude host lifecycle** — reuse Provider Profile capacity, host leases, credential generations, static/on-demand lifecycle, workspace, policy, egress, cleanup, and janitor substrate in #3520.
- [ ] **8.2 Routing, bridge, Workflow Detail, checkpoint, RAG, and remediation parity** — expose only supported capabilities with truthful Claude provenance and explicit degraded behavior.
- [ ] **8.3 Credentialed Claude support matrix and cutover policy** — prove normal UI-originated static/on-demand work, auth reuse, restart/replay, recovery, retrieval, remediation, and direct-Claude compatibility before changing defaults.

**Done means:** a Settings-created Claude OAuth Provider Profile runs through the shared policy-bound Omnigent model without a second login or provider-specific product architecture, and every advertised row has real evidence.

---

## Retained Pentest disposition and shared safety gate

Pentest remains de-scoped as a first-class product area. Any retained capability should be a thin skill or preset over the generic workload path, remain disabled by default and lab-oriented, or be removed cohesively with its docs and tests.

That disposition is a target state, not the current tree, so two concrete items stay open and must not be treated as closed by roadmap editing alone:

- [ ] **Superseded Pentest stack disposition** — `moonmind/integrations/pentest/`, `moonmind/workflows/temporal/activities/pentest_activities.py`, `PentestSettings`, Pentest submission validation, and Pentest provider-lease machinery are still first-class surfaces. Replace them with a thin skill or preset over the generic workload path, or remove them cohesively with their docs and tests.
- [ ] **External-target enablement fail-fast** — `PentestSettings.allow_external_targets` defaults to `True`, and an `external_authorized` scope is rejected only when that setting is false, so a manually approved external target can still launch on unrestricted Docker `bridge`. Make external-target enablement fail closed until #3516 attests validated restricted egress.

The cross-project dependency retained in this roadmap is #3516: Docker `bridge` or a declared network ref is not restricted egress, and external targets stay gated until enforcement exists. That states the required target state rather than current runtime behavior; the fail-fast item above is the work that makes the gate real. Even after enforcement exists, external-target work requires an explicit reviewed egress profile, target scope, approval evidence, and operator-visible diagnostics.

---

## Remaining issue map

| Priority | Issue | Scope |
| --- | --- | --- |
| 🔴 P0 | #3507 | Complete normal-workflow workspace materialization and shared host lifecycle |
| 🔴 P0 gate | #3508 | Publish the real browser-to-host Codex acceptance matrix |
| 🔴 P0 | #3509 | Complete checkpoint capture and host-independent restore evidence |
| 🔴 P0 | #3510 | Wire evidence-gated resume and Checkpoint Branch execution into Workflow Detail |
| 🔴 P0 | #3511 | Complete remediation authoring, Omnigent context, evidence tools, and typed actions |
| 🔴 P0 gate | #3512 | Finish remediation UI, verification, audit, and controlled rollout |
| 🟠 P1 | #3513 | Inject initial MoonMind `ContextPack` evidence into Omnigent |
| 🟠 P1 | #3514 | Add scoped in-session retrieval, budgets, evidence, and controls |
| 🟠 P1 | #3515 | Persist and manage versioned Omnigent policies |
| 🟠 P1 security | #3516 | Enforce restricted egress for hosts and workloads |
| 🟠 P1 | #3517 | Add persistent agent profiles, upstream discovery, and selectors |
| 🟡 P2 | #3518 | Complete Codex cutover, support matrix, and direct-runtime retirement |
| 🔒 Later | #3519 | Graduate embedded compatibility mode with stock-host conformance |
| 🔒 Later | #3520 | Add Claude Code parity after the Codex cutover |

The two retained Pentest items — superseded-stack disposition and external-target enablement fail-fast — have no tracking issue yet. File them before treating this table as the complete remaining-work inventory; completing every issue above still leaves that work undone.

---

## Priority order and dependencies

| Order | Milestone | Status | Primary dependency |
| --- | --- | --- | --- |
| 1 | Normal Codex product path and protected acceptance | 🚧 🧪 | Shipped Create/bridge/host substrate; #3507 before the complete #3508 matrix |
| 2 | Checkpoint, resume, and branching | 🔧 | Complete workspace materialization and checkpoint evidence |
| 3 | Operator-grade remediation | 🚧 | Checkpoint/recovery and branch execution |
| 4 | Omnigent RAG | 🔧 | Stable first-message and host/session boundaries |
| 5 | Policies, enforced egress, and agent profiles | 📐 | Existing built-in policy/readiness substrate; #3515 before profile and egress integration |
| 6 | Codex cutover | 🔧 | Passing product, recovery, remediation, RAG, policy/profile, egress, and live-evidence gates |
| 7 | Embedded mode | 🔒 | Stock proxy acceptance and Codex cutover |
| 8 | Claude parity | 🔒 | Stable provider-neutral Codex contracts and cutover |

---

## Scope decisions in this refresh

| Theme | Disposition |
| --- | --- |
| Bridge, event normalization, Workflow Detail chat/resources/controls | Removed from active milestones and retained as shipped baseline. |
| Normal Create selection, readiness, runtime compilation, static/on-demand host selection | Removed from active feature construction; remaining authority gaps and live proof are #3507 and #3508. |
| Closed conformance issues without their required protected artifacts | Treated as partial infrastructure, not completed acceptance. |
| Direct Codex bridge-compatible event streaming | Shipped migration substrate; retirement belongs to #3518. |
| Checkpoint identity and split session/workspace authority | Shipped substrate; complete capture and production orchestration are #3509 and #3510. |
| Remediation cumulative workspace and generic authority model | Shipped substrate; authoring/evidence/actions and product rollout are #3511 and #3512. |
| RAG | Split into deterministic initial context (#3513) and scoped in-session retrieval (#3514). |
| Policy management | Current built-ins/read-only inventory remain substrate; persistent immutable product management is #3515. |
| Network safety | A declared `enforcedEgress` flag is not enforcement; the real substrate is #3516. |
| Agent profiles | Current built-in execution profile/read-only inventory remain substrate; persistent profiles and sync are #3517. |
| Codex cutover | Evidence-gated staged migration and direct-runtime retirement are consolidated in #3518. |
| Embedded mode | Implementation exists but remains experimental until #3519. |
| Claude Code through Omnigent | Consolidated into gated #3520 after Codex cutover. |
| PentestGPT | De-scoped as a product area, but the superseded first-class stack and the external-target fail-fast gate remain tracked alongside the shared restricted-egress dependency (#3516). |

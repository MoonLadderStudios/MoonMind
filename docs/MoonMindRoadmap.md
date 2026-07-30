# 🌙 MoonMind Roadmap

> Roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**, with a **Codex-first cutover** held to the cross-cutting goals of **safety, resilience, and observability**.
>
> The immediate destination remains Codex CLI running through profile-bound Omnigent hosts from the normal MoonMind Workflow interface. Claude-through-Omnigent work now has useful early substrate, but it remains outside the supported critical path until the Codex contracts and evidence gates are stable.
>
> **Document class:** this roadmap is an *imperative execution tracker*. Durable desired state lives in the canonical declarative `docs/` files named by each milestone. When this tracker and a canonical design disagree, the declarative design wins.
>
> Last updated: 2026-07-29

---

## Direction of travel

MoonMind has moved well beyond initial Omnigent plumbing. The stock-host bridge, durable event journal, Workflow Detail conversation/evidence projection, profile-bound Codex OAuth lifecycle, static and on-demand launch modes, normal Workflow Create selection, readiness catalog, runtime compiler, controls, resource harvesting, and direct-Codex compatibility event producer are shipped substrate.

The July 27–29 implementation wave also landed several major foundations:

- a complete versioned Omnigent checkpoint manifest, reference-only restore validation, cold-restore materialization, and separate recovery-capability projections in #3509 / PR #3554;
- initial first-message `ContextPack` injection in #3513 / PR #3545;
- a persistent immutable policy model and policy evidence foundation in #3515 / PR #3546;
- a persistent agent-profile model, API, bundle validation, and inventory projection in #3517 / PR #3547;
- a versioned Codex cutover phase machine, machine-readable support rows, digest-bound promotion evidence, and fail-closed promotion rules in #3518 / PR #3548;
- stronger embedded-mode readiness, diagnostics, and evidence validation in #3519 / PR #3549;
- early Claude profile, host, and recovery substrate in #3520 / PR #3550;
- bounded remediation authoring, context, read tools, and typed action foundations in #3511 / PR #3544;
- a scoped retrieval-capability, budget, accounting, delivery-result, and revocation foundation in #3514 / PR #3552; and
- managed-session and workflow-failure reliability fixes in PRs #3553 and #3556.

Those merges materially narrow the work, but several PRs explicitly ended with `ADDITIONAL_WORK_NEEDED` or blocked verification. They are therefore recorded as implementation substrate rather than completed acceptance.

The current Codex critical path is:

1. complete authoritative normal-workflow workspace materialization in #3507;
2. run and retain the real credentialed browser-to-stock-host matrix in #3508;
3. make the shipped checkpoint evidence drive the default resume and Checkpoint Branch product flow in #3510;
4. finish operator-grade remediation in #3512 and close the residual authority gaps left by #3544;
5. complete production in-session RAG delivery and authoring in #3514;
6. finish policy consumption, agent-profile product journeys, and restricted-egress enforcement and proof;
7. execute the evidence-gated Codex rollout and preserve historical/Temporal compatibility in #3518; and
8. graduate embedded mode and Claude parity only through new or reopened evidence-complete trackers.

The target ownership split remains:

- **MoonMind owns** Workflow authoring, Temporal orchestration, workflow/run/step identity, Provider Profile selection and capacity, policy/profile selection, canonical workspaces, checkpoint/resume/branching, remediation, retrieval, durable artifacts, publication, and operator audit evidence.
- **Omnigent host owns** the live provider process inside the authorized host environment, host-side session resources, provider/harness interactions, and live upstream events.
- **The MoonMind Omnigent bridge owns** profile-authorized session creation or attachment, event normalization and replay, Workflow Detail projection, controls, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex remains migration compatibility substrate** while the deployed cutover phase remains `opt_in`. Historical direct provenance must remain truthful, and explicit Omnigent selection must never silently fall back to direct Codex.
- **The stock proxy topology remains the primary supported acceptance path.** Embedded mode remains experimental despite the merged compatibility foundation.
- **Claude Code remains outside the current Omnigent critical path.** Existing direct Claude support remains available; the merged Claude-through-Omnigent foundation is not a support claim.

Completed historical milestones have been removed from the active roadmap. Milestone numbers below reflect current execution order; the durable acceptance-claim identifiers pinned by documentation contract tests remain stable across renumbering.

---

## Completion and evidence rules

Roadmap status follows evidence, not issue bookkeeping:

- A merged implementation or closed issue may establish useful substrate without satisfying its full acceptance claim.
- A PR whose own verifier reports `ADDITIONAL_WORK_NEEDED`, `BLOCKED`, missing live proof, or unexecuted controlling tests does not close the corresponding roadmap gate.
- A task that requires credentialed, protected, browser-originated, restart, network-enforcement, or provider-conformance evidence remains open until that evidence is independently resolvable and linked.
- A workflow file, fake provider, semantic action stub, self-asserted passing field, or caller-supplied expected event list is not live provider proof.
- “Supported” means the support matrix links passing evidence for that exact combination. “Implemented,” “foundation,” and “designed” are weaker states.
- Normal product paths must fail closed rather than silently substitute a Provider Profile, host mode, policy, network, credential, runtime, repository state, checkpoint, or retrieval scope.
- Issue closure never rewrites historical runtime provenance or removes the obligation to preserve Temporal replay.
- A closed tracker with known residual work must be reopened or receive a follow-up issue before the roadmap can treat the acceptance claim as owned.

---

## Omnipresent goals

Every milestone is additionally gated by these properties:

- **Safety.** Credential, filesystem, network, publish, approval, retrieval, and control boundaries are enforced at trusted substrate boundaries. Workflows and hosts receive capabilities, refs, and immutable snapshots—not raw infrastructure authority or reusable credential bodies.
- **Resilience.** Runs prefer idempotent retry, evidence-gated resume, branch isolation, bounded degraded mode, and durable cleanup over silent restart-from-scratch. Provider Profiles, billing-relevant settings, constraints, and checkpoint authority are never silently substituted.
- **Observability.** Live state, terminal outcomes, denials, degraded behavior, artifacts, cleanup, recovery decisions, retrieval delivery, and rollout state are inspectable through MoonMind-owned projections, manifests, audit events, and telemetry rather than a second-source runtime dashboard.

---

## Declarative design first

Each milestone begins by reconciling the canonical declarative documents that own its target-state contracts. Implementation that discovers drift ends with a documentation reconciliation pass. This roadmap tracks execution and evidence; it is not the sole architecture specification.

---

## Status tags

| Tag | Meaning |
| --- | --- |
| 🚧 Active | Primary implementation track |
| 🔧 Partial | Important substrate exists, but the complete product path is unfinished |
| 🧪 Evidence gate | Implementation exists; required production-shaped, provider, protected, or controlling evidence does not |
| 📐 Designed | Target state or narrow implementation exists, but persistent product management is unfinished |
| 🔒 Gated | Intentionally waits on another milestone |
| ✅ Implemented slice | A bounded implementation claim is complete, though a broader milestone may remain open |

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
- The Codex OAuth host implements MoonMind-managed credential reuse and automatic Omnigent-server registration. The protected live support claim remains open until #3508 publishes the complete matrix.
- The run workflow records per-step Omnigent identity, so Omnigent checkpoint captures select the `external_state_ref` lane.
- `OmnigentCheckpointIdentity` v2 now carries complete lineage, session, host, profile, policy, first-message, event-cursor, capture, artifact, workspace, branch, and publication evidence; restore validation rejects stale, mismatched, non-artifact, local-path, or credential-shaped authority.
- Cold restore now compiles validated authority-only material for the canonical workspace boundary, while live session reattach, workspace cold restore, and branch creation remain independently projected capabilities.
- The Checkpoint Branch API and persistence model already support create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, immutable instruction digests, workspace policy, git binding, and remediation-created branches.
- Remediation uses normal Workflow identity, consumes stored Create drafts, creates restricted context artifacts, exposes bounded evidence readers, has a typed authority/action catalog, and preserves cumulative workspace progress across attempts. Target-authorized cleanup and release-grade UI/verification remain active work.
- Initial Omnigent `ContextPack` resolution, persistence, first-message digest binding, retry reuse, Step Execution linkage, and bridge lifecycle projection are implemented; the controlling verification rerun remains an evidence gate.
- Follow-up retrieval has a session-bound capability, immutable scope and budget model, accounting, typed result/delivery state, and lifecycle revocation foundation. Production host invocation and complete authoring/control integration remain #3514.
- Persistent immutable Omnigent policy versions, authenticated lifecycle APIs, effective-launch linkage, and bridge policy evidence now exist. Complete cross-boundary consumption, approval, ownership, activation-impact, and product-management journeys remain unfinished.
- Persistent immutable Omnigent agent-profile versions, authenticated APIs, bundle validation, default-runtime inventory, and a read-only UI projection now exist. Full CRUD/version UX, synchronization, smoke validation, and authoring selectors remain #3517.
- `docs/Omnigent/CodexSupportAndCutover.md` now defines a versioned six-phase rollout, truthful direct-runtime compatibility inventory, stable support-row catalog, digest-bound promotion document, objective thresholds, rollback, and fail-closed phase promotion. The deployed phase remains `opt_in`.
- Embedded compatibility now has stronger per-mode readiness, diagnostics, evidence validation, and Workflow Detail projection, but remains experimental because the stock-host credentialed matrix is absent.
- Claude-through-Omnigent now has early profile/host/recovery and declarative substrate, but provider-proven lifecycle, cold recovery, branch, RAG, remediation, UI, and non-regression evidence remain incomplete.
- Managed-session recovery now preserves authoritative failures, uses canonical nested snapshots, and replaces broken container records after repeated locator mismatch. Checkpointless dynamic remediation now stops through a structured remaining-work gate instead of crashing or inventing unsafe resume authority.
- The live-conformance runner, protected-workflow scaffolding, immutable image inputs, evidence schemas, secret scanning, and support/cutover evidence builder exist. The required credentialed browser-originated support matrix still does not.

---

## Durable acceptance-claim identifiers

These exact identifiers are pinned by `tests/unit/docs/test_final_docs_cleanup_policy.py` and `tests/integration/docs/test_final_docs_cleanup_contract.py`. They remain stable even when active milestones are renumbered:

- [ ] **5.1 Checkpoint boundary and completeness** — implementation foundation landed in #3509 / #3554; independently resolvable acceptance evidence remains required.
- [ ] **5.4 Resume-from-checkpoint default flow** — tracked by #3510 in Milestone 2.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — tracked by #3510 in Milestone 2.
- [ ] **6.2 Omnigent remediation context enrichment** — #3511 / #3544 landed substantial implementation, but known target-authority gaps and release evidence remain in Milestone 3.
- [ ] **7.1 Initial context injection for Omnigent** — implementation landed in #3513 / #3545; the controlling verification backend must produce durable passing evidence.
- [ ] **11.1 Restricted egress boundary for PentestGPT external targets** — tracked by #3516 and PR #3555 in Milestone 5 and retained as a cross-project safety gate.

Changing an identifier above is a deliberate owner-approved invariant change. Update the pinning contract tests in the same change rather than deleting an identifier to make a roadmap edit pass.

---

## Milestone 1 — Complete the Normal Codex Product Path and Prove It 🚧 🧪

**Goal:** A normal UI-authored repository workflow executes in the exact authorized workspace through a policy-selected stock Codex Omnigent host, and a protected browser-to-host matrix proves the complete lifecycle.

**Current state:** Ordinary selection, readiness, runtime compilation, host lifecycle, bridge projection, controls, cutover gating, and evidence plumbing are implemented. #3507 remains open and has no merged completion PR. #3508 gained stricter evidence admission and per-row resolution/secret scanning in #3541, but the credentialed browser matrix still does not exist.

### Remaining work

- [ ] **1.0 Declarative reconciliation** — keep Create-to-host, workspace, adapter, host OAuth, combined-stack validation, and managed/external execution docs aligned with the exact shipped authority boundaries.
- [ ] **1.1 Authoritative normal-workflow workspace and host lifecycle** — complete repository, branch, attachment, Skill/tool, checkpoint/external-state, publication, output-manifest, diagnostics, partial-start reconciliation, static/on-demand parity, and shared-runtime behavior in #3507.
- [ ] **1.2 Real browser-to-host acceptance matrix** — run `/workflows/new` through a real enrolled Codex OAuth profile and unchanged stock host, covering static, restart/replay, on-demand, repository read/mutation, failure, cancellation, cleanup, janitor, denial, and secret-scan evidence in #3508.
- [ ] **1.3 Protected release linkage** — bind the complete matrix to #3508, #3448, the support rows, and the cutover promotion document through independently resolvable digest-checked artifacts.

**Done means:** a browser-originated normal Workflow request materializes the authored repository state, reaches the exact policy/profile-bound stock host, posts the first message once, produces durable Workflow Detail and artifact evidence, cleans only owned resources, releases Provider Profile capacity last, and passes the protected support matrix.

---

## Milestone 2 — Make Checkpoint Evidence Drive Resume and Branching 🔧 🧪

**Goal:** Failed Codex Omnigent work resumes from validated MoonMind-owned evidence by default, whether the original host survives or must be replaced, and corrected instructions execute through isolated Checkpoint Branches.

**Current state:** #3509 / #3554 implemented the complete v2 checkpoint manifest, restore-material validator, cold-restore input compiler, canonical checkpoint-writer integration, and independent Workflow Detail capability projections. #3556 correctly makes checkpointless remediation stop safely. Production orchestration still does not make recovery and branch methods the default product behavior.

### Remaining work

- [x] **2.0 Checkpoint declarative reconciliation** — the Step Execution, Checkpoint Branch, and Omnigent adapter documents now describe split session/workspace/host authority and the required manifest.
- [x] **2.1 Versioned capture and restore implementation** — complete lineage, refs, digests, credential generation, cursor, first-message, workspace, branch, publication, and per-capability validation landed in #3509 / #3554.
- [ ] **2.2 Acceptance proof for checkpoint boundary and completeness** — retain independently resolvable controlling evidence for the exact manifest, artifact resolution, stale-generation rejection, cold restore, and capability projections before closing acceptance claim 5.1.
- [ ] **2.3 Evidence-gated default resume** — wire production orchestration to choose safe live reattach, cold restore, branch-required, or explicit unavailable outcomes in #3510.
- [ ] **2.4 Omnigent Checkpoint Branch execution and UI** — use the existing branch APIs for isolated new host/session turns, immutable corrected instructions, profile/policy/publish selectors, compare, promote, and archive in #3510.
- [ ] **2.5 Replay and failure proof** — cover worker restart, Temporal retry/replay, stale generations, duplicate first-message prevention, partial artifacts, capacity contention, cancellation, cleanup, duplicate branch suppression, and checkpointless fail-closed behavior.

**Done means:** the original host is optional, credentials never enter checkpoint artifacts, recovery mode is evidence-derived and operator-visible, retries remain idempotent, and changed instructions or runtime choices always produce a branch rather than mutating original workflow input.

---

## Milestone 3 — Operator-Grade Remediation 🚧 🧪

**Goal:** Operators author remediation through normal Create, remediators receive complete bounded Omnigent evidence, repairs use typed policy-bound actions and Checkpoint Branches, and every action is verified and auditable.

**Current state:** #3511 / #3544 landed normal Create draft consumption, richer remediation context, bounded event/artifact/log readers, and typed controls. Its verifier still found that `cleanup.request_janitor` does not resolve and authorize the referenced cleanup target and that helper-container activity loses target-workflow linkage before the owning controller. #3512 remains open. #3556 prevents unsafe checkpointless remediation from crashing the parent workflow, but does not replace the missing resume authority.

### Remaining work

- [ ] **3.0 Declarative reconciliation** — update `docs/Workflows/WorkflowRemediation.md` and `docs/Workflows/RemediationVerificationCadence.md` for the exact shipped authoring, evidence, action, branch, cumulative-attempt, approval, verification, and rollout boundaries.
- [ ] **3.1 Close residual typed-action authority gaps** — target-authorize janitor requests, preserve target linkage through helper execution, verify action adapters against the intended resource, and reopen #3511 or create a follow-up owner before treating its roadmap claim as complete.
- [ ] **3.2 Product UI, approvals, verification, audit, and loop prevention** — complete target/remediator panels, durable approvals, fresh-evidence verification, locks, cooldowns, cumulative no-progress detection, prevention reporting, cancellation, and operator acceptance proof in #3512.
- [ ] **3.3 Checkpoint Branch integration** — require changed instructions, profile, policy, model, publish mode, or authority to create an isolated branch through the #3510 product path.
- [ ] **3.4 Autonomous remediation gate** — keep automatic or scheduled mutation disabled until the operator-driven matrix, policy enforcement, action verification, conflict controls, telemetry, and cancellation gates pass.

**Done means:** an operator can understand exactly what failed, what evidence was available, what authority was granted, what changed, whether the target actually recovered, what prevention work remains, and why autonomous action is or is not permitted.

---

## Milestone 4 — Complete RAG for Codex Omnigent Sessions 🔧 🧪

**Goal:** Moving Codex behind Omnigent does not reduce context quality or expose retrieval infrastructure credentials.

**Current state:** #3513 / #3545 implemented initial `ContextPack` resolution, persistence, safe framing, retry reuse, first-message binding, Step Execution linkage, and bridge projection. The verifier reported no remaining concrete implementation gap but could not obtain a durable controlling test result from the test backend. #3514 / #3552 added a session-bound retrieval capability, immutable budget/accounting, typed result and delivery acknowledgement, and fail-closed lifecycle revocation; the production host/tool path and required authoring controls are still absent.

### Remaining work

- [ ] **4.0 Declarative reconciliation** — update `docs/Rag/WorkflowRag.md` and Omnigent first-message/tool docs for initial context, in-session retrieval, scoped capability exchange, budgets, evidence, degraded behavior, delivery, revocation, and authoring.
- [ ] **4.1 Initial-context acceptance evidence** — rerun the controlling durable Python and product-path verification, retain independently resolvable passing evidence, and keep acceptance claim 7.1 open until that evidence exists.
- [ ] **4.2 Production in-session retrieval** — make the stock host discover and invoke the capability, deliver bounded results into the active session, record acknowledgement, and enforce overlay, fallback, timeout, retention, redaction, and denial semantics in #3514.
- [ ] **4.3 Authoring and continuation controls** — compile the same retrieval policy across normal Create, schedules, persistent agent profiles, Checkpoint Branches, and remediation.

**Done means:** Omnigent sessions receive artifact-backed initial context and can request bounded follow-up context without raw embedding, Qdrant, artifact-store, or general MoonMind credentials; every result, denial, fallback, budget, revocation, and delivery outcome is observable.

---

## Milestone 5 — Finish Policies, Agent Profiles, and Enforced Egress 🚧 🧪

**Goal:** Operators manage reusable Omnigent policy and agent-profile versions from MoonMind, and selected network constraints are actually enforced rather than merely declared.

**Current state:** #3515 / #3546 established immutable policy versions, authenticated lifecycle APIs, effective-launch authority, and bridge evidence, but the verifier found incomplete consumption across enforcement/evidence boundaries plus unfinished approvals, ownership, dependent-use, activation-impact, and product-authority migration. #3517 / #3547 established immutable agent-profile versions, APIs, bundle validation, default-runtime inventory, and list UI, while #3517 remains open for full CRUD, selectors, synchronization, smoke validation, and migration. #3516 has an open implementation PR, #3555, proposing an internal Docker network plus trusted Squid gateway, immutable egress profiles, live attestation before launch, evidence-backed readiness, bypass protections, and Pentest fail-fast behavior; its real Docker network conformance was not run.

### Remaining work

- [ ] **5.0 Declarative model reconciliation** — keep canonical policy, agent-profile, restricted-egress, Settings, Provider Profile, adapter, workspace, checkpoint, remediation, RAG, and observability contracts aligned.
- [ ] **5.1 Complete persistent policy authority** — consume one compiled immutable snapshot at every launch, bridge, workspace, checkpoint, retrieval, remediation, approval, evidence, and audit boundary; finish ownership, dependent-use, activation-impact, and environment-default migration. Reopen #3515 or create a follow-up tracker for the residual work.
- [ ] **5.2 Real restricted-egress enforcement** — review and merge #3555 if correct, then run live Docker allow/deny, DNS, redirect, IPv6, direct-IP, stale-attestation, gateway-health, static/on-demand, generic-workload, cleanup, and negative conformance before #3516 or acceptance claim 11.1 can close.
- [ ] **5.3 Persistent agent-profile product journeys** — add create, clone, edit-as-new-version, detail/diff, usage, safe deletion, upstream sync, bundle provenance, minimal-session smoke validation, bootstrap migration, and selectors across every authoring and continuation surface in #3517.

**Done means:** every run records the exact policy and agent-profile versions that governed it; the runtime realizes those snapshots without silent widening; and `enforcedNetworkRefs` represent independently verified backend enforcement rather than configured network declarations.

---

## Milestone 6 — Execute the Codex Cutover and Retire Direct Launching 🔧 🧪

**Goal:** Make Codex through Omnigent the primary supported runtime through a staged, reversible, evidence-gated rollout, then retire redundant direct launch code without breaking historical evidence or in-flight workflows.

**Current state:** #3518 / #3548 implemented the compatibility inventory, six-phase rollout, fail-closed promotion engine, stable support-row catalog, digest-bound promotion builder, readiness projection, thresholds, rollback, and immutable per-run cutover snapshots. `README.md` and Quick Start now accurately describe Codex-through-Omnigent as opt-in and direct Codex as migration compatibility. The deployed phase remains `opt_in`, protected evidence is incomplete, and #3518 remains open.

### Remaining work

- [x] **6.0 Compatibility and cutover design reconciliation** — `docs/Omnigent/CodexSupportAndCutover.md` classifies direct surfaces and owns rollout, support, rollback, and retirement policy.
- [x] **6.1 Versioned support matrix and promotion machinery** — stable rows, evidence kinds, digests, thresholds, phase snapshots, readiness, and fail-closed one-step promotion are implemented.
- [x] **6.2 Public startup and support story** — README, Quick Start, architecture table, and canonical support document now present one truthful opt-in story.
- [ ] **6.3 Protected evidence hardening** — replace minimal self-asserted pass fields with validated observed results and provenance, then publish the complete fresh matrix required for promotion.
- [ ] **6.4 Staged default and telemetry rollout** — move Create, schedule, preset, and broad-default cohorts only when readiness, live evidence, objective thresholds, and rollback criteria pass.
- [ ] **6.5 Historical and Temporal compatibility** — preserve truthful direct provenance, Workflow Detail reads, schema decoders, recorded histories, and mixed-version worker replay through every phase.
- [ ] **6.6 Controlled retirement** — disable direct scheduling before removing launch/UI/configuration code; retain compatibility event/read models until history, rollback, and release gates pass.
- [ ] **6.7 Release-metadata reconciliation** — publish the actual supported rows and deployed phase in release notes and artifacts; do not imply that merged implementation equals supported operation.

**Done means:** every supported row has evidence, explicit Omnigent selection never silently falls back, rollout and rollback are objective and observable, historical and in-flight contracts remain safe, and direct code is removed only after the phase-6 gates pass.

---

## Milestone 7 — Embedded Compatibility Mode Graduation 🔒 🧪

**Goal:** Support MoonMind’s embedded Omnigent-compatible surface only after an unmodified stock host passes the real auth, registration, session, restart, rotation, replay, network, upgrade, and rollback matrix.

**Current state:** #3519 / #3549 improved the declarative contract, per-mode fail-closed readiness, evidence validation, diagnostics, and Workflow Detail host-mode projection. Its verifier explicitly found no independently resolvable credentialed stock-host evidence, so embedded mode correctly remains experimental and proxy-first. The issue was closed despite that remaining acceptance work.

### Gated work

- [ ] **7.0 Tracker ownership** — reopen #3519 or file a follow-up issue before treating embedded graduation as scheduled work.
- [ ] **7.1 Stock-host embedded conformance** — prove fresh registration, restart/reconnect, credential rotation/revocation, session/events/resources/controls, static/on-demand behavior where supported, network policy, failure cases, upgrade, rollback, and immutable evidence.
- [ ] **7.2 Rollout and upgrade discipline** — keep proxy mode default until evidence passes; pin compatible upstream versions, require conformance on upgrade, and preserve a tested rollback version.

**Done means:** embedded mode is supported because an unchanged stock host proved compatibility—not because MoonMind has an implementation—and no host fork, browser credential forwarding, second provider login, or silent proxy/embedded substitution is required.

---

## Milestone 8 — Claude Code Omnigent Parity 🔒 🧪

**Goal:** Reuse stable provider-neutral contracts for Claude without destabilizing the Codex path or claiming support before provider-proven evidence exists.

**Current state:** #3520 / #3550 merged declarative reconciliation plus early Claude Provider Profile, Compose-host, OAuth-capacity, embedded identifier, and live-checkpoint recovery work. Its verifier found lifecycle and Temporal boundary evidence, provider-proven bridge/Workflow Detail behavior, cold recovery, branching, RAG, remediation, and Codex non-regression coverage incomplete. #3520 was closed before its dependency on #3518 was satisfied.

### Deferred work

- [ ] **8.0 Tracker ownership after Codex cutover** — reopen #3520 or create a successor issue once the Codex support matrix and cutover contracts are stable.
- [ ] **8.1 Complete profile-bound Claude host lifecycle** — reuse Provider Profile capacity, host leases, credential generations, static/on-demand lifecycle, workspace, policy, egress, cleanup, and janitor substrate.
- [ ] **8.2 Routing, bridge, Workflow Detail, checkpoint, RAG, and remediation parity** — expose only provider-proven capabilities with truthful Claude provenance and explicit degraded behavior.
- [ ] **8.3 Credentialed Claude support matrix and cutover policy** — prove normal UI-originated static/on-demand work, auth reuse, restart/replay, recovery, retrieval, remediation, and direct-Claude compatibility without regressing Codex.

**Done means:** a Settings-created Claude OAuth Provider Profile runs through the shared policy-bound Omnigent model without a second login or provider-specific product architecture, and every advertised row has real evidence.

---

## Retained Pentest disposition and shared safety gate

Pentest remains de-scoped as a first-class product area. Any retained capability should be a thin skill or preset over the generic workload path, remain disabled by default and lab-oriented, or be removed cohesively with its docs and tests.

That disposition is a target state, not the current `main` tree:

- [ ] **Superseded Pentest stack disposition** — `moonmind/integrations/pentest/`, `moonmind/workflows/temporal/activities/pentest_activities.py`, `PentestSettings`, submission validation, and Pentest provider-lease machinery remain first-class surfaces. Replace them with a thin skill or preset over the generic workload path, or remove them cohesively with their docs and tests.
- [ ] **External-target enablement fail-fast** — current `main` still permits the legacy path described by the prior audit. PR #3555 proposes default-off external targets plus exact reviewed egress-profile, approval, security-review, and in-profile target requirements. Treat this as unshipped until merged and verified.

The cross-project dependency remains #3516: Docker `bridge`, an `internal` network declaration, or a configured proxy is not by itself independently proven restricted egress. The rule is unchanged: external targets stay gated until enforcement exists and the live negative-conformance matrix passes. Even after enforcement exists, external-target work requires an explicit reviewed egress profile, target scope, approval evidence, and operator-visible diagnostics.

---

## Current tracker map

### Open acceptance and product trackers

| Priority | Issue | Current scope |
| --- | --- | --- |
| 🔴 P0 | #3507 | Authoritative normal-workflow workspace materialization and shared host lifecycle |
| 🔴 P0 gate | #3508 | Real credentialed browser-to-stock-host acceptance matrix |
| 🔴 P0 | #3510 | Evidence-gated resume and Checkpoint Branch execution in Workflow Detail |
| 🔴 P0 gate | #3512 | Remediation UI, verification, audit, and controlled rollout |
| 🟠 P1 | #3514 | Production in-session retrieval, delivery, policy, and authoring |
| 🟠 P1 security | #3516 | Restricted-egress implementation and live enforcement proof; PR #3555 is open |
| 🟠 P1 | #3517 | Complete persistent agent-profile product journeys and selectors |
| 🟡 P2 | #3518 | Complete protected evidence, staged Codex cutover, compatibility, and retirement |

### Closed trackers and their actual roadmap disposition

| Issue | Merged implementation | Roadmap disposition |
| --- | --- | --- |
| #3509 | PR #3554 | Bounded implementation slice complete; checkpoint acceptance evidence and production resume remain open |
| #3511 | PR #3544 | Significant remediation substrate; known target-authority gaps require reopened or successor ownership |
| #3513 | PR #3545 | Initial-context implementation appears complete; controlling durable verification remains blocked |
| #3515 | PR #3546 | Persistent policy foundation; cross-boundary consumption and product controls remain incomplete |
| #3519 | PR #3549 | Embedded compatibility foundation; graduation evidence absent, mode remains experimental |
| #3520 | PR #3550 | Early Claude foundation; parity and support remain deferred and incomplete |

Closing every currently open issue would still leave the residual work from closed-but-incomplete #3511, #3513, #3515, #3519, and #3520 unless those acceptance gaps receive explicit ownership.

---

## Priority order and dependencies

| Order | Milestone | Status | Primary dependency |
| --- | --- | --- | --- |
| 1 | Normal Codex product path and protected acceptance | 🚧 🧪 | #3507 before the complete #3508 matrix |
| 2 | Default resume and Checkpoint Branch product flow | 🔧 🧪 | Shipped #3509 manifest plus authoritative #3507 workspace materialization |
| 3 | Operator-grade remediation | 🚧 🧪 | #3510 recovery/branch path, residual #3544 authority fixes, and #3512 |
| 4 | Omnigent RAG | 🔧 🧪 | Initial-context verification plus complete #3514 host delivery and authoring |
| 5 | Policies, agent profiles, and restricted egress | 🚧 🧪 | Policy follow-up, #3517, review/merge of #3555, and live #3516 proof |
| 6 | Codex cutover | 🔧 🧪 | Passing product, recovery, remediation, RAG, policy/profile, egress, and support evidence |
| 7 | Embedded mode | 🔒 🧪 | Stock proxy acceptance, Codex cutover, and a reopened/successor tracker |
| 8 | Claude parity | 🔒 🧪 | Stable provider-neutral Codex contracts, cutover, and a reopened/successor tracker |

---

## Scope decisions in this refresh

| Theme | Current disposition |
| --- | --- |
| Normal workspace path | Still the first implementation blocker; #3507 is unchanged as the critical prerequisite. |
| Protected Codex matrix | Harness and admission logic improved in #3541, but no credentialed browser matrix exists; #3508 remains the release gate. |
| Checkpoint capture | v2 manifest, validation, cold-restore inputs, and projections landed in #3554; production resume and branch orchestration remain #3510. |
| Remediation | #3544 added substantial authoring/evidence/action substrate; known target-authority gaps and #3512 keep the milestone active. |
| Initial RAG | Implementation landed in #3545; durable controlling verification remains an evidence gate. |
| In-session RAG | #3552 added capability/budget/revocation substrate but not the production host/tool or five-surface authoring path; #3514 remains open. |
| Policy management | #3546 added persistent immutable authority, but complete consumption, approval, ownership, and product migration need follow-up ownership. |
| Agent profiles | #3547 added the persistent model/API/list foundation; #3517 remains open for full product and runtime integration. |
| Restricted egress | #3555 is an open implementation candidate; live Docker enforcement and negative conformance remain mandatory. |
| Codex cutover | #3548 added the phase machine and evidence builder; the deployed phase remains `opt_in` and #3518 remains open. |
| Public documentation | README and Quick Start now accurately describe the opt-in Omnigent path and direct compatibility fallback. |
| Embedded mode | #3549 strengthened implementation but supplied no stock-host proof; the closed issue needs successor ownership and the mode remains experimental. |
| Claude parity | #3550 landed early foundation before the Codex dependency; the closed issue needs successor ownership and no parity/support claim is made. |
| Workflow reliability | #3553 and #3556 repaired recent recovery/failure paths and intentionally fail closed when checkpoint authority is unavailable. |
| PentestGPT | Still de-scoped as a product area; #3555 proposes the external-target fail-fast correction, while superseded-stack disposition remains untracked. |

# MoonMind Constitution

**Version:** 2.1.0 · **Last amended:** 2026-06-25

> Principles are cited **by name**, not by number — ordering and numbering may change across amendments. See **Governance** at the end of this document for the versioning policy, amendment procedure, and amendment log. This revision adds explicit simplicity-as-safety, name-only internal capability identity for skill-name, preset-slug, and tool-name references, and narrowed short-lived internal workflow cutover guidance while preserving durable-execution safety.

## Core Principles

### I. Orchestrate, Don't Recreate

MoonMind MUST orchestrate AI agents directly — as their providers build and maintain them — rather than requiring users to rebuild agent behavior in a MoonMind-specific SDK.

Non-negotiable rules:

- MoonMind MUST work with any agent that can be reached via a standard interface (CLI, API, MCP). Specialized integrations MAY provide deeper orchestration for specific agents, but the platform MUST NOT require them.
- Adding support for a new agent MUST require only a new adapter — not changes to core orchestration logic.
- MoonMind MUST distinguish at least three execution classes and MUST NOT collapse them into one another:
  - **Managed runs** — MoonMind directly launches and supervises a CLI runtime execution (lifecycle, recovery, result normalization). Claude Code and Codex CLI are concrete on this path today.
  - **Managed sessions** — a longer-lived, workflow-scoped runtime with explicit session identity, turn control, continuity epochs, and clear/reset semantics. Codex CLI is the current concrete implementation.
  - **External / delegated agents** — MoonMind coordinates a provider-owned runtime it does not control: tracking status, injecting context, and closing the feedback loop (e.g., Jules, Codex Cloud).
- A runtime MUST NOT be described or surfaced as **session-capable** until it has a runtime-specific controller/adapter that satisfies the shared managed-session contract (launch/resume, turn control, active-turn identity, epoch/reset behavior, continuity artifacts, routing/affinity guarantees, and dashboard projections). Managed-run support does not imply managed-session support.
- Runtime-specific behavior MUST remain behind strategies, adapters, launchers, supervisors, provider-profile materialization, and activity handlers. Core orchestration MUST consume canonical contracts and compact metadata, not runtime internals.
- Core orchestration features (planning, context management, resiliency) MUST degrade gracefully — not break — for external/delegated agents where deep control is unavailable.
- MoonMind MUST NOT build a competing cognitive engine to replace the agents themselves. MoonMind MAY run its own scoped model calls for orchestration-level functions (planning, evaluation, summarization, risk classification, review) where they serve the orchestration layer rather than recreating the agent.

Rationale: The agents themselves evolve rapidly and are best maintained by their providers. MoonMind's moat is the orchestration layer above them, and that layer depends on keeping the run / session / delegated execution classes distinct rather than over-claiming uniform capability.

### II. Safety and Governance by Construction

MoonMind MUST constrain autonomous agents through enforceable runtime, credential, filesystem, Docker, network, publish, and approval boundaries built into the execution substrate — not through trust in the agent's prompt behavior. Safety is a first-class product surface, not an operational afterthought.

Non-negotiable rules (enforced today):

- Launch, control, publish, push, comment, artifact-publication, and external-tool boundaries MUST enforce the policy in effect for the run. Policy violations MUST fail fast with actionable errors, never silent degradation.
- Simplicity is a safety property. MoonMind MUST prefer one explicit canonical path over parallel aliases, layered fallbacks, duplicate identity fields, or compatibility wrappers that make policy, routing, billing, or audit behavior ambiguous.
- MoonMind MUST NOT silently substitute a credential source, switch provider profiles, rewrite billing-relevant values (e.g., model identifiers, effort), or fall back to a less-constrained execution path. Missing or revoked credentials MUST produce explicit, actionable failures — fail-fast, not fall-back.
- Provider Profiles MUST be treated as execution-target **policy contracts** (runtime + provider + credential source + materialization mode + model defaults + concurrency/cooldown + routing), not merely auth records.
- Secrets MUST be carried as **references**, never raw values, in durable contracts, profile rows, workflow payloads, logs, and artifacts. Secret values MUST be resolved only at controlled launch/proxy boundaries and MUST be redacted from observable output (logs, artifacts, outbound text, PR/issue text).
- Managed runtime work MUST run inside isolated execution boundaries: capability-routed Docker access, a per-session sidecar daemon rather than the host socket for ordinary session Docker work, and file allowlists restricting what a run may modify.
- High-autonomy or high-privilege actions (e.g., dangerous-permission runtime modes, autonomous remediation) MUST be policy-gated and operator-visible.

Target-state rules (recommended now; tracked in **Roadmap Milestone 12** and becoming MUST as the substrate lands):

- Every managed run and session SHOULD compile a **typed policy envelope** declaring runtime, provider profile, credential references, workspace scope, file allowlists, Docker/network permissions, outbound boundaries, and approval requirements — enforced at launch and control boundaries. *(Target state; becomes MUST once envelopes are compiled and enforced per run.)*
- Privileged actions SHOULD produce **governance telemetry** recording actor, action, target, policy decision, rationale, and evidence/artifact references. *(Target state.)*
- The **secret lifecycle** — who created, rotated, or deleted a secret; which profiles reference it; which launches resolved it — SHOULD be answerable from operator surfaces without exposing secret values. *(Target state.)*
- Outbound boundaries (PR/issue comments, commit/push paths, messages, artifact publication, external tool calls) SHOULD run deterministic secret/safety scans in high-security mode, blocking on match. *(Partially adopted at Jira-comment and managed-workspace git-push boundaries; full coverage is target state.)*

Gate (a prohibition, enforced today):

- Autonomous remediation and other high-autonomy actions MUST NOT be enabled until the substrate they depend on — enforced policy, governance telemetry, secret auditability, and sufficient forensic observability — is in place. Autonomy MUST NOT outrun its guardrails.

Rationale: Granting an agent your credentials and a shell is a liability unless something constrains it. Building the constraints into the substrate — and making them auditable — is what lets MoonMind grant autonomy without granting trust. Avoiding unnecessary indirection is part of that safety model: operators cannot govern what they cannot clearly identify, route, or audit.

### III. Temporal-Native Durable Orchestration

MoonMind MUST keep Temporal as the authoritative orchestration boundary for workflow lifecycle, retries, timers, cancellation, signals, updates, queries, child workflows, durable waiting, and operator-visible workflow state.

Non-negotiable rules:

- Workflow code MUST be deterministic and side-effect-free. It MUST NOT read or write files, open network connections, hold PTYs/WebSockets, launch or supervise processes, call Docker or provider APIs, write application databases or artifact stores, or resolve raw secrets. All such side effects MUST happen in routed Activities, external services, or integration callback boundaries.
- Side-effecting Activities MUST be idempotent or guarded by durable idempotency keys (e.g., `workflow_id`, `run_id`, `session_id`, `session_epoch`, `turn_id`, `request_id`, or a lease ID).
- Workflow payloads, Search Attributes, Workflow IDs, Memos, Signals, Updates, Queries, and Activity inputs/results MUST contain only compact, non-sensitive metadata. Large content and evidence MUST be represented by artifact references (see **Artifacts Are the Durable Evidence Layer**). Secret values MUST NOT traverse the Temporal control plane unless an explicit Payload Codec / Data Converter encryption policy is enabled for that payload class.
- Long-lived workflows MUST bound history and preserve replay compatibility through Continue-As-New, Temporal patch/version markers, Worker Versioning, or an explicit reset/migration plan. Changes that add, remove, or reorder workflow commands MUST use these mechanisms whenever in-flight histories or persisted payloads depend on them.
- Short-lived internal pre-release workflows MAY be cut over without compatibility shims when operators can drain, cancel, reset, or allow completion of affected runs and when no durable histories or persisted payloads are expected to replay under both old and new command shapes. This is a controlled cutover decision, not permission to break live durable execution.
- Compatibility-sensitive orchestration changes MUST ship with boundary-level regression coverage (worker-binding, Temporal activity-invocation, replay-style, or in-flight payload compatibility tests), not only isolated unit tests.

Rationale: Temporal is the durable envelope that makes fire-and-forget execution and clean recovery possible. Determinism, payload discipline, and replay compatibility are correctness requirements, not stylistic preferences. Short-lived internal workflows can be cut over aggressively when active executions are explicitly handled, but durable histories and persisted payloads remain correctness boundaries that must not be corrupted.

### IV. Artifacts Are the Durable Evidence Layer

MoonMind MUST treat every run as an evidence-producing process whose durable record survives the container, worker, session, or provider process that produced it.

Non-negotiable rules:

- Large execution data — prompts and instruction bundles, retrieved context packs, skill snapshots, stdout/stderr, merged logs, diagnostics, generated files and patches, provider result bundles, session summaries and reset boundaries, control events, and observability event streams — MUST be stored as artifacts or compact artifact references, not embedded in workflow history or terminal result contracts.
- Artifacts MUST remain linked to concrete executions. Workflow-, step-, run-, and session-oriented views — including the dashboard — MUST be projections over execution-linked artifacts and compact metadata, NOT a second durable source of truth.
- MoonMind MUST provide an operator-facing surface (the dashboard) to track real-time run status, browse artifacts, inspect per-step logs and diagnostics, monitor intervention requests, and audit execution histories. The dashboard is the primary operator interface and its capabilities SHOULD grow alongside the orchestration layer.
- Operators MUST be able to answer "what happened?" and "what is the evidence?" from durable artifacts and compact metadata without reading raw worker internals. Operators SHOULD likewise be able to answer "what changed?", "what failed?", and "what did it cost?" as the observability surface matures. *(End-to-end tracing and per-step cost attribution are target state; tracked in Roadmap Milestone 14.)*
- Structured logs, metrics, and traces MUST use stable, non-sensitive correlation identifiers (workflow / activity / run / runtime / profile / session / turn IDs and artifact refs). Raw prompts, raw credentials, full logs, generated file contents, and secret-bearing data MUST NOT be emitted as ordinary structured telemetry fields.
- Live logs and streaming SHOULD degrade to artifact-backed replay; live-stream failure MUST NOT fail the run.

Rationale: "It finished" is not an answer. Evidence that outlives the process is what makes runs auditable, recoverable, and improvable. The dashboard reads that evidence; it does not replace it.

### V. One-Click Agent Deployment

MoonMind MUST provide a “fresh clone → running system” path that is simple,
documented, reliable, and local-first in any environment that supports Docker
Compose.

Non-negotiable rules:

- The repo MUST define a canonical “one-click” operator path. For MoonMind, that
  path is `docker compose`.
- The core system MUST be deployable in any environment with Docker Compose
  without requiring essential dependencies from an external public cloud.
- A default deployment MUST start successfully using only:
  - documented prerequisites (Docker Engine and Docker Compose, or equivalent),
    and
  - a minimal, clearly documented set of required secrets (if any).
- Core development, testing, and baseline runtime flows MUST remain available
  against repo-managed or operator-managed local services. External SaaS or public
  cloud services MAY be supported, but only as optional integrations or explicit
  operator choices.
- All non-secret configuration MUST have smart defaults (safe, functional, and predictable).
- Optional integrations MUST be either:
  - disabled by default with safe no-op behavior, or
  - enabled by default only if they do not require secrets and do not increase operational risk.
- Any missing prerequisite MUST fail fast with an actionable error message (what is missing + how to fix it).

Rationale: MoonMind is an operator tool. Setup friction and mandatory cloud
coupling are feature-killers.

### VI. Avoid Vendor Lock-In

MoonMind MUST avoid designs that force one exclusive proprietary provider to use core functionality.

Non-negotiable rules:

- Vendor-specific behavior MUST live behind adapter interfaces so alternatives can be added without refactoring core flows.
- Data formats for artifacts, run state, and logs MUST be stored in portable, inspectable formats (e.g., JSON/YAML/text diffs).
- When introducing a vendor-specific feature, the plan MUST document:
  - what would change to support an alternative provider, and
  - what is intentionally vendor-specific (and why).

Rationale: MoonMind should remain deployable and evolvable across ecosystems.

### VII. Own Your Data

MoonMind MUST make it easy to gather data from many sources, store it locally, and provide it as managed context to AI agents.

Non-negotiable rules:

- MoonMind MUST support ingesting context from diverse sources (e.g., GitHub, Jira, Confluence, Google Drive, local files) through built-in loaders.
- Context MUST be injectable at the step level — the orchestrator decides what each agent sees and clears context between steps to prevent window pollution.
- Procedural memory (structured summaries of past runs and failures) MUST be retained and available to future runs so agents don't repeat the same mistakes.
- All ingested data and generated artifacts MUST be stored on operator-controlled infrastructure, not in external SaaS by default.

Rationale: Context management is a core orchestration advantage. Agents perform better when they see the right information at the right time — and operators must own the data that makes that possible.

### VIII. Skills Are First-Class and Easy to Add

MoonMind MUST make skills straightforward to create, register, test, and use across runtimes.

Non-negotiable rules:

- Skills MUST be discoverable and composable (usable as steps in larger workflows).
- Adding a skill SHOULD be “low ceremony”:
  - minimal boilerplate,
  - clear registration location,
  - clear contract for inputs/outputs and side effects.
- Skills MUST declare:
  - required inputs,
  - produced outputs/artifacts,
  - external dependencies,
  - failure modes and expected operator actions.
- Skill execution SHOULD be runtime-neutral at the workflow level (with runtime adapters implementing the specifics).
- Internal capability identity MUST be name-only and canonical:
  - agent instruction bundles are identified by **skill-name**,
  - task presets are identified by **preset-slug**,
  - executable tools are identified by **tool-name**.
- MoonMind MUST NOT introduce internal ID aliases, display-name matching, provider-specific synonyms, or compatibility translation tables for those capability identities. Renames MUST update every caller, test, mock, seed, and doc reference in the same cohesive change.

Rationale: Skills are the unit of scale for MoonMind automation. Name-only identity keeps capability routing understandable across agents, workflows, presets, and tools; parallel IDs and aliases create ambiguity exactly where safety, auditability, and operator intent need crisp boundaries.

### IX. The Bittersweet Lesson: AI scaffolds are useful, but they must constantly evolve.

**The Principle:** AI scaffolding is a massive short-term speed multiplier—but it expires. Scaffolding decays rapidly as product boundaries sharpen, integrations drift, and foundation model capabilities internalize what used to require custom code. Therefore, optimize for **replaceability and evolution**: build every scaffold expecting to delete, swap, or regenerate it quickly without destabilizing the system.

**The Forever Scaffold:** While ephemeral reasoning loops and API wrappers will inevitably decay, **the Scientific Method is permanent**. Architect the system so that every task is treated as a verifiable experiment: **Hypothesize → Execute → Verify → Publish → Learn**. The underlying implementation will change, but this evidence-based loop is the permanent bedrock that allows you to confidently delete obsolete code.

**Actionable Engineering Norms:**

1. **Design for Deletion (Compressible Workflows)**
If a 5-step Temporal workflow is required today because a model cannot plan and code simultaneously without losing context, expect to delete 4 of those steps tomorrow. Keep workflows declarative and loosely coupled so intermediate cognitive steps can be bypassed via capability flags the moment an experiment proves a model can do it natively.
2. **Thin Scaffolding, Thick Contracts**
Push complexity behind stable interfaces. Keep adapters and tool wrappers “dumb.” Prefer standard protocols (e.g., MCP) and capability discovery over bespoke per-tool integration layers.
3. **Abstract the Infrastructure, Not the Cognition (The 'Execute' Phase)**
Focus rigid, robust engineering on what LLMs won’t do natively: secure remote execution, job queues, container lifecycles, secrets, persistence, and observability. Treat agents (Jules, Gemini CLI, Claude Code, Codex) as highly interchangeable compute engines behind a standard runtime interface.
4. **Tests are the Anchor (The 'Verify' Phase)**
“First draft” scaffolding is allowed to be rough, provided it is surrounded by strict contract and integration tests. Implementations can be rapidly swapped or regenerated by an AI, but the behavioral contract and the objective telemetry must hold firm to prove the hypothesis succeeded.
5. **Isolate Volatility**
Encapsulate likely-to-change code—vendor auth flows, API clients, capability negotiation, and UI seams—into replaceable modules with explicit boundaries. This ensures that early integration hacks don’t calcify into the permanent architecture.

### X. Powerful Runtime Configurability

MoonMind MUST be configurable at runtime without requiring code edits or image rebuilds for routine changes.

Non-negotiable rules:

- Operator-facing behavior MUST be controlled by configuration (env/config) rather than hardcoded constants.
- Configuration MUST have deterministic precedence (highest to lowest):
  1) command-line arguments (for CLI tools),
  2) explicit request payload / API parameter (when applicable),
  3) environment variables,
  4) config file,
  5) defaults.
- Each config option MUST be:
  - documented (purpose + default + examples),
  - namespaced consistently (e.g., `MOONMIND_*`), and
  - safe-by-default (no surprising network calls, no permissive security defaults).
- Runtime mode switches (e.g., worker runtime selection, adapter selection) MUST be observable in logs and/or run metadata.

Rationale: MoonMind runs in many environments; routine tuning must be easy and reversible.

### XI. Modular and Extensible Architecture

MoonMind MUST remain easy to extend without rewriting the core.

Non-negotiable rules:

- New capabilities MUST be introduced behind clear module boundaries with explicit contracts.
- Core orchestration logic MUST depend on stable interfaces (contracts), not on vendor/CLI specifics.
- Adding a new integration SHOULD require:
  - a new adapter/module, and
  - minimal changes to the existing orchestration core.
- Cross-cutting changes (touching many modules) MUST be justified in the plan “Complexity Tracking” section.

Rationale: Extensibility is the product. Architecture must resist entanglement.

### XII. Resilient by Default

MoonMind MUST enable fire-and-forget execution — operators should be able to submit work, walk away, and trust the system to handle failures without babysitting.

Non-negotiable rules:

- All externally visible side effects (starting runs, publishing results, posting to GitHub/Jira) MUST be retry-safe so that a crash mid-operation never produces duplicates. At the workflow boundary this is realized through the activity-idempotency rule in **Temporal-Native Durable Orchestration**.
- Long-running workflows MUST persist enough state to resume, retry, or fail deterministically after worker restarts.
- MoonMind MUST detect stuck agents (loops, repeated failures) and apply escalating interventions (soft reset, hard reset, termination) before burning through the operator's API budget.
- Failure classification MUST distinguish transient errors (safe to retry) from permanent failures (stop execution).
- Failure handling MUST be explicit:
  - retries and backoff where appropriate,
  - deterministic “needs human” terminal states when not recoverable,
  - error summaries that tell an operator what happened and what to do next.
- Health checks MUST exist for runtime-critical services and worker processes (startup checks + dependency checks).
- Changes to workflow/activity/update/signal contracts MUST remain safe for in-flight executions per **Temporal-Native Durable Orchestration** (replay compatibility, versioned cutover, and boundary-level regression coverage).

Evidence-based recovery and remediation:

- Recovery MUST be evidence-based: remediation actions MUST read durable artifacts, step ledgers, diagnostics, and compact workflow metadata rather than transient container state.
- Remediation actions MUST be typed, idempotent, privilege-separated, and audit-logged.
- Duplicate or conflicting repair attempts MUST be prevented through durable locks, ledgers, or reconciliation state.
- Checkpoint resume SHOULD be the default recovery path when evidence supports it. *(Operator-driven recovery is current; resume-as-default is target state, tracked in Roadmap Milestone 13.)*
- Autonomous remediation MUST NOT outrun the safety and observability substrate it depends on (see **Safety and Governance by Construction**).

Rationale: Resiliency is what makes unattended execution possible. The system must withstand infrastructure failures, agent failures, and runaway costs — and when a run does fail, recovery must be driven by durable evidence, not by whatever happened to survive in a container.

### XIII. Facilitate Continuous Improvement

MoonMind MUST make it easy to improve itself and the projects it operates on.

Non-negotiable rules:

- Every run MUST end with a structured outcome summary:
  - success / no-op / failed,
  - primary reason (if failed),
  - key artifacts/links,
  - recommended next action.
- The system SHOULD capture improvement signals (for example: repeated retries, loops, ambiguous prompts, missing files, flaky tests)
  and route them into a reviewable backlog (e.g., proposals / improvements queue).
- Continuous improvement suggestions MUST be opt-in for application (reviewable; no silent auto-commit to important repos).

Rationale: MoonMind is an automation engine; it must learn from real execution.

### XIV. Docs-First Development and Traceability

MoonMind development MUST be docs-first, with clear contracts and traceability. The durable source of truth is the canonical, long-lived documentation under `docs/` together with this constitution — not the per-feature execution packets under `specs/`.

Non-negotiable rules:

- Durable, desired-state knowledge — architecture, contracts, operator-visible behavior, and target semantics — MUST live in the owning long-lived `docs/` files (and this constitution). These are the canonical surfaces that future readers and agents are expected to trust.
- Per-feature Spec Kit / MoonSpec artifacts under `specs/<id>-<feature>/` (`spec.md`, `plan.md`, `tasks.md`, `research.md`, `contracts`) are **temporary, run-local execution artifacts**. The `specs/` tree is gitignored: packets are disposable execution scaffolding, not version-controlled history, and MUST NOT be treated as canonical long-term architecture or behavior documentation.
- When a non-trivial change lands, its durable decisions MUST be reflected in the owning `docs/` files; the `specs/` packet (if one was produced) remains supplemental execution evidence, not the system of record.
- When a feature does use the optional execution workflow, `spec.md` SHOULD remain technology-agnostic with implementation details in `plan.md`, and `plan.md` MUST include a “Constitution Check” with PASS/FAIL coverage for each principle (documenting any violation and mitigation in “Complexity Tracking”).
- Documentation MUST NOT silently drift from reality: when behavior changes, update the owning long-lived `docs/` files first; refresh any in-flight `specs/` execution packet only as supplemental history.
- **Public-facing release metadata is a documentation surface.** Package metadata, license declarations, version strings, README positioning, compose/startup instructions, and product descriptions (e.g., `pyproject.toml`, `package.json`) MUST agree with each other and with the canonical README, roadmap, and long-lived `docs/`. They MUST NOT describe an older or divergent product.
- **This constitution is itself a canonical documentation surface.** It MUST carry version and amendment metadata (see **Governance**) and MUST NOT drift from the architecture and product direction it governs.

Rationale: Canonical docs are how MoonMind stays maintainable while evolving quickly. Spec packets are disposable execution scaffolding; the long-lived docs, the public release metadata, and this constitution are what operators and agents must be able to rely on.

### XV. Canonical Documentation Separates Desired State from Migration Backlog

MoonMind MUST keep long-lived documentation readable as **what the system is for and how it should behave**, not as a running construction diary.

Non-negotiable rules:

- Documentation under `docs/` MUST focus on **declarative, desired-state** descriptions: architecture, contracts, operator-visible behavior, and target semantics (including Temporal-native orchestration where that is the product direction).
- **Migration narratives, phased implementation plans, rollout sequencing, and implementation backlogs** MUST be recorded under **`docs/tmp/`** or in **local-only / gitignored handoff paths** (for example `artifacts/` for ephemeral tool outputs, or run-local `specs/<feature>/` packets), not embedded as the primary framing of canonical docs.
- Canonical docs MUST NOT depend on disposable migration-only material to be readable; time-bound work belongs in `docs/tmp/`, `artifacts/`, run-local `specs/<feature>/` packets, or other explicitly local handoffs.
- When a migration or implementation effort completes, **delete or archive** the corresponding scratch material rather than leaving obsolete plan sections in canonical files.
- Canonical docs that still have open migration work SHOULD **point** to the relevant `docs/tmp/` plan or tracking issue instead of duplicating volatile checklists inline; they MUST NOT cite gitignored paths as required reading.

Rationale: Canonical docs stay stable references for operators and implementers; time-bound work belongs in `docs/tmp/` or disposable local artifacts.

### XVI. Pre-Release Velocity: Delete, Don't Deprecate

MoonMind is a **pre-release project** with no external consumers. Speed of iteration and codebase clarity MUST take priority over backward compatibility with internal legacy patterns.

Non-negotiable rules:

- When a pattern, interface, model, activity name, or alias is superseded, the old version MUST be **removed immediately** — not preserved as a compatibility shim, fallback, or "just in case" alias.
- Dead code, orphaned schemas, stale documentation, and unused imports MUST be deleted in the same change that introduces their replacement. Leaving them behind is a defect, not a kindness.
- Do NOT introduce compatibility aliases, translation layers, or backward-compat wrappers for internal-only contracts. There are no external consumers to protect.
- Legacy artifacts (code, docs, configs) that remain after a refactor are considered **tech debt bugs**, not acceptable trade-offs. They actively harm troubleshooting by creating ambiguity about which path is live.
- When refactoring, **track down and update every caller** — grep the codebase, update tests, update docs — in a single cohesive change. Partial migrations are worse than no migration.

Durable-execution carve-out (this is a correctness boundary, not legacy compatibility):

- "Delete, don't deprecate" MUST NOT be read to permit Temporal nondeterminism, broken replay, orphaned in-flight executions, or unreadable persisted payloads. Internal compatibility shims for code-level contracts are unnecessary. Replay / version / migration paths are **required** when durable workflow histories, persisted payloads, or live executions must cross the change boundary; short-lived internal pre-release workflows MAY instead use an explicit drain/cancel/reset/complete cutover when that preserves durable-execution safety (see **Temporal-Native Durable Orchestration** and **Resilient by Default**).
- Breaking changes to any future **public** API/contract MUST include a migration plan. Deprecation windows and compatibility aliases are NOT required for internal contracts — remove cleanly and immediately. If a public API surface is later introduced, deprecation windows MUST be defined at that time.

Rationale: In a pre-release project, the cost of legacy confusion vastly exceeds the cost of a clean break. Every stale alias, orphaned model, or outdated doc section is a future debugging trap. The one thing a clean break must never break is a durable workflow that is already running.

## Development Workflow & Quality Gates

- **Constitution is a gate**:
  - Every `/agentkit.plan` output MUST include the Constitution Check gate, and it MUST be re-checked after Phase 1 design.
  - A plan **PASSES** a principle when it complies with that principle's **MUST** rules. **SHOULD** and explicitly **target-state** items are recommendations: they do not by themselves cause a FAIL, but a plan that regresses against them MUST note it under “Complexity Tracking,” and a plan that claims to deliver a target-state capability MUST meet the corresponding MUST bar.
- **Validation is required**:
  - Each feature MUST define at least one independent validation path (automated tests or a deterministic quickstart/manual validation).
- **Clarity over cleverness**:
  - Prefer explicit contracts, explicit adapters, and explicit errors over implicit fallback behavior.
- **Simplicity gate**:
  - Plans and implementations MUST reject unnecessary aliases, duplicate identity systems, speculative abstractions, and generated context bulk that obscures the canonical path. Added complexity MUST be justified by a concrete safety, durability, or operator-value need.
- **Exceptions must be visible**:
  - If a plan violates a MUST principle, it MUST be documented as a violation with a mitigation and a path back to compliance.

## Governance

- **Authority.** This constitution and the canonical long-lived documentation under `docs/` are the durable source of truth. Where this constitution and a `docs/` file disagree, the constitution governs the principle and the `docs/` file governs the mechanism; conflicts MUST be resolved by amendment rather than left standing.
- **Referencing principles.** Cite principles **by name** (e.g., “Safety and Governance by Construction”), not by number. Numbering is presentation order and MAY change across amendments; names are stable identifiers.
- **Versioning policy.** This document uses semantic versioning:
  - **MAJOR** — a principle is added, removed, or materially redefined, or the document is restructured.
  - **MINOR** — a new rule or meaningful expansion is added within an existing principle.
  - **PATCH** — clarifications, wording, or non-semantic fixes.
- **Amendment procedure.** Amendments MUST update the version and `Last amended` date, summarize the change in the amendment log below, and propagate any renamed principles to dependent references (e.g., agent instruction files, `docs/` cross-references) in the same change, per **Pre-Release Velocity: Delete, Don't Deprecate**.
- **Amendment log.**
  - **2.1.0 (2026-06-25)** — Added explicit simplicity-as-safety language to **Safety and Governance by Construction**, name-only internal capability identity for skill-name, preset-slug, and tool-name references under **Skills Are First-Class and Easy to Add**, and narrowed **Temporal-Native Durable Orchestration** / **Pre-Release Velocity: Delete, Don't Deprecate** guidance so short-lived internal pre-release workflows may use explicit cutover without compatibility shims while durable histories and persisted payloads remain protected. Added a simplicity gate to quality gates. Traceability: MM-911, source issue MM-901.
  - **2.0.0 (2026-06-16)** — Added **Safety and Governance by Construction**, **Temporal-Native Durable Orchestration**, and **Artifacts Are the Durable Evidence Layer**. Updated **Orchestrate, Don't Recreate** (three execution classes; session-capability claim discipline), **Resilient by Default** (evidence-based/typed remediation; autonomous-repair gate), **Docs-First Development and Traceability** (release-metadata hygiene; constitution as a versioned doc surface), and **Pre-Release Velocity: Delete, Don't Deprecate** (durable-execution carve-out). Reordered so the execution-model principles follow **Orchestrate, Don't Recreate**, with Safety leading. Relocated the former *Non-Negotiable Product & Operational Constraints* (security/secret hygiene, observability/Mission Control, compatibility/migration) into their owning principles so each rule lives once. Established versioning and the cite-by-name convention.
  - **1.x (unversioned)** — Original thirteen-principle constitution prior to the introduction of version metadata.

# 🌙 MoonMind Roadmap

> MoonMind is a secure, resilient, and observable orchestration platform for agentic work.
>
> The near-term architectural priority is to make **Omnigent the primary runtime provider** for Codex, Claude Code, OpenCode, and future approved harnesses. The broader product destination also includes guided cybersecurity workflows, a simpler user experience, portable outputs, and extensible connectors without accumulating a separate MoonMind architecture for every agent, tool, provider, or external service.
>
> MoonMind owns durable workflow authority, policy, credentials, workspaces, recovery, evidence, and publication. Provider-maintained runtimes, specialized tool images, connectors, and replaceable infrastructure should remain outside the core wherever practical.
>
> The canonical runtime-provider strategy is [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](Omnigent/PrimaryRuntimeProviderStrategy.md). The harness mechanics are defined by [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](Omnigent/OmnigentHarnessPlatformDesign.md).
>
> **Document class:** this file is a canonical declarative document. It states the durable product destination, milestone outcomes, ownership boundaries, evidence rules, acceptance-claim identifiers, and security gates that persist across implementation waves. Dated checklists, issue-by-issue status, rollout sequencing, and PR disposition belong in [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md). When the tracker and this roadmap disagree, the declarative design wins.

---

## Advance organizer

**One sentence:** MoonMind should let people direct provider-maintained agents and specialized tools through one durable, policy-controlled platform that is simple for normal use and deeply inspectable when something goes wrong.

**One paragraph:** Omnigent becomes the preferred runtime provider beneath MoonMind's authority. Codex, Claude Code, OpenCode, and future approved harnesses enter one generic execution, session, chat, evidence, recovery, and cleanup plane. MoonMind then builds on that foundation with guided cybersecurity workflows, progressively simpler product abstractions, portable output formats, and replaceable connectors, retrieval providers, and memory providers. Normal operation should remain local-first, useful with minimal configuration, and capable of producing durable work even when GitHub or another external publishing destination is not connected.

---

## Product destination

MoonMind orchestrates agentic work rather than recreating provider agents.

A normal workflow or interactive request should resolve through:

```text
user intent
  -> Workflow, preset, or interactive session
  -> immutable Agent Profile and policy
  -> compatible Provider Profile or credentialless provider
  -> authorized workspace, context, Skills, and tools
  -> provider-maintained agent through Omnigent
  -> canonical session, turns, chat, evidence, and controls
  -> durable result, artifact, repository output, or connector publication
  -> checkpoint, recovery, verification, and cleanup
```

The product should answer five questions clearly:

1. What should MoonMind do?
2. What source, target, or context may it use?
3. Which approved agent or capability should perform the work?
4. What authority and constraints govern the run?
5. Where should the result be preserved or published?

The default experience should not require users to understand Host Classes, materializers, runtime packs, execution realizers, lease generations, or other implementation machinery. Those concepts remain available in operator diagnostics and evidence where they are necessary for control and trust.

---

## Governing principles

- **One control plane, many capabilities.** Agents, tools, and connectors enter through stable MoonMind contracts rather than separate workflow engines.
- **Omnigent is the preferred runtime provider.** New managed coding-agent support should enter through the generic Omnigent harness platform unless a reviewed limitation requires another boundary.
- **MoonMind retains authority.** Temporal orchestration, Provider Profiles, OAuth enrollment, credentials, workspaces, Skills, model and policy selection, publication, checkpoints, remediation, evidence, and cleanup remain MoonMind-owned.
- **One top-level Omnigent identity.** Omnigent-backed harnesses use `agentKind=external`, `agentId=omnigent`. Harness identity remains nested immutable authority.
- **One generic lifecycle.** Codex, Claude Code, OpenCode, and future approved harnesses share execution planning, leases, host realization, sessions, turns, chat, evidence, publication, recovery, and cleanup.
- **Differences stay at small adapters.** Runtime-specific logic should be limited to trusted runtime-pack descriptors, credential materializers, bounded probes, protocol adapters where genuinely required, and truthful capability normalization.
- **Security is enforced by the substrate.** Credentials, filesystems, networks, tools, targets, publication, approvals, retrieval, and controls receive explicit scoped authority.
- **Simple normal path, deep advanced path.** The common experience is opinionated and guided. Advanced controls are progressively disclosed by capability, deployment policy, and permission.
- **Portable outputs are first-class.** A workflow can preserve useful work as artifacts, reports, patches, Git bundles, local commits, or workspace archives without requiring a particular hosted platform.
- **Support is exact and evidence-gated.** A binary, plugin, image, or connector being present does not make it supported. Evidence binds the exact combination that was exercised.
- **No silent fallback.** An explicit plan never silently changes runtime, harness, Provider Profile, model, policy, target scope, host mode, repository state, retrieval scope, or publication authority.
- **Migration preserves history.** Existing plans and Temporal histories retain their recorded realizer and runtime provenance until replay, rollback, historical-read, and retention criteria permit removal.
- **Partner maintenance is preferred.** MoonMind should adopt provider-maintained runtimes, tools, plugins, and connectors through thin governed boundaries instead of internalizing their implementation without a compelling reason.

---

## Target ownership split

- **MoonMind owns** Workflow authoring, Temporal orchestration, workflow/run/step identity, Agent Profile and Provider Profile selection, Provider Profile capacity, OAuth enrollment, credential-generation fencing, policy selection, canonical workspaces, Skills and mounted tools, context authority, checkpoint/resume/branching, remediation, durable artifacts, publication, cleanup, and operator audit evidence.
- **Omnigent owns** the host and runner protocol, harness discovery, the selected live provider process inside the authorized host environment, provider-session interactions, and live upstream events and resources.
- **The MoonMind Omnigent bridge owns** profile-authorized session creation or attachment, canonical session and turn correlation, event normalization and replay, Workflow Detail projection, native Workflow Chat authorization, controls, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Specialized tool images own** their approved binaries and portable invocation behavior. MoonMind owns image admission, capability policy, durable job execution, evidence collection, and cleanup.
- **Connector providers own** provider-specific APIs, pagination, subscriptions, and object semantics. MoonMind owns connection authority, normalized capabilities, workflow use, side-effect evidence, and retry or reconciliation policy.
- **Direct Codex and direct Claude remain migration compatibility substrate** until their supported Omnigent combinations pass the required product and release gates. Historical direct provenance must remain truthful.
- **The legacy Codex profile-bound Omnigent realizer remains explicit compatibility substrate** until generic Codex parity, rollback, and replay criteria pass.
- **OpenCode is the first proven generic-host integration**, not a permanent reason to keep a dedicated OpenCode-only host architecture.
- **The stock proxy topology remains the primary supported browser acceptance path.** Embedded behavior is promoted only through its own compatibility evidence.

---

# Milestone 1: Complete the Omnigent Agent Platform

## Outcome

Codex, Claude Code, OpenCode, and future approved harnesses operate as variations of one Omnigent-backed MoonMind platform rather than as separate MoonMind runtime architectures.

A user can start a workflow or interactive session, communicate with the selected agent, observe live progress, steer or interrupt it, recover from failures, and obtain durable outputs through the same core lifecycle regardless of the selected harness.

This is the current primary milestone and is intentionally more detailed than later milestones.

## 1. One generic execution lifecycle

All supported harnesses use the same fundamental path:

```text
external/omnigent
  -> immutable Omnigent Agent Profile
  -> compatible Provider Profile
  -> immutable execution plan
  -> policy-selected Host Class
  -> trusted runtime pack and credential materializer
  -> fenced runtime binding
  -> authorized workspace, Skills, tools, and network
  -> canonical Omnigent session and turns
  -> Workflow Chat, evidence, publication, checkpoint, and cleanup
```

The shared lifecycle includes:

- Workflow, run, Step Execution, AgentRun, session, and turn identity
- Provider Profile capacity, cooldown, credential generation, and revocation
- Static and on-demand host realization
- Repository and workspace preparation
- Skill and mounted-tool delivery
- Resource, network, and egress policy
- Session creation, follow-up turns, approvals, and continuation
- Event normalization, replay, and live projection
- Checkpoint capture, reattach, cold restore, and branching
- Publication, saved work, reports, and artifacts
- Cancellation, cleanup, janitor reconciliation, and audit evidence

Adding another supported harness must not require another top-level Temporal workflow, another durable session model, or another host-lifecycle coordinator merely because its CLI differs.

## 2. Minimal harness-specific logic

Genuine runtime differences are represented through small registered components:

- **Runtime packs** describe the binary, supported versions, environment shaping, readiness and authentication probes, model discovery, and host compatibility.
- **Credential materializers** present the selected Provider Profile generation to the runtime while preserving its ownership, isolation, fencing, and cleanup semantics.
- **Capability adapters** truthfully normalize features such as session resume, approvals, subagents, terminals, resources, or native branching.
- **Protocol adapters** exist only when a harness or Omnigent integration exposes genuinely different behavior that cannot be represented through the shared protocol.

The abstraction must not pretend every harness has identical capabilities. Unsupported and degraded behavior remain explicit, typed, and visible.

## 3. Shared host distribution where practical

Codex, Claude Code, and OpenCode should reuse one neutral, digest-pinned MoonMind Omnigent host image when doing so does not weaken credential isolation or create unreasonable release coupling.

Separate Host Classes and support rows may point to the same image digest. Each harness still has independent qualification, rollout, and rollback authority.

The shared host release contract includes:

- Exact Omnigent server and host build compatibility
- Approved runtime and harness versions
- Immutable multi-architecture image identity
- SBOM and build provenance
- Runtime-pack and materializer compatibility
- Exact-host readiness and authentication probes
- Drift detection, quarantine, and stale-plan rejection
- Last-known-good rollback identities

A shared image never means shared OAuth homes, API keys, credential mounts, active harness authority, or support evidence.

## 4. Unified provider and credential setup

Provider Profiles are the standard representation for provider accounts, credentials, capacity, cooldown, models, connection state, and authentication generations.

The normal setup journey supports:

- Guided OAuth enrollment for Codex and Claude Code
- Guided API-key setup where appropriate
- Credentialless approved providers, including supported OpenCode free-tier profiles
- Multiple accounts for the same provider
- Clear model, tier, effort, and capability availability
- Connection validation, repair, rotation, and revocation
- Shared capacity enforcement across harnesses that use the same provider account
- Strict isolation among Codex, Claude Code, OpenCode, and future runtime credential homes

An Omnigent host must not start an unexpected second login ceremony. It consumes only the exact Provider Profile generation selected and leased by MoonMind.

## 5. Reliable interactive Workflow Chat

Workflow Chat becomes the primary interactive control surface over the canonical Omnigent session rather than a loosely coupled message feature.

It supports:

- Incremental streaming with bounded buffering
- Reconnect and replay from durable cursors
- Same-session follow-up turns
- Explicit queued, delivered, active, waiting, failed, and completed states
- Steering, interruption, cancellation, and stop controls
- Approval and elicitation responses
- Attachments, resources, and artifact references
- Long-running commands and resumable processes
- Provider-session replacement when evidence requires recovery
- Idempotent first-message and follow-up-turn delivery
- Durable reconstruction after the live host is gone

Workflow Detail shows the relationship among the workflow, current turn, provider session, host, workspace, resources, artifacts, cleanup, and terminal outcome without requiring an operator to use a second runtime dashboard.

## 6. One platform for workflows and continuations

The same Omnigent platform powers:

- New workflows
- Presets and generated plans
- Batch workflows
- Scheduled and recurring workflows
- Reruns and edited reruns
- Repository-output continuations
- Workflow Chat
- Checkpoint resume
- Checkpoint Branches
- Remediation
- Verification and review agents
- Publication-only recovery

Each path preserves the exact profile, policy, model intent, workspace authority, Skill snapshot, retrieval authority, and publication authority selected for it. Changed instructions or authority-sensitive choices create an explicit new turn, branch, or execution rather than rewriting historical input.

## 7. Useful operation without GitHub credentials

GitHub publication is one output option, not a prerequisite for agent compute.

Without a GitHub PAT or GitHub App connection, MoonMind can still:

- Work in a local, mounted, uploaded, or previously materialized repository
- Initialize a local Git repository
- Create local commits and branches
- Preserve changes as a patch or Git bundle
- Produce reports and other durable artifacts
- Export a workspace archive
- Perform non-repository analysis and tool workflows
- Attach a remote destination later without repeating the agent work

A fresh local deployment should select a credentialless or locally available model profile when an approved provider supports one. Adding GitHub, another Git host, or a paid provider later should not change the fundamental workflow model.

## 8. Recovery and operational reliability

The generic lifecycle behaves correctly through:

- Worker, process, server, and host restart
- Provider capacity and temporary unavailability
- Host-image or runtime-pack drift
- Host startup and registration failure
- Event-stream interruption
- Duplicate Activity delivery
- Credential rotation and stale generations
- Workspace restoration and checkpoint recovery
- Cancellation during every launch and execution phase
- Publication response loss
- Cleanup failure and janitor reconciliation

Recovery preserves the original model, Provider Profile, policy, target, workspace, and publication intent unless the user explicitly creates a new branch or execution with changed authority.

The original host is optional when MoonMind has sufficient durable workspace, checkpoint, session, event, and publication evidence. Recovery chooses an explicit supported outcome such as live reattach, cold restore, branch required, new session required, or unavailable.

## 9. Qualification, cutover, and retirement

Every supported combination has exact deterministic and protected-live evidence covering the runtime, image, architecture, materializer, Provider Profile class, model, policy, host mode, session lifecycle, workspace, chat, publication, recovery, and cleanup behavior.

Cutover proceeds independently for:

- Workflow Create
- Workflow Chat
- Presets
- Schedules
- Reruns
- Checkpoint Branches
- Remediation

Direct and legacy paths remain explicit compatibility substrate until new admission has stopped, active work has drained, required Temporal histories replay, historical evidence remains readable, rollback no longer depends on the old path, and retention policy permits removal.

Only then are duplicate runtime code, Compose services, credential handling, UI options, environment settings, tests, and documentation removed.

## Milestone 1 is complete when

A user can select Codex, Claude Code, or OpenCode through the same normal product experience and receive equivalent lifecycle guarantees. Workflow Chat is interactive, durable, and recoverable. Repository and non-repository outputs work. Local operation does not require a GitHub credential. Every advertised combination has exact evidence. Adding another harness is primarily registration, adaptation, and conformance work rather than creation of another execution architecture.

---

# Milestone 2: Build a Guided Cybersecurity Workbench

## Outcome

MoonMind makes sophisticated cybersecurity workflows operable by software engineers and other authorized users who are not security specialists.

The product guides the user through authorization, scope, tool selection, execution, evidence, interpretation, remediation, and verification. It does not expose an unrestricted collection of offensive binaries or rely on a prompt alone to enforce security boundaries.

## 1. Start with software and repository security

The first workflows should focus on high-value, comparatively bounded activities:

- Secret and credential exposure
- Dependency and vulnerability analysis
- SBOM generation and comparison
- Static application security testing
- Infrastructure-as-code and deployment review
- Container and image analysis
- Authentication and authorization review
- Supply-chain and CI configuration review
- Threat modeling
- Secure code review
- Verification of suspected vulnerabilities
- Remediation planning and fix generation

These workflows naturally fit MoonMind's repository, report, evidence, remediation, and verification capabilities.

## 2. Use a generic security tool-pack contract

Specialized binaries normally live in separate immutable tool images and run through the generic Container Job or executable-tool substrate.

A security tool pack declares:

- Immutable image and tool versions
- Portable entrypoint and invocation contract
- Input and output schemas
- Required filesystem and process capabilities
- Required network and egress policy
- Secret slots and credential class
- CPU, memory, time, and concurrency limits
- Expected artifacts, evidence, and finding types
- Cancellation, failure, and cleanup behavior
- License, SBOM, provenance, and conformance checks

Skills and presets orchestrate these tools. MoonMind should not add a new backend service, permanent container, worker fleet, or first-class runtime for every scanner.

## 3. Revisit PentestGPT as orchestration, not infrastructure

PentestGPT or a similar project may provide value as:

- An assessment-planning agent
- A tool-selection and sequencing Skill
- A guided hypothesis-validation workflow
- A finding correlation and interpretation layer
- A reporting and remediation coordinator

It should not become the cybersecurity architecture itself. Individual tools remain independently versioned, policy-bound, observable, and replaceable. A PentestGPT-style agent receives only the tools, target scope, network access, credentials, and time budget authorized for the run.

Any prior special-purpose PentestGPT execution path should be replaced by the generic tool and agent substrates rather than revived as permanent parallel infrastructure.

## 4. Guide non-specialists through scope and authority

A cybersecurity workflow begins with understandable questions:

- What are you evaluating?
- Do you own it or have authorization to test it?
- Is the input source code, an image, an artifact, an internal service, a lab, or an external target?
- What outcome is required?
- What targets and exclusions define the scope?
- What level of active testing is permitted?
- What time, traffic, and resource budget applies?
- Who must approve impactful actions?

MoonMind turns those answers into a durable scope and rules-of-engagement artifact. The user sees a plain-language summary before execution begins.

## 5. Use graduated capability levels

Security capabilities advance through explicit levels:

1. **Repository and artifact analysis.** No target network access. This is the default entry point.
2. **Authorized internal or lab assessment.** Bounded interaction with explicitly scoped targets through an enforced network profile.
3. **External or impact-capable assessment.** Available only with exact target approval, restricted-egress evidence, operator authorization, rate limits, and required review.

Ordinary users never receive arbitrary shell, Docker, routing, firewall, or network authority merely because a security tool requires those capabilities internally.

## 6. Normalize findings, reports, and evidence

Security tools produce a common finding envelope while preserving tool-specific extensions:

- Title and category
- Severity and confidence
- Affected component or target
- Evidence references
- Reproduction or validation state
- Plain-language impact
- Recommended remediation
- Relevant standards or weakness identifiers
- False-positive, accepted-risk, and disposition state
- Fix and verification linkage

Outputs include human-readable reports and machine-readable formats such as Markdown, HTML, JSON, and SARIF where appropriate.

## 7. Close the loop through remediation and verification

A finding can produce:

- A remediation plan
- A local patch or saved-work branch
- A pull request or merge request
- A configuration change
- A compensating-control recommendation
- A verification workflow using the original tool and scope
- Before-and-after evidence
- A final resolved, mitigated, accepted, or unresolved disposition

MoonMind's value is not merely launching scanners. It connects findings to durable engineering work and proves whether the resulting change addressed the issue.

## Milestone 2 is complete when

A non-specialist can select a guided security workflow, establish an authorized scope, run appropriate tools without learning their command-line interfaces, understand the findings, produce remediation work, and verify the outcome. Every active action is tied to an exact target, policy, tool version, network boundary, approval, and evidence trail. The main MoonMind image does not become a large security-tool distribution.

---

# Milestone 3: Make MoonMind Feel Smaller

## Outcome

The product exposes the complexity users need, not the complexity the platform happens to contain.

A normal user can move from startup to useful work through a small number of understandable decisions. Operators can still inspect and control the underlying profiles, policies, hosts, evidence, and compatibility state.

## Standard workflow experience

The default experience centers on:

1. What should MoonMind do?
2. What source or context may it use?
3. Which agent or capability should perform the work?
4. Where should the result go?

Presets and schema-driven forms cover normal workflows. Raw JSON, internal identifiers, and provider-specific configuration remain advanced escape hatches.

## Progressive disclosure

The UI distinguishes among:

- **Standard users**, who see workflows, sources, agents, connections, outputs, chat, and results.
- **Advanced users**, who see model tiers, policies, schedules, verification, publication choices, and bounded overrides.
- **Operators**, who see Provider Profiles, Agent Profiles, policy versions, hosts, readiness, support evidence, cleanup, and audit history.
- **Engineering diagnostics**, which may expose materializers, realizers, support keys, lease generations, runtime packs, and raw conformance evidence.

Advanced mode is a coherent product state based on permissions and deployment policy, not a collection of unrelated flags scattered through the interface.

## Guided setup and readiness

Configuration appears at the point where it matters.

Examples include:

- A provider connection is required before a selected agent can run.
- A workflow can run locally without GitHub, but remote publication requires a repository connection.
- A selected security workflow requires an approved network profile.
- A model is temporarily at capacity and will retry with the same profile.
- A result can be downloaded now and published after a connection is added.

Errors explain what failed, what authority or evidence is missing, what remained unchanged, and which safe action can resolve the problem.

## One live work surface

Workflow Detail and Workflow Chat become the primary place to understand active work.

The surface combines:

- Current objective and step
- Agent messages
- Tool and command summaries
- Approval requests
- Resources and artifacts
- Progress and waiting reasons
- Steering and cancellation
- Terminal result
- Suggested continuation, remediation, or verification actions

Deep technical evidence remains available without overwhelming the default presentation.

## Simplify configuration and terminology

The product should:

- Prefer goal-oriented profiles and presets over raw runtime configuration
- Derive forms from versioned schemas
- Use consistent vocabulary across create, detail, settings, and diagnostics
- Collapse duplicate readiness and status concepts
- Keep the normal Docker Compose deployment small
- Hide unsupported or experimental combinations by default
- Make expert imports and raw references visually distinct from normal setup
- Avoid exposing environment variables as the normal management interface

## Remove obsolete surfaces

After Milestone 1 establishes the supported runtime platform, remove or isolate:

- Duplicate direct-runtime authoring paths
- Legacy host configuration
- Deprecated environment flags
- Duplicate settings and readiness pages
- Runtime-specific forms that shared schemas can generate
- Stale experimental routes
- Unmaintained adapters
- Dead feature scaffolding
- Redundant dependencies and always-on services
- Documentation describing unsupported product paths

Deletion remains evidence-gated where Temporal replay, active executions, rollback, or historical reads depend on compatibility code.

## Measure simplification

Useful measures include:

- Time from first startup to first successful result
- Number of required fields for a standard workflow
- Percentage of workflows created without raw JSON editing
- Percentage of common setup failures resolved from product guidance
- Number of user-visible runtime-specific concepts
- Number of settings destinations required for normal setup
- Number of active legacy runtime and connector implementations
- Frequency of contradictory status or readiness messages

## Milestone 3 is complete when

A new user can start MoonMind, select a useful workflow, understand required setup, run it, interact with it, and retrieve the result without knowing what a Host Class, runtime pack, materializer, or Temporal Activity is. An operator can still trace every important decision and inspect the exact underlying evidence.

---

# Milestone 4: Expand Through Connectors and Replaceable Capabilities

## Outcome

MoonMind can consume new sources and publish new outcomes through stable connector, workspace, output, context, and memory contracts.

Integrations are independently versioned, capability-described, policy-bound, and replaceable. Adding a provider does not cause its terminology, credentials, or assumptions to spread through the core workflow model.

## Common connector model

Every connector exposes a versioned description of:

- Provider and connector identity
- Supported capabilities
- Authentication methods and required scopes
- Connection and account profiles
- Object and resource identity
- Read, write, search, and subscription operations
- Cursors, pagination, webhooks, and rate limits
- Idempotency and reconciliation behavior
- Data classification, retention, and audit support
- Health, readiness, installation, and update ownership

Secrets remain behind SecretRefs or Provider Profile style connections. Workflows receive capabilities and safe resource references rather than reusable credential bodies.

## Separate Git transport from code-host APIs

MoonMind distinguishes:

- **Git remote operations:** clone, fetch, branch, commit, push, bundle, and remote refs.
- **Code-host operations:** issues, pull requests, merge requests, review threads, checks, releases, and repository metadata.

This supports GitHub, GitLab, Bitbucket, generic authenticated Git remotes, local Git repositories, and future version-control systems through separate adapters.

GitHub remains a strong built-in provider, but not a structural assumption in workspace, publication, review, or UI contracts.

## Multiple connections per provider

A deployment can configure:

- Multiple GitHub identities or installations
- Multiple GitLab instances
- Multiple provider accounts
- Different credentials for different repository sets
- Read-only and publication-capable connections
- User, team, project, and deployment-owned connections

A workflow records the exact connection profile it used. It never selects whichever global credential happens to be present.

## Portable output destinations

A workflow result can be preserved or published as one or more of:

- Durable MoonMind artifact
- Markdown, HTML, JSON, SARIF, or another structured report
- Patch file
- Git bundle
- Workspace archive
- Local commit or branch
- Remote branch
- Pull request or merge request
- Issue, ticket, or comment
- Connector record
- Webhook event
- Object-store artifact

The execution engine returns a canonical result and evidence model. Output adapters decide how that result is delivered.

Failure to publish to one external destination does not erase the underlying result or require the agent work to be repeated.

## Omnigent plugins and partner capabilities

Where Omnigent or another partner exposes maintained plugins, connectors, or tools, MoonMind should prefer their declared capabilities instead of independently recreating every integration.

MoonMind still owns:

- Which integration and version are trusted
- Which connection and scopes are authorized
- Which workflow may use it
- What data may be transferred
- How side effects are recorded
- How retry and reconciliation behave
- What evidence establishes support

Discovery is not trust. Unsupported integrations may be visible without being launchable.

## Replaceable RAG and memory providers

MoonMind retains the **context authority contract**:

- When context is requested
- Which tenant, repository, workflow, user, and security scope applies
- Which source classes are trusted
- Provenance and evidence references
- Recency and freshness policy
- Token and result budgets
- Prompt-injection framing
- Redaction, retention, revocation, and deletion
- The exact ContextPack delivered to an agent
- Run-derived history and fix patterns tied to MoonMind evidence

Replaceable providers may own:

- Source connectors and ingestion
- Parsing and transformation
- Embeddings
- Vector storage
- Ranking and retrieval
- Generic long-term memory services
- Connector-managed synchronization

The architecture should support interfaces such as:

```text
ContextSourceProvider
RetrievalProvider
MemoryProvider
```

An Omnigent plugin can implement one or more of these interfaces. Qdrant, LlamaIndex, Mem0, or another local implementation can remain optional profiles rather than mandatory MoonMind-operated infrastructure.

MoonMind should retain a local implementation only when it materially improves zero-configuration operation, offline use, security control, or evidence quality. Temporal, Postgres, artifacts, and source control remain authoritative records. Retrieval indexes and memory services remain derived, replaceable projections.

## Partner-maintenance policy

For each external runtime, connector, tool image, or memory provider, MoonMind owns:

- Version pinning
- Compatibility metadata
- Security review
- Capability normalization
- Conformance tests
- Release and rollback evidence
- Deprecation and unsupported-state behavior

MoonMind should fork or internalize an integration only when the upstream boundary cannot meet required security, resilience, observability, portability, or maintenance guarantees.

## Milestone 4 is complete when

MoonMind has proven the extension architecture with at least one non-GitHub Git or code-host provider, one non-version-control connector, multiple connections for the same provider, several interchangeable output destinations, one externally maintained tool or plugin, and one replaceable retrieval or memory backend. These integrations evolve without changing the core workflow engine.

---

## Milestone sequencing

Milestone 1 is the primary near-term dependency because it establishes the common runtime, session, chat, credential, workspace, and evidence plane.

Milestone 2 can begin during Milestone 1 with the generic tool-pack contract, repository security workflows, normalized findings, and report outputs. Network-active security workflows remain gated on proven target authorization and restricted-egress enforcement.

Milestone 3 is continuous, but the largest deletions and vocabulary simplifications follow Milestone 1 cutover decisions. Simplifying too early must not hide distinctions still required for safe migration, replay, or rollback.

Milestone 4 interface design can begin early. Broad connector expansion follows once runtime and user-facing abstractions are stable enough that integrations do not target temporary product concepts.

Detailed dependency maps, issue ownership, rollout cohorts, and implementation order belong in the temporary tracker and GitHub issues.

---

## Omnipresent goals

Every milestone is gated by these durable properties:

- **Security.** Credential, filesystem, network, target, tool, publish, approval, retrieval, connector, and control boundaries are enforced at trusted substrate boundaries. Workflows and hosts receive capabilities, refs, and immutable snapshots, not raw infrastructure authority or reusable credential bodies.
- **Resilience.** Runs prefer idempotent retry, evidence-gated resume, branch isolation, bounded degraded mode, portable saved work, and durable cleanup over silent restart from scratch. Provider Profiles, billing-relevant settings, constraints, source authority, target scope, and checkpoint authority are never silently substituted.
- **Observability.** Live state, terminal outcomes, denials, degraded behavior, artifacts, findings, connector side effects, cleanup, recovery decisions, retrieval delivery, runtime-pack identity, host-image identity, materializer identity, tool-image identity, and rollout state are inspectable through MoonMind-owned projections, manifests, audit events, and telemetry.
- **Simplicity.** Each new capability should reduce or bound the reachable failure state space. Common paths use one explicit contract rather than parallel aliases, runtime-specific branches, and layered fallbacks.
- **Portability.** Workflows, Skills, tools, context, and outputs should remain usable through standard files, CLIs, containers, Git, artifacts, and provider-neutral references where practical.
- **Maintainability.** MoonMind owns coordination and trust boundaries. Providers and partners retain ownership of replaceable runtime and integration implementations wherever feasible.

---

## Completion and evidence rules

Roadmap status follows evidence, not issue bookkeeping:

- A merged implementation or closed issue may establish useful substrate without satisfying its full acceptance claim.
- A PR whose own verifier reports `ADDITIONAL_WORK_NEEDED`, `BLOCKED`, missing live proof, or unexecuted controlling tests does not close the corresponding roadmap gate.
- A task that requires credentialed, protected, browser-originated, restart, network-enforcement, target-scope, connector-conformance, or provider-conformance evidence remains open until that evidence is independently resolvable and linked.
- A workflow file, fake provider, semantic action stub, self-asserted passing field, caller-supplied expected event list, installed CLI, installed plugin, or configured network is not live support proof.
- “Supported” means the support matrix links passing evidence for that exact combination. “Implemented,” “foundation,” “installed,” “connected,” and “designed” are weaker states.
- Normal product paths fail closed rather than silently substitute a Provider Profile, host mode, policy, network, credential, runtime, harness, realizer, source, target, repository state, checkpoint, retrieval scope, connector, or output destination.
- One shared image does not allow evidence from one harness or tool to qualify another.
- Issue closure never rewrites historical runtime provenance or removes the obligation to preserve Temporal replay.
- A closed tracker with known residual work must be reopened or receive a follow-up issue before the roadmap can treat the acceptance claim as owned.
- External publication failure does not erase an independently valid underlying result, artifact, checkpoint, local branch, patch, or report.
- A capability is not simple merely because advanced evidence is hidden. The normal path and the advanced diagnostic path must remain consistent views of the same authority and outcome.

---

## Declarative design first

Each milestone begins by reconciling the canonical declarative documents that own its target-state contracts. Implementation that discovers drift ends with a documentation reconciliation pass.

This roadmap states durable desired state. It is not the sole architecture specification and is not the place for dated implementation diaries, per-PR status, or phased rollout checklists.

The primary runtime-provider strategy owns long-term Omnigent direction. The harness platform design owns generic planning and realization mechanics. OAuth, Provider Profile, bridge, control-plane, checkpoint, remediation, security-tool, connector, context, memory, output, and conformance documents own their narrower contracts.

---

## Durable acceptance-claim identifiers

These exact identifiers are pinned by `tests/unit/docs/test_final_docs_cleanup_policy.py` and `tests/integration/docs/test_final_docs_cleanup_contract.py`. They remain stable even when active execution milestones are renumbered in the tracker:

- [ ] **5.1 Checkpoint boundary and completeness** — implementation foundation landed; independently resolvable acceptance evidence remains required.
- [ ] **5.4 Resume-from-checkpoint default flow** — production orchestration must choose validated reattach, cold restore, branch-required, or explicit unavailable outcomes.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — isolated corrected-instruction turns, selectors, compare, promote, and archive in Workflow Detail.
- [ ] **6.2 Omnigent remediation context enrichment** — bounded evidence with target-authorized typed actions and closed residual authority gaps.
- [ ] **7.1 Initial context injection for Omnigent** — durable controlling verification evidence for first-message `ContextPack` injection.

Changing an identifier above is a deliberate owner-approved invariant change. Update the pinning contract tests in the same change rather than deleting an identifier to make a roadmap edit pass.

---

## Durable substrate assumptions

These are shipped, durable capability statements that the roadmap treats as baseline desired state rather than active milestone checklists:

- Omnigent uses the canonical external-agent identity `agentKind=external`, `agentId=omnigent`.
- The generic harness platform provides immutable catalogs, Agent Profiles, credential bindings, materializers, Host Classes, execution plans, runtime bindings, support keys, and a generic host realizer.
- `integration.omnigent.execute` can create or reattach a session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult` for supported combinations.
- Profile authorization, provider leases, host bindings, host leases, credential generations, lifecycle transitions, and redacted preflight evidence are durable without placing credential bodies in Temporal, bridge, checkpoint, workspace, or artifact payloads.
- Static and on-demand hosts use complete image references, UID/GID `1000:1000`, `/home/app`, separate provider credential and Omnigent state, read-only root filesystems, bounded temporary storage, deterministic ownership labels, and explicit cleanup.
- OpenCode has the first deployment-backed generic Host Class, materializer, image, and execution path. That foundation should be generalized rather than preserved as a permanent OpenCode-only architecture.
- The run workflow records per-step Omnigent identity, so Omnigent checkpoint captures select the `external_state_ref` lane, and restore validation rejects stale, mismatched, non-artifact, local-path, or credential-shaped authority.
- The Checkpoint Branch API and persistence model already support create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, immutable instruction digests, workspace policy, git binding, and remediation-created branches.
- Persistent immutable Omnigent policy versions and Agent Profile versions exist with authenticated lifecycle APIs, effective-launch linkage, and bridge evidence. Complete cross-boundary consumption, approvals, ownership, and product-management journeys remain tracked execution work.
- Report artifacts can represent unit-test, benchmark, security, compliance, and related workflow outputs without forcing every producer into one report schema.
- Local artifacts, workspaces, Git state, and exported files remain valid sources of durable evidence even when no hosted publication provider is configured.

---

## Where the imperative tracker lives

Milestone-by-milestone status, current-state notes, per-PR disposition, the open and closed tracker map, priority ordering, rollout sequencing, and evidence links are dated execution scaffolding, not durable desired state.

They live in the disposable tracker at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md) and should be refreshed or deleted as execution proceeds.

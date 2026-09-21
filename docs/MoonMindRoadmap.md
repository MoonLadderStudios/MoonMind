# 🌙 MoonMind Roadmap

**Document Class:** Canonical declarative  
**Status:** Product destination, not implementation or deployment evidence  
**Updated:** 2026-09-21

MoonMind is a secure, resilient, and observable single-user application for agentic work. **Reliability through simplification is the first priority.** Make the ordinary execution, chat, saved-work, recovery, and update paths work before expanding the machinery around them.

One operator may run concurrent workflows, use multiple provider/repository accounts, and operate independent deployments. This is not an application-account, team-role, or tenant product. [AGENTS.md](../AGENTS.md) and the [single-user design](SingleUserApplicationDesign.md) govern that direction.

This roadmap keeps the four product destinations and durable acceptance references. Providing modules own technical contracts. GitHub issues own remaining implementation. A roadmap paragraph, closed issue, installed image, or proposed design does not establish runtime support.

## Advance organizer

MoonMind should let its operator describe useful work, select the needed source and capability, observe and steer execution, and retrieve a durable result. Omnigent supplies one agent-runtime lifecycle behind thin harness adapters. Temporal supplies durable orchestration. Existing artifact, workspace, credential, and publication owners preserve work and authority when a component fails.

## Product destination

```text
operator intent and explicit constraints
  -> existing Workflow / preset / interactive entrypoint
  -> selected harness and Profile with admitted source and operations
  -> existing Omnigent session or bounded tool job
  -> observable progress and independently preserved result
  -> verification, continuation, or admitted publication
  -> recovery and cleanup through the same owners
```

The normal experience asks what to do, what it may use, which capability should run it, and where the result should go. Runtime packs, materializers, host classes, internal IDs, and lease generations belong in existing advanced controls or diagnostics only where useful. Hiding them does not excuse duplicated systems underneath.

## Governing principles

Give each responsibility one owner. Prefer deleting obsolete work or extending a small existing interface over adding another controller, registry, state machine, verifier, or fallback. Skills retain their portable semantics, rather than becoming a second native implementation of the same decisions.

Recover transient failures within a bound and reconcile uncertain effects before repeating them. Preserve completed steps, original intent, and the only recoverable workspace. A publication, preview, projection, or cleanup failure must not erase successful compute or verified saved content. Basic diagnostics remain readable without an LLM or working interactive chat.

Record actual source, image, and attempt provenance. Compatibility depends on required interfaces, behavior, schemas, and actual Temporal replay needs, not equal SHAs, image digests, patch versions, or a replacement compatibility fingerprint. Preserve integrity checks and legitimate source-control concurrency protection.

Security remains enforced at the real boundary. Operator admission, machine capability, repository access, model credentials, registry access, and artifact permissions are distinct. Missing or revoked authority never causes credential shopping, source substitution, wider access, or unrequested paid execution. Existing explicit user approvals remain effective without a new universal approval step.

## Target ownership split

| Responsibility | Existing owner |
| --- | --- |
| Durable Workflow decisions, timers, retries, and control | Temporal and the owning MoonMind Workflow. Side effects run through Activities or existing services. |
| Provider process and live session protocol | Omnigent with thin Codex, Claude Code, OpenCode, and other approved harness adapters. |
| Admitted session use, durable correlation, chat access, and captured evidence | Existing MoonMind runtime, bridge, workspace, and artifact boundaries. |
| Credentials and capacity | Existing Secrets and Provider Profile owners. Repository connections remain separate from model accounts. |
| Saved content, restore, and retention | Existing artifact and checkpoint/workspace owners. A result manifest is an index, not another storage service. |
| Immediate and deferred publication | Existing compiler and publisher with Git/Lore provider authority and exact-effect reconciliation. |
| Deployment and independent repair | One portable controller using the installed Docker Compose stack, local durable progress, and changed-service recreation. |
| Specialized tools and connectors | Maintained external implementations invoked through existing jobs and thin adapters, not a new permanent service per integration. |

Direct or profile-bound legacy runtimes remain only for actual supported consumers during their controlled transition. Preserve their recorded history and saved work, but do not expand or retain them simply because a past roadmap listed them. Static and on-demand host behavior stays qualified where still supported, without requiring a separate architecture for every harness.

# Milestone 1: Complete the Omnigent Agent Platform

Codex, Claude Code, and OpenCode should share the normal execution, session, chat, evidence, recovery, and cleanup lifecycle. Genuine provider differences stay in small adapters. Adding a harness must not require another top-level Workflow, session database, or launch coordinator.

A normal run must support usable progress and diagnostics, same-session follow-up where supported, bounded reconnect, steering/cancellation, approvals and attachments, and truthful terminal results. Browser disconnection must not cancel independently admitted work. Recorded evidence remains readable after the live host is unavailable. Session reattachment and workspace restoration are different capabilities, with explicit unavailable outcomes where necessary.

Presets, schedules, batches, reruns, Checkpoint Branches, remediation, and verification reuse that lifecycle. First-message and subsequent delivery are idempotent. Changed instructions or authority use the existing new-turn, branch, or execution admission rather than rewriting history or inheriting unrequested permission. Preserve model, cost/privacy, source, and publication choices across retries.

Provider setup uses the existing Profile form and credential lifecycle for OAuth, API keys, and supported credentialless access. Multiple accounts and concurrent work remain supported. A host consumes admitted credentials rather than initiating an unexpected second login. Share host distribution where practical while keeping credentials, sessions, and resource ownership isolated.

Useful scratch, report, anonymous-source, artifact/checkpoint, and authorized existing-workspace work does not require GitHub publication. Save the required content and references before destructive cleanup. Failed or canceled compute can retain useful output without becoming successful compute. Publication-only recovery uses saved bytes and fresh destination authority without another model run or the original source PAT.

Worker/host restart, unavailable capacity, lost acknowledgments, credential rotation, interrupted capture, and stale cleanup must recover through their existing owners. Fix concrete failure paths independently instead of waiting for App enrollment, every source variant, or the whole milestone. Do not create a finalization daemon or permanent compatibility fleet to satisfy the roadmap.

**Completion:** the supported harnesses deliver these shared guarantees through actual product journeys, including chat, saved results, and recovery. Unsupported behavior is explicit and does not silently substitute another runtime or credential. A type definition, installed tool, or mocked success alone is insufficient.

**Providing contracts:** [runtime-provider strategy](Omnigent/PrimaryRuntimeProviderStrategy.md), [harness platform](Omnigent/OmnigentHarnessPlatformDesign.md), [repository access and durable work](RepositoryAccessAndWorkspaceDesign.md), [Workflow Publishing](Workflows/WorkflowPublishing.md), [artifact presentation](Artifacts/ArtifactPresentationContract.md), and [deployment updates](Steps/DockerComposeUpdateSystem.md).

# Milestone 2: Build a Guided Cybersecurity Workbench

The longer-term outcome is authorized software-security analysis that explains its scope, evidence, findings, and verified remediation to a non-specialist. It is not an unrestricted collection of offensive tools or a special-purpose PentestGPT runtime.

Begin with one passive repository/artifact analysis through the existing Container Job or executable-tool substrate. The first slice uses one concrete tool, immutable authorized input, read-only bounded execution, no assessment-target network access, and existing result artifacts. Dependency/inventory and secret-exposure analysis are suitable bounded starting outcomes. No scanner collection, new tool-pack database, universal findings schema, or new feed service is required.

Preserve native structured output and a readable summary, with interoperable exports where useful. Separate job success, declared coverage, findings, and incomplete analysis. Redact sensitive matches and treat scanner output as untrusted. Missing feeds, unsupported inputs, timeout, or truncated output cannot become a clean finding. Feed acquisition, if needed, is separately bounded and recorded rather than an excuse to upload source or open target egress.

Broader goals include dependency/SBOM, static code, infrastructure/configuration, image and supply-chain analysis, threat modeling, and remediation verification. Reuse maintained tool images and portable Skills. Fix generation or publication is separate admitted work with before/after evidence. Existing image SBOM/provenance is useful release metadata, not proof of a repository-security product journey.

Network-active internal/lab assessment remains a later capability with explicit targets, exclusions, permitted actions, resource/traffic limits, and enforced egress. External or impact-capable assessment additionally retains its required approvals and evidence. A prompt, tool installation, or container configuration alone does not prove target confinement.

**Completion:** an authorized operator can run the supported guided analysis, understand its limitations, produce remediation, and verify the outcome without a scanner-specific MoonMind architecture. The main application does not become a security-tool distribution.

**First implementation owner:** [#3970](https://github.com/MoonLadderStudios/MoonMind/issues/3970). Passive work may proceed during runtime convergence when its actual job/source/artifact dependencies suffice. It does not displace concrete reliability repairs or depend on all App/CLI work.

# Milestone 3: Make MoonMind Feel Smaller

Simplification is continuous and begins now, not after the other milestones. Improve the existing Create, Settings, Workflow Detail, and Chat surfaces rather than build new wizards and dashboards for every subsystem.

One operator uses ordinary and advanced views. These are disclosure levels, not human-role tiers or a new global advanced-mode state machine. Standard forms show useful choices and preserve explicit drafts. Backend declarations supply supported choices, while stale requests and unavailable advisory catalogs do not overwrite valid configuration or block unrelated edits.

Show the original objective, current step/agent interaction, waiting reason, available outputs, and valid next action together. Distinguish execution failure, missing evidence, unavailable diagnostics, incomplete save, and failed publication. Read models remain repairable projections, not another execution authority. Recovery should retry the missing phase instead of replaying completed work.

Remove dead routes, duplicate runtime forms, unused settings, obsolete dependencies, and copied guidance with their real consumers. Retain only necessary migration/history behavior. Do not preserve obsolete machinery merely to keep old tests green, but do not drop security or saved-work coverage for still-supported behavior.

**Completion:** the ordinary journey works with understandable setup and few required decisions, while the operator can inspect the same underlying evidence. Time to a useful result and recurring failure patterns guide improvements, without a mandatory telemetry platform or field-count quota.

**Providing contracts:** [single-user application](SingleUserApplicationDesign.md), [Temporal architecture hub](Temporal/TemporalArchitecture.md), [source-of-truth and projections](Temporal/SourceOfTruthAndProjectionModel.md), and the existing UI and subsystem docs they link.

# Milestone 4: Expand Through Connectors and Replaceable Capabilities

Add concrete integrations through existing connection, workspace, artifact, Skill, and publishing contracts. Keep provider-specific authentication, pagination, object semantics, and retry behavior in thin adapters. Do not commission a universal connector framework before a real second consumer needs an abstraction.

Git transport and code-host APIs are distinct. Multiple repository identities or App installations use deterministic selected connections, not ambient token discovery. Connections are instance resources with scoped execution authority, not user/team tenancy. GitHub App acquisition and event-to-workflow dispatch are separate capabilities.

The first event integration is one opted-in repository action through existing preset/admission and Temporal dispatch, with durable delivery identity and bounded reconciliation. A valid webhook signature or App installation does not authorize an arbitrary commenter to spend model budget. Local-only operation does not require public ingress. No new executor, webhook service, event-rule language, or fleet-wide dedupe system is a prerequisite.

Portable results include artifacts and readable/structured reports, applicable patches/bundles/archives, local Git output, and explicitly admitted remote branches, PRs, tickets, or connector records. Use output adapters through the existing result/publisher owners. Failure at an external destination does not erase valid saved content or require another agent run.

MoonMind retains explicit context authority, provenance, budgets, injection framing, redaction, and the exact delivered input. It does not own a native vector database, embedding/indexing platform, or Manifest replacement. Maintained external context/tools are usable only through supported scoped interfaces, not implicit fallback. Retained exact context is not a claim of semantic memory.

Prefer partner-maintained runtimes, tools, and connectors. Record real provenance and verify changed capabilities without requiring equal release strings across unrelated components. Do not expose an unimplemented integration as ready or fork an entire provider merely to avoid a small missing adapter.

**Completion:** the extension approach works with multiple connections, at least one non-GitHub Git/code-host provider, one non-version-control connector, useful alternative output destinations, and an externally maintained tool/plugin without a second core workflow engine.

**Providing owners:** [repository-access design](RepositoryAccessAndWorkspaceDesign.md), [Secrets System](Security/SecretsSystem.md), [Workflow Publishing](Workflows/WorkflowPublishing.md), and [#3967](https://github.com/MoonLadderStudios/MoonMind/issues/3967) for the first event path. Production App acquisition remains with [#4022](https://github.com/MoonLadderStudios/MoonMind/issues/4022).

## Milestone sequencing

Reliability and simplification govern every milestone. The shared Omnigent path is the near-term platform direction, but small fixes to existing behavior do not wait for complete convergence. Passive security work can use the current job substrate. Broader integration work must demonstrate a concrete need instead of expanding temporary architecture.

Prefer one in-place deployment controller and bounded interruption to permanent candidate/retained fleets and promotion machinery. Actual incompatible Workflow/schema changes still need compatible replay or a controlled transition. A fresh installation without legacy consumers does not need retirement machinery. Independent deployments have independent state and observations, not an assumed shared database or all-device readiness gate.

## Completion and evidence rules

Reuse relevant current-candidate GitHub Actions results and focused local/container reproductions. Shared production mechanisms can share representative journeys, with focused tests for genuinely different adapters or authority/storage boundaries. Do not repeat the full harness/source/profile/image/fault cross-product, introduce a conformance registry, or require broad local suites before an authorized PR.

Required behavior must be exercised at the boundary claimed. A real database race needs real database evidence, a served browser journey needs its API, and a durable restore needs actual saved content. Missing required functionality cannot be relabeled unsupported to close an issue. Partial, failed, canceled, or unexecuted checks remain explicit. A report validator or green unrelated check is not a product pass.

Live provider, deployment, and physical-event observations are separately authorized and tracked. They are not silently waived, but unavailable access is not a code defect or a reason to burn implementation retries. Use an authorized automated evidence path where possible, preserve the candidate, and never claim continuation is scheduled until its owner accepts it. Existing explicit human decisions remain explicit, not a default handoff for ordinary tool failure.

Retain actual histories, immutable input/result evidence, admitted authority, and necessary recovery data during removal. Deleting a patch requires the applicable history/retention/reset evidence. A pause or maintenance window cannot make an incompatible recorded history disappear. Evidence can identify an exact artifact without imposing exact-version compatibility everywhere.

## Durable acceptance-claim identifiers

These references retain unresolved product obligations from the earlier roadmap. They are not assertions that current tests pass, or a machine-readable completion registry. Preserve their meaning while consumers need them, not a mandatory Markdown layout or documentation test.

| Identifier | Retained outcome | Existing implementation/evidence owners |
| --- | --- | --- |
| **5.1 Checkpoint boundary and completeness** | Scoped, independently readable saved content and required references support restoration after source disposal. | Checkpoint/artifact owners and #4015, #4016, #4017. |
| **5.4 Resume-from-checkpoint default flow** | The real product chooses validated reattach, cold restore, branch-required, or explicit unavailable behavior without repeating accepted work. | #3510 and current workspace/session recovery owners. |
| **5.5 Checkpoint Branch UI and runtime-profile gaps** | Isolated corrected-instruction turns, normal Profile selection, continue/fork, comparison, promotion/publication, and archive retain exact source/result authority. | #3621, #3622, #4016 and the existing Workflow Detail/branch owners. |
| **6.2 Omnigent remediation context enrichment** | Bounded authorized evidence, useful typed actions, result-bound verification, cumulative progress, and retained qualification support recovery. | #3512, #3622, #3624, #3626 and their existing action/verification owners. |
| **7.1 Initial context injection for Omnigent** | Durable evidence identifies the authorized ContextPack actually delivered with the first message and its verification. Native RAG is not restored to satisfy this claim. | Existing context delivery/evidence owners; historical #3513 and remaining handoff in #3965. |

Documentation is reviewed, not unit-tested for wording, headings, counts, or checkbox state. Preserve actual executable schema, parser, security, replay, and generated-catalog tests with their code owners. No new semantic-prose validator is required.

## Where the imperative tracker lives

The [temporary execution tracker](tmp/MoonMindRoadmapExecutionTracker.md) retains historical observations and unresolved handoff material. Its old PR statuses, role models, exact-version matrices, and promotion mechanics do not override the current direction. [#3965](https://github.com/MoonLadderStudios/MoonMind/issues/3965) owns transferring the remaining real obligations to current issues before disposal. This roadmap revision does not declare that handoff complete or any deployment migrated.

The [earlier roadmap](https://github.com/MoonLadderStudios/MoonMind/blob/6fdaab848e8f9fd9c5279ea36186482cab05733d/docs/MoonMindRoadmap.md) remains available as historical context. Long-term module contracts stay with their providers, not in a second roadmap or growing status ledger.

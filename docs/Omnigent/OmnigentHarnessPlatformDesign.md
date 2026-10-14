# Omnigent Harness Platform Design

**Document Class:** System / Feature Design View  
**Owners:** MoonMind Platform  
**Scope:** The generic model for running any trusted Omnigent harness through MoonMind, with the existing Codex lane as one conforming realization.

## Related documents

- [`README.md`](./README.md), [`ContractOwnership.md`](./ContractOwnership.md), [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) — module entrypoint, ownership, and package boundaries
- [`SharedHostImage.md`](./SharedHostImage.md), [`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md), [`OmnigentHostMountedTools.md`](./OmnigentHostMountedTools.md) — shipped host image, runtime packs, Host Classes, credential materialization, mounted tools
- [`OmnigentAdapter.md`](./OmnigentAdapter.md), [`OmnigentBridge.md`](./OmnigentBridge.md), [`AgentProfiles.md`](./AgentProfiles.md), [`PolicyAuthority.md`](./PolicyAuthority.md)
- [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), [`NormalCodexProductPathReconciliation.md`](./NormalCodexProductPathReconciliation.md), [`CodexSupportAndCutover.md`](./CodexSupportAndCutover.md)
- [`ControlPlaneAggregates.md`](./ControlPlaneAggregates.md), [`ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md), [`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`ProviderProfiles.md`](../Security/ProviderProfiles.md), [`SecretsSystem.md`](../Security/SecretsSystem.md), [`SkillSystem.md`](../Steps/SkillSystem.md), [`RepositoryAccessAndWorkspaceDesign.md`](../RepositoryAccessAndWorkspaceDesign.md)
- [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md), [`WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md), [`CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md), [`WorkflowRemediation.md`](../Workflows/WorkflowRemediation.md)

## Summary

MoonMind treats Omnigent as one stable external execution provider whose discovered harnesses are admitted through immutable contracts instead of one MoonMind runtime per harness. A workflow selects a versioned Omnigent Agent Profile. MoonMind resolves the agent source, pinned harness build, Skill snapshot, credential-binding set, Provider Profiles, credential materializers, Host Class, launch policy, model configuration, and execution realizer, and compiles those pre-host decisions into one secret-free **execution plan** before acquiring leases or touching a host. After lease acquisition and host realization, a separate fenced **runtime binding** records acquired credential generations, exact-host attestation, model validation, workspace, session, and cleanup authority. The Codex profile-bound lane is one explicit realizer.

MoonMind owns orchestration, authority, credentials, workspace, Skills, publication, evidence, recovery, and cleanup. Omnigent owns provider sessions, runners, harness execution, and the live harness protocol. Discovery never grants trust, and class-level admission never substitutes for exact-host proof.

Shipped behavior for the shared image, runtime packs, Host Classes, and OAuth-home materializers is described in `SharedHostImage.md` and `OmnigentHostOAuth.md`; those documents are the reference for current behavior of §§10–11. The Codex documents describe the Codex specialization; this design extends them without weakening their guarantees.

## 1. Goals and non-goals

- **One identity.** Every harness runs as `agentKind=external`, `agentId=omnigent`; harness, model, Provider Profile, Host Class, realizer, host, and session are nested authority.
- **Catalog-driven, exact support.** Harnesses come from Omnigent, and trust and support bind package, build, vendor runtime, and digests rather than names. Adding an approved harness is catalog, materializer, Host Class, policy, profile, and conformance data, not a coordinator branch.
- **Codex preservation**, **two-stage capability proof**, **shared provider-account capacity**, **immutable per-run Skills**, and **safe extension** (community harnesses visible without receiving secrets or authority).
- **Objective support claims.** Certified support needs evidence for the exact model and realizer; ordinary execution depends on actual authority, credentials, compatibility, capabilities, and policy, not a historical certificate.
- **One recovery model** for retry, continuation, remediation, cancellation, rotation, and janitor work.

Workflows cannot supply raw Docker options, host paths, volumes, host ids, generations, or credentials. Ordinary launches do not install harness software, and neither a Host Class nor an upstream declaration counts as proof of readiness.

## 2. Governing decisions

**Authority split.** MoonMind owns Step ordering, profile and source selection, Skills, Provider Profile selection and capacity, materializer approval, host and network policy, workspace and mutation authority, publication, checkpoints, evidence, and cleanup. Omnigent owns harness registration, agent imports, the host/runner protocol, live sessions, harness-specific message/tool/elicitation behavior, and native resume and fork. No catalog row, bundle, host claim, setup operation, or plugin declaration can broaden MoonMind authority.

**Discovery is not trust.** MoonMind distinguishes discovered, trusted, installed, authenticated, class-admissible, exact-host-attested, launchable, and supported. A discovered harness can appear in Settings while non-launchable; a class-admissible on-demand combination can fail exact-host attestation; a launchable one can stay experimental until conformance evidence exists.

**Codex is a conforming specialization.** The Codex profile-bound implementation is the first registered realizer, selected explicitly before launch rather than as a fallback. A failed generic selection never silently switches to Codex.

**Plans and bindings are separate.** `OmnigentExecutionPlanPayload` holds decisions possible before leases and an exact host exist; `OmnigentRuntimeBinding` holds what was actually acquired or attested. The plan never claims a host or generation, and the binding cannot change plan decisions. One plan can govern several realizations: each rerun, linked continuation, and recurring occurrence owns a binding aggregate keyed by `(planRef, executionScopeRef)`, while Activity retries in a scope reconcile the same aggregate.

**Version handling.** Admission reads the immutable catalog named by `harnessCatalogRef` and checks endpoint and build provenance, never substituting the latest catalog. A different Omnigent release number alone does not veto a plan, because exact-host attestation checks the image and required behavior. New v1 plans omit `omnigentVersion` so older readers can consume them. Missing evidence is a recoverable readiness gap and conflicting evidence rejects launch; see [SharedHostImage.md](SharedHostImage.md#server-compatibility-and-deployment-readiness).

**Non-self-referential digests.** The plan envelope (`moonmind.omnigent-execution-plan-envelope.v1`) holds `planRef`, the digest of the canonical payload bytes, outside the payload, so verification needs no exclusion rule.

**Certificate-independent ordinary admission.** A missing, expired, or unavailable historical certificate never vetoes ordinary admission. Only an explicit `protected` or `deployment` evidence policy selects strict certification. Remaining certificate consumers (bootstrap qualification, `deployment_evidence` gates, and strict branches in readiness and plan-admission validation) are advisory on ordinary paths. The `AdmissionAuthority` validator in `harness_platform.execution_plan` defaults to strict so plans saved before `admissionMode` existed keep their meaning; `reissue_ordinary_admission_for_saved_plan` re-admits one as ordinary.

## 3. Topology and identities

```text
workflow/Step -> Agent Profile snapshot, agent source, ResolvedSkillSet
  -> Execution Planner (catalog + trust, binding set, Provider Profiles,
     materializers, Host Class, launch policy, class admission decision)
  -> digest-addressed plan envelope
  -> fenced control plane (provider leases, host lease, workspace,
     credentials, exact-host attestation, model validation, runtime binding)
  -> stock Omnigent server/host -> runner -> harness -> agent
  -> MoonMind bridge, evidence, publication, checkpoint, cleanup
```

The planner has no provider side effects; the realizer acts through Activities and trusted services under Temporal orchestration.

| Identity | Purpose |
| --- | --- |
| `endpointRef`, `harnessCatalogRef` | Omnigent control plane; immutable catalog snapshot |
| `harnessId`, `harnessImplementationRef` | Canonical harness id; exact package, version, digest |
| `agentProfileSnapshotRef`, `agentSourceRef` | Immutable profile version; upstream or artifact-backed source |
| `resolvedSkillSetRef`, `skillDeliveryRef` | Per-run Skill snapshot and its delivery metadata |
| `providerProfileRef`, `credentialMaterializerRef`, `credentialBindingSetRef` | Account/capacity authority; approved credential presentation; binding-set id, version, digest |
| `hostClassRef`, `launchPolicyRef` | Host environment contract; host mode, limits, network, capture, cleanup |
| `modelConfigDigest`, `executionRealizerRef` | Normalized model configuration; versioned realizer |
| `executionPlanRef`, `executionScopeRef`, `runtimeBindingRef` | Plan digest; owning execution; acquired/attested binding |
| `hostBindingRef`, `hostLeaseRef`, `providerLeaseRef` | Durable host and capacity ownership |
| `omnigentHostId`, `omnigentSessionId`, `chatBindingId` | Exact host, provider session, native chat |
| `supportCombinationKey` | Exact combination qualified by support evidence |

Aliases are accepted only at discovery and input edges, and only the trusted planner chooses `executionRealizerRef`.

## 4. Harness catalog

MoonMind projects the authenticated Omnigent catalog into a bounded immutable snapshot from three views: `/v1/harnesses` (what the endpoint understands), `/v1/agents` (selectable agents), and `/v1/hosts` (connected hosts and readiness). Only the first two are pre-host planning evidence; `/v1/hosts` cannot stand in for an on-demand host that does not exist yet.

A snapshot (`moonmind.omnigent-harness-catalog.v1`) records the endpoint, build digest, plugin-load errors, and per harness its aliases, implementation (package, version, digest, entry point), runtime binary constraints, declared capabilities, and setup steps. Unknown capability values stay unknown rather than being coerced.

Trust is bound to implementation identity, not a name: `core_trusted` (shipped with the approved build), `plugin_approved` (approved package, version, entry point, digest), `quarantined` (discovered; no credentials or workflows), or `blocked`.

New plans keep the profile's pinned catalog as authority and require a current observation attesting the same endpoint, build, and implementation; that observation supplies freshness only. Schedule creation and refresh use the same authority, reading within the admission transaction when one is supplied, and missing catalog authority fails admission before an unresolvable reference is published. An endpoint outage may keep the last catalog for diagnostics but does not make stale harnesses launchable.

## 5. Exact-host attestation

A Host Class says what a host should contain; it does not prove what the selected host contains. Before runner or session creation, the host provides a bounded, credential-free `HostHarnessAttestation` naming host id, Host Class, image digest, Omnigent build, harness implementation, runtime dependency versions and digests, configured state, and capabilities. It passes only when:

- Host Class, architecture, harness id, and the harness package, version, entry point, and digest match the plan and trusted catalog.
- The image digest and build match, or the image is qualified same-repository drift (digest-pinned, observed or operator-pinned, present locally, with a parseable Omnigent version and a live-probed executable). A foreign repository or a changed build label on an unchanged image never qualifies.
- Each required vendor runtime is in the same major.minor series.
- Every required capability is positively reported, and the attestation is fresh and tied to the current host-lease fencing generation.

Where upstream lacks attestation fields, an approved MoonMind verifier at the host-realization boundary inspects the image and binaries without changing runner behavior. A connected static host whose plugin or vendor runtime changes fails closed for new sessions until requalified.

## 6. Agent Profiles

An Agent Profile is the reusable selection surface (source, harness, capabilities, model, workspace, Skills, tools, capture, continuation, publication). It holds no credentials, generations, host authority, or secrets. Profile model and effort are defaults; an explicit workflow model or effort stays authoritative, and the plan and Temporal parameters record the same resolved selection.

A v2 profile (`moonmind.omnigent-agent-profile.v2`) has exactly one source variant:

```yaml
source: {kind: upstream, upstreamId: opencode-native-ui, upstreamVersion: "<v>", upstreamSnapshotDigest: sha256:...}
source: {kind: bundle, bundleArtifactRef: artifact:..., bundleDigest: sha256:..., importReceiptRef: omnigent-agent-import:...,
         importedAgentId: moonmind-opencode-default, importedAgentVersion: "<v>", importedContentDigest: sha256:...}
```

The bundle variant pins both the MoonMind artifact and the resulting upstream import, so a historical plan detects replaced content under the same upstream id. The rest of the document pins the harness and catalog, declares required and preferred capabilities and credential slots, and carries model defaults, workspace, Skills, tools, capture, continuation, publication, and allowed launch policies. v1 profile versions remain replayable through a compatibility decoder that compiles them into the same planning inputs. A deterministic template factory can generate, store, and import a minimal bundle for an approved harness.

## 7. Resolved Skills

The profile's `skills` list is intent, not executable content, because Skill sources change independently. Before plan commitment, MoonMind resolves it through the [Skill System](../Steps/SkillSystem.md) into one immutable per-run snapshot that includes step Skills and Skills declared by dynamic remediation contracts. Admission and scheduled refresh read the same intent as execution (a non-empty `workflow` envelope takes precedence over the persisted `task` envelope). The plan carries only `resolvedSkillSetRef`, its digest, and `skillDeliveryRef`, which describes exposure through `$MOONMIND_ACTIVE_SKILLS_DIR`; Skill bodies and source paths never enter the plan or Temporal history. Retries reuse the same refs, historical plans and schedules keep theirs, and a branch or remediation run can resolve a new snapshot only through a new plan whose lineage records both.

## 8. Provider Profiles and credential bindings

`ManagedAgentProviderProfile` remains the single authority for account, credentials, current generation, concurrency, cooldown, and readiness. Runtime-specific fields on existing profiles act as constraints for the selected materializer. An Agent Profile names credential roles (`primary-model`, `secondary-model`, `embedding-provider`, `vendor-login`), any of which may be optional; repository and publication credentials are capability-scoped MoonMind credentials, not model slots.

A credential-binding set has a stable id and immutable versions, each canonical JSON with a digest, referenced as `omnigent-credential-bindings:<id>@<version>#sha256:<digest>`. Each binding maps a slot to a `providerProfileRef` and `materializerRef`. Edits append a version and never change historical authority.

**Repository bindings.** Repository access uses the v2 union of model and repository bindings, leaving model-only v1 digests unchanged; the general model is described in [RepositoryAccessAndWorkspaceDesign.md](../RepositoryAccessAndWorkspaceDesign.md). The compiler derives source, hosting-tool, and publication roles from the resolved profile and places compact refs in `resolvedTools.repositoryAccess`. Only model bindings participate in Provider Profile capacity, so a repository slot never demands a provider lease. When policy keeps Codex on the model-only profile-bound realizer, ordinary agent plans admit no typed repository authority and keep that realizer's credential (`repository-connection:git-default`); explicitly selecting another connection rejects before plan persistence rather than being silently replaced. The review-only PR workflow uses a `consumer: native` binding admitted only for the exact `github.resolve_pull_request_target` graph, which never reaches an agent realizer.

**Generation ownership.** The plan selects a Provider Profile and materializer, never a generation; the runtime binding records the generation after the lease is acquired, so rotation before first acquisition is harmless. Retries reuse the recorded lease and generation when acquisition authority is unchanged. A new lease or generation for the same scope compare-and-swaps a new binding revision, advances its fence, and clears superseded host/session authority before any provider mutation. Rotation after realization requires credential maintenance to drain or fence the consumer, so a former host, lease owner, or janitor cannot write through the new fence. A post-binding mismatch yields a typed fenced outcome and reconciliation, never a plan change or another Provider Profile.

## 9. Capacity

| Layer | Owner | Meaning | Setting |
| --- | --- | --- | --- |
| Configured profile capacity | Operator | Ceiling a Provider Profile may ever admit | `max_parallel_runs` |
| Effective provider capacity | Runtime | Applied limit, lowered by rate-limit backpressure and restored on recovery | `effective_capacity` |
| Host capacity | Deployment | Concurrent on-demand hosts (default 1) | `MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY` |
| Cold-launch rate | Deployment | Launches that may start per window | `MOONMIND_OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST`, `..._WINDOW_SECONDS` |
| Container-job slots | Deployment | Container jobs launching or running (default 1) | `MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS` |
| Worker capacity | Deployment | Concurrent Activities per fleet | `TEMPORAL_AGENT_RUNTIME_WORKER_CONCURRENCY` |

Effective concurrency is the minimum of all layers, and the admission decision reports the `limitingLayer`. A ceiling of `N` promises only that nothing above `N` is admitted; the host default of one is conservative because no fixed count fits every machine. Lowering the ceiling stops new admission without evicting work, and runtime backpressure never edits the configured value. Waiting work is durable workflow state with an explicit reason; it holds no execution slot and never falls back to another runtime, profile, model, or Host Class.

**Admission sequence.** Before scheduling the long execution Activity, the AgentRun workflow (as lease owner) queues with the ProviderProfileManager for the plan's Provider Profile, then polls a short control Activity that checks host ceiling and cold-launch rate against the host-lease ledger. The poll reserves nothing; the allocating transaction counts hosts and inserts the lease row under a transaction-scoped lock so two replicas cannot take one slot. Without host capacity, provider capacity is released immediately. A request larger than the ceiling is rejected, because waiting would never end and clamping would silently change a billing-relevant value. The Activity then consumes a secret-free admitted-capacity ticket by inspection only, and provider capacity is released last, after host, session, credential, and workspace cleanup. A workflow-authored `executionPlanRef` that disagrees with the request's plan binding is a substitution conflict and fails closed. The hand-off to a worker is bounded by ScheduleToStart, because a start-to-close deadline does not run while an Activity waits and a starved lane could otherwise hold a lease for the whole budget.

**Ticket.** The ticket binds plan, run, step and request identity, capacity scopes, observed generation, and a monotonic admission epoch, also recorded on the lease row. Consumption fails closed before any side effect when no matching active lease exists or the generation or scope changed. The run then releases capacity, re-reads admission authority, and re-admits with a bumped epoch a bounded number of times; a plan whose realizer changed fails closed. If an interrupted attempt already launched a host and recorded completed provider work, only the remaining save, publication, or reporting runs, so work is not repeated.

**Limits and lanes.** Limits are explicit per-container Docker ceilings with no machine-derived budget; new policies require positive CPU limits, and bootstrap policies use fixed limits (2 CPUs, 4 GiB). Container jobs are admitted atomically under a backend-scoped lock. Plans selecting more than one Provider Profile are rejected before execution because the manager holds one lease per requester workflow. One worker process polls an execution queue (`TEMPORAL_ACTIVITY_AGENT_RUNTIME_TASK_QUEUE`) and a control queue (`TEMPORAL_ACTIVITY_AGENT_RUNTIME_CONTROL_TASK_QUEUE`) with separate budgets, so saturated execution cannot starve the cancellation and cleanup that free capacity; using one queue for both is rejected.

**Lease purposes.** Shared execution leases count against capacity; single-flight validation leases for credentialless profiles do not; exclusive maintenance leases block new consumers until existing ones drain. Mutable OAuth-home profiles stay capacity one because a probe and a run would share one auth home. Leases are acquired in Provider Profile id order and released in reverse, and a cooldown applies to every harness on that profile.

## 10. Credential materializers

A materializer turns a leased Provider Profile and acquired generation into runtime state a harness can consume. A plugin may describe its needs, but MoonMind decides which materializers are trusted and which secret roles, target paths, host modes, and cleanup they use. A materializer declares accepted implementations, auth models, host modes, secret roles, state mutability, target (for example a `0600` generated file), and cleanup, and returns a secret-free handle with its cleanup ref and attestation.

Classes: `oauth-home` (exclusive mutable CLI OAuth home with generation tracking), `omnigent-provider-config` (lease-owned Omnigent config backed by secret refs), `generated-auth-file`, `secret-env-file`, `session-scoped-config`, `host-owned-auth` (pre-authenticated connected host; nothing copied), and `none`.

Mutable credential stores need exclusive ownership unless a provider-specific design proves safe concurrent refresh, and refreshed state either persists to the authoritative store or is rejected before launch, because a disposable copy would silently lose it. The Codex OAuth volume, generation checks, startup scripts, readiness checks, lease, and release-last cleanup form `codex-oauth-home@1`, which the generic interface wraps without rewriting.

## 11. Host Classes and launch policies

A Host Class (`moonmind.omnigent-host-class.v1`) is an immutable declaration of an environment: image digest, build, architectures, harness implementations, integration modes, materializers, and features such as read-only root, restricted egress, and mounted Skills and tools. It is class-level evidence; the exact host still passes §5. The `omnigent-host-codex` service and the Codex on-demand container path form one registered realization, the Claude static service can be another, and services are not renamed to look generic. Several bounded Host Classes (core SDK, standard native, specialized vendor, approved community plugin, connected static) replace one large image. Installing harness software is an operator or image-building action; readiness is observed, never inferred from an install request.

A launch policy (`moonmind.omnigent-launch-policy.v2`) governs host behavior, not provider identity: host mode, required Host Class features, run-dedicated isolation, limits, egress policy, capture and retention, cleanup and janitor, and permitted controls. `codex-on-demand@1` and `codex-static@1` remain valid, read through normalized fields rather than their prefix. A policy is class-admissible only when it permits the integration mode, Host Class, every materializer, the workspace mutation, required repository and publication operations, required controls and continuations, and the network and capture posture; a mismatch blocks before lease acquisition.

## 12. Capability negotiation

**Pre-host.** The planner intersects workflow, profile, catalog, Host Class, materializer, bridge, launch-policy, and support-policy capabilities into an immutable `ClassAdmissionDecision` that proves class compatibility only. The v1 `requiredSatisfied` field is the exact-host capability set read by every deployed worker; a runtime-mode token such as `omnigent` is admitted by the trusted runtime-selection boundary and not added to it, which keeps the wire shape readable by older workers. The plan also lists `runtimeValidationRequirements` to prove on the exact host.

**Exact host.** After the host exists, a verifier intersects that decision with the host attestation, network and mount attestation, live model-option attestation, and bridge readiness into a fenced `ExactHostCapabilityDecision`. Missing, unknown, stale, or mismatched required evidence blocks runner and session creation.

**Model validation** keeps the host and lease while retrying a missing model or a transient catalog probe failure, at most three reads with unchanged inputs; authentication and invalid-request errors stay terminal. A successful CLI exit is not proof, because OpenCode can return its bundled catalog when refresh fails, so only an observation naming the exact selected model (or, for Claude Code aliases, a `served` answer from the upstream serving rule) permits launch. On exhaustion, bounded evidence is published before host cleanup.

**Required versus preferred.** Missing or unknown required capabilities block plan creation (class level) or realization (exact host). A missing preferred capability can yield an explicit degraded decision. Degradation never broadens authority, and no mismatch silently picks another harness, profile, Host Class, model, policy, or realizer.

## 13. Execution plan and runtime binding

The plan payload (`moonmind.omnigent-execution-plan-payload.v1`) contains only pre-host decisions:

```yaml
harnessCatalogRef: omnigent-harness-catalog:sha256:...
harnessId: opencode-native
harnessImplementationRef: omnigent-harness-implementation:sha256:...
agentSource: {kind: bundle, bundleDigest: sha256:..., ...}
credentialBindingSetRef: omnigent-credential-bindings:opencode-go-primary@3#sha256:...
credentialBindings: {primary-model: {providerProfileRef: opencode-go-default, materializerRef: opencode-auth-json@1}}
hostClassRef: omnigent-native-standard@3
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
model: {qualifiedId: opencode/..., modelConfigDigest: sha256:...}
resolvedSkills: {resolvedSkillSetRef: artifact:..., skillDeliveryRef: skill-delivery:sha256:...}
classAdmissionDecision: {requiredSatisfied: [interrupt, repository.read], preferredSatisfied: [streaming]}
runtimeValidationRequirements: [exact-harness-implementation, exact-network-egress, live-model-option]
supportCombinationKey: omnigent-support:sha256:...
```

It also names the endpoint, profile snapshot, workspace intent, and policy snapshot. Canonical form is UTF-8 JSON with sorted keys, no insignificant whitespace, and normalized enums and nulls. The payload excludes secrets, credential files, generations, lease refs, exact host ids or readiness, Docker volumes and socket access, bind sources, worker paths, caller-provided host ids, environment-derived authority, Skill bodies, and unbounded upstream metadata.

The runtime binding (`moonmind.omnigent-runtime-binding.v1`) records the plan and scope, per-slot leases and generations, host lease and id, attestation and capability decisions, workspace resolution, session id, and cleanup refs. Mutable state lives in the fenced control-plane aggregates; each transition yields a new ref and revision, and replaced generations cannot authorize writes. Profile-bound executions derive their scope from the request idempotency key, so redelivery reuses it while a new Step Execution gets its own binding. Stale Activities receive `fencing_conflict` and reconcile.

If the exact host does not match the plan beyond qualified drift (§5), realization fails before runner or session creation; the realizer cleans up only what it owns, records bounded evidence, and leaves the plan unchanged for diagnosis.

## 14. Control plane and lifecycle

The platform uses the canonical [control-plane aggregates](./ControlPlaneAggregates.md) (`OmnigentSession`, `OmnigentTurnAttempt`, `OmnigentObservation`, `OmnigentCommand`) rather than a second session authority. Provider Profile, host lease, supervisor, and cleanup generations fence stale owners, and a stale result triggers reconciliation instead of a repeated side effect.

A conforming realization resolves and persists the plan (§§4–12), acquires leases and persists the initial binding, materializes workspace and credentials, starts or attaches the host, validates and records exact-host evidence, creates or reattaches the session and persists its identity before the idempotent first message, streams events with controls routed through capability enforcement, harvests artifacts and checkpoints, cleans session, materializer, and host state, and releases leases last.

A retry reuses the plan, binding aggregate, Skills, session, command identities, workspace, and host authority, and never replans against a newer catalog or changes account, model, implementation, bundle, Host Class, policy, or realizer. An exact-host failure shows the chosen realization did not satisfy the plan; a different Host Class, model, binding set, or realizer needs a new plan with explicit lineage.

## 15. Continuation, branch, and remediation

Harness declarations guide continuation but MoonMind checkpoints remain authoritative. Warm reattach is valid only while the same session, binding, generations, implementation, and host state remain authoritative. Cold continuation uses checkpoint evidence, prior results, and Skill refs with a harness-supported strategy (rebuild native history, a bounded preamble, or a new session with a context package) or rejects when none is safe, because harnesses do not share resume semantics. Branches and remediation preserve every plan identity plus binding lineage and checkpoint authority; changing harness, model, Skills, binding set, Host Class, or realizer requires a new explicit plan, never an implicit fallback.

For PR output, an unchanged restored candidate reuses the accepted branch and existing PR only when the workspace is clean and the accepted and live remote heads still match; otherwise reuse is rejected before any mutation. Publication retries pre-connection transport failures on `fetch` and `ls-remote` up to four times, but not authentication, TLS, unknown errors, or remote mutations.

## 16. Native Workflow Chat

Native chat uses the binding-scoped [bridge](./OmnigentBridge.md). Effective controls are the intersection of upstream session capabilities, the exact-host decision, profile, plan, binding, workflow and Step state, and caller permission. The browser may hide unavailable controls, but the bridge enforces them, because an upstream control indicates technical availability, not authorization. Model, effort, terminal, file, approval, interrupt, stop, clear-context, workspace, subagent, and resource controls are gated separately.

## 17. Evidence and cleanup

Runs record safe references to every identity in §3 plus attestations, decisions, checkpoint lineage, and cleanup results. Logs and metrics use bounded reason codes; secrets, raw payloads, transcripts, Skill bodies, and unbounded diagnostics stay artifact-backed and redacted. Completion requires objective terminal evidence, not process exit, assistant prose, or a mutable path.

Cleanup is revision- and generation-fenced. On-demand cleanup removes only plan- and binding-owned resources; static-host cleanup drains plan-owned sessions without deleting unrelated host authentication. Provider leases release only after the harness stops using the generation, materializer and host cleanup are complete or durably delegated to the janitor, and terminal evidence records cleanup state. Cancellation or an ambiguous outcome keeps enough authority for retry or janitor reconciliation and never releases credentials while a consumer may be alive.

## 18. Failure taxonomy

Failures use typed low-cardinality codes such as `OMNIGENT_HARNESS_CATALOG_UNAVAILABLE`, `OMNIGENT_HARNESS_UNTRUSTED`, `OMNIGENT_HARNESS_BUILD_MISMATCH`, `OMNIGENT_VENDOR_RUNTIME_MISMATCH`, `OMNIGENT_AGENT_BUNDLE_IDENTITY_CONFLICT`, `OMNIGENT_SKILL_DELIVERY_MISMATCH`, `OMNIGENT_CAPABILITY_REQUIRED_UNKNOWN`, `OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH`, `OMNIGENT_CREDENTIAL_SLOT_UNBOUND`, `OMNIGENT_CREDENTIAL_GENERATION_FENCED`, `OMNIGENT_HOST_HARNESS_NEEDS_AUTH`, `OMNIGENT_MODEL_UNAVAILABLE`, `OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE`, `OMNIGENT_EXECUTION_PLAN_DIGEST_MISMATCH`, `OMNIGENT_RUNTIME_BINDING_CONFLICT`, and `OMNIGENT_CLEANUP_DEFERRED`. Diagnostics name the failed boundary and a remediation and do not treat vendor log text as authority.

## 19. Support and conformance

A `supportCombinationKey` digests server and host builds, harness implementation, vendor runtimes, agent source, materializers, Provider Profile compatibility class, Host Class, architecture, launch policy, model digest, realizer, and required-capabilities digest. Account secrets are excluded; the compatibility class and credential strategy are included.

| Classification | Meaning |
| --- | --- |
| Fully managed | Managed host and credentials, unattended launch, full lifecycle evidence |
| Connected host | Approved static host with host-owned auth that MoonMind can lease, attest, and drain |
| Experimental | Launchable and smoke-validated, but some support rows lack protected evidence |
| Discovered only | No approved combination; visible with setup guidance, not launchable |
| Quarantined | Unapproved package; no credentials or authority |

The generic conformance suite ([ConformanceAndLiveSmoke.md](./ConformanceAndLiveSmoke.md)) covers each contract in this document, from catalog trust through cleanup and raw-channel secret scans; harness-specific tests cover vendor login, per-model options, elicitation, fork history, streaming, interruption, and subagents. Evidence from one realizer qualifies another only across an explicitly proven shared observation boundary. Codex conformance and support matrices apply to whichever realizer Codex uses, and a generic refactor does not reduce OAuth exclusivity, exact host binding, workspace isolation, Skill and tool projection, restricted egress, capture, publication, checkpoints, cancellation, cleanup, replay compatibility, or rollback.

## 20. Product experience

Settings shows discovered harnesses with identity, trust and support classification, capabilities, setup steps, compatible Host Classes and Provider Profiles, model options, and lease and cooldown state, with advanced details behind progressive disclosure. Workflow Create asks for Agent Profile, provider account, model, and host policy; host ids, volumes, credential files, and generations are never authoring controls. Omnigent can be the default provider while the default profile remains the proven Codex profile, and new harnesses, models, Host Classes, or realizers do not change existing defaults or historical plans.

## 21. Codex continuity

In generic terms, Codex is the `codex-native` harness running the upstream `codex-native-ui` agent, with the Codex OAuth Provider Profile and `codex_auth_volume` presented through `codex-oauth-home@1`, the `omnigent-host-codex` service and on-demand Codex container as Host Class realizations, `codex-on-demand@1` and `codex-static@1` as launch policies, and `profile_bound_execution.py` as the `codex-profile-bound@1` realizer, which delegates to the existing coordinator and scripts while other harnesses use `generic-omnigent-host@1`. This coexistence is deterministic planning, not fallback. Existing Codex records and histories remain readable and replayable and are not rewritten. Because the realizer is part of the support key, Codex moves to the generic realizer only after proving the Codex support matrix for the same combination; reassignment and rollback affect only new plans. See [CodexSupportAndCutover.md](./CodexSupportAndCutover.md).

## 22. OpenCode Go example

The plan in §13 is OpenCode Go: Provider Profile `opencode-go-default` holds an `api_key` secret ref, the `opencode-auth-json@1` materializer writes a protected binding-owned auth file, the exact host attests the pinned OpenCode implementation and CLI build, live validation confirms the model before session creation, and materializer cleanup precedes lease release. No new top-level agent, workflow, or harness-named branch is involved.

## 23. Extension boundary

Where upstream metadata is insufficient, an approved companion descriptor keyed by implementation identity can declare credential slots, materializer classes, host features, required binaries and digest rules, mutable state paths, validation probes, and known limits, but never secrets, arbitrary mounts, Docker authority, or policy exceptions. A community plugin is launchable only when its package, version, entry point, and digest are approved, its catalog contribution is conflict-free, a Host Class pins it and the exact host attests it, every slot uses an approved materializer, its capabilities are enforceable, and its support classification permits the requested model and realizer. Unapproved plugins stay visible as quarantined.

## 24. Design rationale

- **One runtime per harness** duplicates selection, credentials, lifecycle, recovery, and evidence.
- **Replacing the Codex lane wholesale** would discard verified authority and recovery behavior.
- **Exact-host claims at planning time** are impossible for on-demand hosts that do not exist yet, and **pre-lease generations** break under rotation.
- **One universal image** ignores differing dependencies, cadence, size, auth, and architecture.
- **Workflow-time installation** breaks image authority, reproducibility, and egress policy.
- **Silent fallback** changes credentials, billing, model behavior, and evidence authority.

## 25. Module boundaries

Package map, dependency directions, ports, and exemptions are described in [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) and enforced by `tests/unit/omnigent/test_module_architecture.py`. Dependencies point from adapters toward coordination and pure contracts; pure modules do not import infrastructure, settings, or the environment. Generic coordination receives infrastructure through narrow ports and does not branch on harness ids, Provider Profile ids, or vendors, so approving a harness is registration data in `harness_platform/harness_registry.py`. Session and turn identities are owned by `control_plane/identities.py`. The Codex realizer and every other retained path that new admission can select are listed in `moonmind/omnigent/legacy_retirement.py` with owner and retirement class, which decides whether new work may still select them.

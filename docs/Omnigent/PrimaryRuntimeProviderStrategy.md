# Omnigent Primary Runtime Provider Strategy

**Status:** Canonical desired state (stages 1-5 implemented; stage 6 retirement outstanding)  
**Document Class:** System / Product Architecture  
**Owners:** MoonMind Platform  
**Last updated:** 2026-09-04  
**Authority:** Long-term runtime-provider direction for MoonMind

## Related documents

- [`docs/MoonMindArchitecture.md`](../MoonMindArchitecture.md)
- [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/OpenCodeHost.md`](./OpenCodeHost.md)
- [`docs/Omnigent/SharedHostImage.md`](./SharedHostImage.md)
- [`docs/Omnigent/RuntimeProviderRollout.md`](./RuntimeProviderRollout.md)
- [`docs/Omnigent/CodexSupportAndCutover.md`](./CodexSupportAndCutover.md)
- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md)
- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## Advance organizer

**One sentence:** Omnigent is to become MoonMind's primary runtime provider over time, with Codex, Claude Code, OpenCode, and future approved harnesses entering one generic Omnigent execution plane instead of accumulating separate MoonMind runtime architectures.

**One paragraph:** MoonMind will continue to own durable Temporal orchestration, Provider Profiles, OAuth enrollment, secret references, workspace authority, policies, Skills, publication, evidence, checkpointing, remediation, and cleanup. Omnigent will become the normal host, runner, harness, session, and live interaction substrate beneath those controls. Codex, Claude Code, and OpenCode should share one digest-pinned MoonMind Omnigent host image and one generic host lifecycle wherever technically possible. Genuine differences are isolated behind small trusted runtime-pack descriptors, credential materializers, and exact-host probes. Existing direct and legacy profile-bound paths remain available only as explicit migration, replay, rollback, and historical-read compatibility until evidence-backed retirement criteria pass.

## 1. Decision

MoonMind adopts the following long-term product and architecture decision:

> **Omnigent is the preferred and eventually primary runtime provider for MoonMind-managed coding agents.**

This is a directional commitment, not an immediate claim that every runtime has already completed cutover. A runtime becomes the default Omnigent-backed path only after its exact image, harness, credential, model, policy, lifecycle, and user journey have passing support evidence.

The destination is not a MoonMind runtime implementation for every provider CLI. The destination is one MoonMind-to-Omnigent platform boundary with registered harness integrations.

## 2. What “primary runtime provider” means

“Primary runtime provider” has a specific meaning in this architecture.

Omnigent becomes the normal provider of:

- host registration and runner connectivity
- harness discovery and execution
- provider-session creation and attachment
- live turns, events, tools, approvals, tasks, subagents, terminals, and resources where supported
- harness-native session behavior
- the native Workflow Chat application and protocol surface

MoonMind remains the authority for:

- Workflow, run, Step Execution, AgentRun, session, and turn ownership
- Temporal durability and reconciliation
- Agent Profile and Provider Profile selection
- OAuth enrollment, credential generation, capacity, cooldown, and revocation
- secret resolution and credential materialization
- repository and workspace authorization
- Skills, mounted tools, context, and retrieval policy
- host class, launch, resource, network, and egress policy
- model and effort selection
- checkpoint, resume, branch, and remediation decisions
- publication and approval authority
- artifacts, evidence, observability, audit, and historical reads
- cleanup, janitor ownership, fencing, and rollback

Omnigent becoming primary does not transfer MoonMind's security or workflow authority to Omnigent. It consolidates the runtime and harness substrate beneath MoonMind's authority.

## 3. Stable product identity

All Omnigent-backed harnesses use the same top-level execution identity:

```text
agentKind = external
agentId   = omnigent
```

The selected harness remains nested immutable authority:

```text
codex-native
claude-native
opencode-native
<future approved harness>
```

MoonMind does not introduce permanent top-level product identities such as `omnigent_codex`, `omnigent_claude`, or `omnigent_opencode`.

The dashboard may display friendly target names, but authoring and execution resolve one Omnigent Agent Profile, one harness implementation, one Provider Profile, one Host Class, one runtime pack, one materializer, one model configuration, one launch policy, and one execution realizer.

## 4. Current transition state

The repository currently contains three important generations of runtime behavior:

1. Direct managed Codex and Claude Code paths.
2. A proven Codex profile-bound Omnigent specialization with legacy OAuth-host lifecycle code.
3. A generic Omnigent harness platform and generic host realizer proven first through OpenCode.

The third generation is the destination. The first two remain explicit compatibility implementations while the generic path reaches equivalent or better support.

Which generation a product surface offers is no longer implied by code paths or scattered boolean flags. One versioned runtime-provider rollout policy governs each exact combination independently, and every authoring and follow-up surface reads that one decision through one shared selection and admission boundary. [`docs/Omnigent/RuntimeProviderRollout.md`](./RuntimeProviderRollout.md) is the authority for the rollout states, the exact compatibility dimensions, the canary and rollback controls, the operator-visible migration status view, and the migration telemetry contract.

Direct Codex, direct Claude Code, and the legacy profile-bound Codex realizer are presented as **labeled compatibility paths**, never as equal recommended defaults. A promoted generic row is the only non-compatibility default a surface preselects.

No current path is silently reclassified as generic. Existing execution plans and Temporal histories continue to invoke the realizer and compatibility version they recorded, and every admitted plan freezes the rollout decision generation that admitted it.

## 5. Governing principles

### 5.1 One generic control plane

Codex, Claude Code, OpenCode, and future approved harnesses should use the same canonical:

- immutable execution plan
- fenced runtime binding
- Provider Profile lease coordination
- host binding and host lease
- workspace preparation
- Skill and tool delivery
- restricted egress realization
- Omnigent session and turn ownership
- bridge, event, resource, and Workflow Chat contracts
- publication and checkpoint integration
- cancellation, cleanup, and janitor behavior

Adding a harness must not create a new top-level Temporal workflow or another lifecycle coordinator merely because its CLI differs.

### 5.2 One shared host image where practical

MoonMind should reuse one digest-pinned Omnigent runtime host image for Codex, Claude Code, and OpenCode when the image can contain their supported runtime binaries without weakening isolation or producing unmanageable release coupling.

The intended neutral image is conceptually:

```text
ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest>
```

The current `omnigent-host-opencode` image lineage is the starting point because it already derives from the stock Omnigent host and adds a pinned OpenCode runtime. During migration, the old image name may remain as an alias to the same manifest digest.

One shared image does not mean one shared credential home, one shared Host Class, or one support claim for every installed harness.

### 5.3 Separate Host Classes may share one image

Each supported harness combination retains an explicit Host Class even when several Host Classes point to the same image digest.

For example:

```text
omnigent-codex@1     -> shared-image@sha256:...
omnigent-claude@1    -> shared-image@sha256:...
omnigent-opencode@2  -> shared-image@sha256:...
```

Separate Host Classes preserve independent:

- harness declarations
- runtime dependency requirements
- materializer allowlists
- launch-policy compatibility
- qualification evidence
- rollout and rollback state
- support-combination identity

A newly published shared image can therefore be qualified and promoted for one harness without automatically promoting the other harnesses.

### 5.4 Runtime differences belong in a trusted runtime pack

The generic host lifecycle consumes a versioned trusted `HarnessRuntimePack` or equivalent descriptor.

A runtime pack contains only genuine harness-specific details such as:

- harness id
- provider runtime id
- binary name and supported version range
- credential mount and staging requirements
- forbidden ambient credential variables
- readiness and authentication probes
- model-catalog probe when required
- runner environment passthrough
- static and on-demand host compatibility

Runtime packs are deployment-owned registrations. Workflows and Agent Profiles cannot author arbitrary commands, mount paths, or environment variables through them.

The generic launcher, runtime script builder, attestor, and cleanup system must consume the selected runtime pack rather than accumulating `if harness == ...` branches.

### 5.5 Credential materialization remains runtime-specific and minimal

Credential formats and mutation behavior genuinely differ. Those differences remain isolated behind approved materializers.

The intended initial set is:

| Harness | Materializer | Credential ownership | Runtime behavior |
| --- | --- | --- | --- |
| OpenCode | `opencode-auth-json@1` | Run-owned | Read-only source is staged into a writable runtime home and destroyed after cleanup |
| Codex | `codex-oauth-home@1` | Provider Profile-owned | Writable OAuth home is mounted exclusively for the acquired generation |
| Claude Code | `claude-oauth-home@1` | Provider Profile-owned | Writable credential bundle supports every required Claude user-level path |

A credential handle declares whether its backing state is:

```text
run_owned
profile_owned
host_owned
```

Cleanup follows that ownership. Run-owned secrets are destroyed. Profile-owned OAuth homes are unmounted and released but are not deleted by ordinary run cleanup. Host-owned authentication is observed but not copied or claimed by MoonMind.

### 5.6 MoonMind owns OAuth enrollment

MoonMind Settings remains the user-facing OAuth enrollment authority for Codex and Claude Code.

The normal sequence is:

```text
Settings OAuth connection
  -> validated Provider Profile and credential generation
  -> execution-plan selection
  -> Provider Profile lease
  -> generic materializer binds the selected generation
  -> shared Omnigent host starts non-interactively
  -> exact-host auth probe succeeds
```

An Omnigent host must not start another interactive login ceremony. It consumes only the Provider Profile generation selected and leased by MoonMind.

### 5.7 Credential isolation is stricter than image isolation

A shared image may contain several CLIs. A running host receives credentials only for the one selected harness and Provider Profile.

The following must remain true:

- A Codex execution cannot read Claude or OpenCode credential state.
- A Claude execution cannot read Codex or OpenCode credential state.
- An OpenCode execution cannot read Codex or Claude credential state.
- Ambient API-key and configuration selectors are cleared or rejected according to the selected runtime pack.
- Image contents never grant permission to use an installed harness.
- Exact plan, Host Class, runtime pack, materializer, and attestation authority determine what the host may run.

### 5.8 Support is exact and evidence-gated

A shared image digest is not proof that every contained harness is supported.

Support remains specific to:

```text
MoonMind commit
+ Omnigent server and host build
+ shared host image digest and architecture
+ harness implementation
+ runtime-pack version
+ vendor CLI version and digest
+ Agent Profile version
+ Provider Profile compatibility class and credential generation class
+ credential materializer version
+ Host Class and launch policy
+ normalized model configuration
+ execution realizer version
+ required capabilities
```

Each claimed combination requires deterministic conformance and the protected live evidence required by policy.

### 5.9 No silent fallback

A plan records its execution realizer before side effects.

A generic Codex, Claude, or OpenCode execution that fails does not silently switch to:

- a direct runtime
- the legacy Codex profile-bound realizer
- another harness
- another Provider Profile
- another host mode
- another model
- a broader policy

Rollback changes future admission or creates an explicit new execution. It does not reinterpret the failed plan.

### 5.10 Replay and historical truth outlive cutover

Existing plans and Temporal histories retain their recorded runtime and realizer identities.

Legacy modules may remain as bounded replay-visible wrappers after new selection has moved to the generic realizer. They are removed only after the code-owned retirement checks prove:

- no new plans select them
- no active execution or cleanup authority uses them
- supported histories replay
- historical Workflow Detail and artifacts remain readable
- rollback no longer depends on them
- retention policy permits removal

## 6. Target topology

```text
Workflow authoring
  -> external/omnigent
  -> immutable Omnigent Agent Profile
  -> immutable execution plan
       harness implementation
       Provider Profile selection
       runtime pack
       credential materializer
       shared-image Host Class
       launch policy
       model configuration
       generic-omnigent-host@1
  -> fenced runtime binding
       acquired Provider Profile generation
       credential attachments
       host lease and exact shared image
       exact-host harness and runtime attestation
       workspace and Skill realization
  -> generic Omnigent host lifecycle
  -> Omnigent runner and selected harness
  -> canonical Omnigent session and turn control plane
  -> bridge, Workflow Chat, evidence, publication, checkpoint, and cleanup
```

Provider-specific logic stops at the registered runtime pack, credential materializer, and truthful capability adapter unless Omnigent exposes a genuinely different protocol.

## 7. Shared image contract

The shared image must:

- derive from an immutable compatible Omnigent host base
- contain the exact approved Codex, Claude Code, and OpenCode runtimes
- install runtimes at image-build time, never during a workflow launch
- run as the normal non-root Omnigent host user
- preserve the generic `/home/app` runtime contract
- publish an SBOM and provenance
- publish the portable Omnigent build identity label
- expose immutable multi-architecture manifest digests
- verify every required CLI and Omnigent harness during build and release tests
- avoid embedding provider credentials
- avoid making every installed CLI active by default

The image release record should identify each runtime independently. An OpenCode upgrade can produce a new image digest without asserting that the Codex or Claude support row has been requalified.

## 8. Runtime-pack contract

A representative trusted descriptor is:

```yaml
schemaVersion: moonmind.omnigent-harness-runtime-pack.v1
ref: codex-native-pack@1
harnessId: codex-native
providerRuntimeId: codex_cli
binary:
  command: codex
  supportedVersion: ">=<minimum>,<maximum>"
credentialMaterializers:
  - codex-oauth-home@1
forbiddenAmbientEnvironment:
  - OPENAI_API_KEY
probes:
  version: codex --version
  authentication: codex login status
hostModes:
  - static-connected
  - on-demand
```

The exact schema may evolve. The durable rules are:

- references are versioned
- descriptors contain no secrets
- descriptors are not workflow-authored
- commands are bounded and allowlisted by trusted code
- the execution plan records the selected runtime-pack ref
- exact-host evidence records the observed pack and runtime identity

## 9. Product selection and defaults

The long-term normal Workflow Create experience is:

1. Select or accept an Omnigent Agent Profile.
2. Select a compatible Provider Profile.
3. Select model, effort, workspace, policy, Skills, and publication intent.
4. Submit one `external/omnigent` execution.
5. Let the immutable plan select the approved Host Class, runtime pack, materializer, and generic realizer.

Direct Codex and direct Claude Code may remain visible during migration when policy permits them. They must be labeled as direct compatibility paths rather than equal long-term architecture choices.

### One shared selection boundary

`moonmind/workflows/executions/runtime_target_selection.py` resolves the runtime target for every surface that chooses one:

- new Workflow Create
- presets and preset expansion
- schedules and recurring occurrences
- edit and rerun
- retry as a fresh execution
- Checkpoint Branch create, continue, and fork
- remediation authoring
- linked continuation
- any API or MCP submission
- worker step normalization
- the dashboard's runtime-target catalog projection

No surface reconstructs a default from an environment variable or a hard-coded runtime map, and a source-kind difference changes policy and evidence rather than creating a second resolver.

### Default promotion is per combination

Default migration is staged independently per exact combination, not per product area. Each combination carries its own versioned rollout state, generation, canary allowlists, and rollback controls, so promoting Codex never promotes Claude Code or OpenCode. The set of governed surfaces still includes Workflow Create, presets, schedules, reruns and edits, Checkpoint Branches, remediation, and Workflow Chat and continuation — but a surface reads one decision instead of owning a stage of its own.

No default changes until the exact target combination has passing evidence and an operator-visible rollback path. A `preferred` or `new_work_default` state is demoted to explicit-only, with an exact reason, whenever required evidence is missing, stale, or expired, or the target is not launch-ready, model-qualified, architecture-supported, host-mode-available, or Provider-Profile-available.

### Preserved identity on continuation

An existing execution retains its recorded plan and realizer. A rerun may reuse the recorded target or explicitly upgrade to a currently qualified target. Schedules pin a target version or follow a separately versioned default-update policy, and changing a schedule's default advances the schedule revision. A historical selection that is no longer qualified stays visible and requires an explicit replacement before new submission.

See [`docs/Omnigent/RuntimeProviderRollout.md`](./RuntimeProviderRollout.md) for the exact dimensions, states, reason vocabulary, and configuration.

## 10. Migration stages

### Stage 1: Reuse the image without changing execution ownership

- Publish the OpenCode-derived image under a neutral shared name.
- Verify Codex, Claude Code, OpenCode, and Omnigent in the exact image.
- Point current host services and generic launches at the same digest where compatible.
- Preserve existing execution realizers and credential paths.

### Stage 2: Introduce runtime packs and shared Host Class configuration

- Register trusted runtime-pack descriptors.
- Generalize host bootstrap and image resolution around one shared image authority.
- Register separate Codex, Claude, and OpenCode Host Classes that reference the shared digest.
- Make startup, attestation, and model probing descriptor-driven.

### Stage 3: Generalize credential materialization

- Add credential ownership to materialization handles.
- Complete `codex-oauth-home@1` for the generic realizer.
- Complete `claude-oauth-home@1` for the generic realizer.
- Preserve OpenCode's run-owned API-key materialization.
- Prove rotation, fencing, cleanup, and cross-runtime isolation.

### Stage 4: Qualify and canary generic Codex and Claude

- Produce exact-artifact and protected-live support evidence.
- Allow selected new Codex and Claude Agent Profiles to choose `generic-omnigent-host@1`.
- Canary static and on-demand modes independently.
- Keep legacy realizers available for recorded work and explicit migration rollback.

### Stage 5: Make Omnigent the normal default

**Implemented.** The mechanism is in place and the promoted rows are deployment-owned:

- A versioned runtime-provider rollout policy controls each exact combination, and the decision plus its generation is frozen into the immutable execution plan.
- One shared selection and admission boundary serves Workflow Create, presets, schedules, edit, rerun, retry as a fresh execution, Checkpoint Branch, remediation, linked continuation, and API/MCP submissions.
- Direct and legacy paths are labeled compatibility choices and are never preselected while a generic row is promoted.
- Continuation, remediation, checkpoint, steering, approval, and Workflow Chat turns enter the canonical Omnigent session and turn-command path.
- Unsupported combinations stay unavailable with an exact reason rather than silently using a direct runtime.
- Exact canary allowlists, six independent rollback controls, an operator-visible migration status view, and eleven bounded migration metric families make the migration observable and reversible.

Promoting an individual harness row still requires that deployment's exact support evidence: the generic Codex, Claude Code, and OpenCode rows are promoted by their own qualification gates.

### Stage 6: Retire duplicate runtime architecture

- Stop admitting new legacy Codex profile-bound plans after parity and rollback criteria pass.
- Stop defaulting to direct Codex and direct Claude after their Omnigent combinations pass.
- Consolidate duplicate Compose startup scripts and environment variables.
- Reduce legacy modules to replay and historical-read adapters, then remove them only when retirement guards permit it.

## 11. Required acceptance gates

The strategy is complete only when all applicable gates pass:

- One shared image is built and verified by immutable digest for every supported architecture.
- Separate Host Classes can reference the same digest without conflating support state.
- Runtime packs drive startup, credential staging, environment, readiness, model discovery, and attestation.
- The generic host lifecycle contains no top-level provider-specific orchestration branches.
- Codex and Claude OAuth enrollment remains owned by MoonMind Settings.
- Generic Codex and Claude materializers preserve exclusive writable OAuth state and acquired generation fencing.
- OpenCode run-owned credentials remain isolated and are destroyed after cleanup.
- Non-selected runtime credentials are absent from every exact host.
- Codex, Claude, and OpenCode normal product journeys run through `generic-omnigent-host@1` for supported combinations.
- Continuations and other follow-up sources use one canonical session and turn-command boundary.
- Exact-artifact and protected-live reports identify image, harness, runtime pack, materializer, model, policy, and realizer.
- New authoring defaults prefer Omnigent only for qualified combinations, through one versioned per-combination rollout policy and one shared selection and admission boundary.
- Every admitted plan freezes the rollout decision and generation that admitted it, so a later policy change cannot reinterpret it.
- Operator status and bounded migration telemetry explain the current state of every combination without exposing credentials, provider-session ids, raw host paths, or image authority.
- Explicit generic selections never silently fall back.
- Existing histories remain replayable and historically truthful throughout migration.
- Duplicate runtime and host code is removed only after machine-checkable retirement criteria pass.

## 12. Non-goals

This strategy does not require:

- one Host Class for every harness in the shared image
- one credential format across runtimes
- sharing OAuth homes between Codex and Claude Code
- installing every future Omnigent harness in the shared image
- allowing workflows to select arbitrary images, runtime packs, probes, or mounts
- moving MoonMind workflow, policy, credential, workspace, evidence, or cleanup authority into Omnigent
- claiming support merely because a binary exists in an image
- removing direct or legacy runtime paths before replay and rollback obligations are satisfied
- silently converting active or historical executions to another realizer

## 13. Documentation rule

Documents that describe current runtime behavior must distinguish:

- **current supported path**
- **migration compatibility path**
- **desired primary path**
- **qualified support combination**

The phrase “Omnigent is the primary runtime provider” describes the durable destination. Current support claims remain exact and evidence-gated until each migration stage is complete.
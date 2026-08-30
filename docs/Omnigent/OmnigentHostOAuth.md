# Omnigent Host OAuth

**Document Class:** Canonical declarative  
**Status:** Current desired state  
**Owners:** MoonMind Platform  
**Last updated:** 2026-08-28  
**Authority:** Provider Profile, OAuth materialization, host binding, generation fencing, readiness, cleanup, and migration contract for OAuth-backed Omnigent harnesses

Implementation progress belongs in the roadmap, issues, and pull requests. This document defines the durable desired state and the security invariants that every OAuth-backed Omnigent launch must enforce.

## Related documents

- [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Omnigent/CodexSupportAndCutover.md`](./CodexSupportAndCutover.md)
- [`docs/Omnigent/OpenCodeHost.md`](./OpenCodeHost.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/ManagedAgents/OAuthTerminal.md`](../ManagedAgents/OAuthTerminal.md)
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Omnigent/CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)

## Advance organizer

**One sentence:** MoonMind Settings owns Codex and Claude Code OAuth enrollment, while the generic Omnigent host plane consumes only the selected leased credential generation through small runtime-specific materializers.

**One paragraph:** OAuth is one of the genuine runtime differences that remains after Codex and Claude Code converge on the generic Omnigent lifecycle. MoonMind connects, validates, rotates, repairs, and disconnects each Provider Profile. The immutable execution plan selects an approved OAuth materializer and runtime pack. After capacity is acquired, the generic realizer mounts the profile-owned writable credential state into the shared Omnigent host image, verifies the exact generation and runtime-specific authentication status, and starts no interactive login ceremony. Codex and Claude share planning, leases, hosts, sessions, turns, workspaces, evidence, recovery, and cleanup. They differ only in approved credential paths, runtime probes, and truthful harness capabilities.

## 1. Purpose

Omnigent is to become MoonMind's primary runtime provider over time. Codex and Claude Code OAuth support must therefore converge on the generic Omnigent execution plane rather than retain separate permanent host and lifecycle architectures.

MoonMind Settings enrolls first-party CLI OAuth credentials into durable provider-specific backing state and registers connected Provider Profiles. An Omnigent execution reuses that verified credential state without a second login ceremony and without extracting access or refresh tokens into workflow data.

The target journey is:

```text
Settings OAuth connection
  -> validated runtime-owned Provider Profile
  -> profile-owned credential state and generation
  -> immutable Omnigent Agent Profile and execution plan
  -> Provider Profile capacity lease
  -> generic OAuth materializer binds the acquired generation
  -> policy-selected Host Class using the shared Omnigent image
  -> runtime-pack-specific auth and version preflight
  -> generic Omnigent host registration
  -> canonical Omnigent session and turns
  -> evidence, checkpoint, publication, and cleanup
  -> credential consumer stops
  -> Provider Profile lease releases last
```

The current Codex profile-bound path remains a compatibility realizer until the generic Codex support combination has passing parity, rollback, and replay evidence. Direct Codex and direct Claude remain migration and historical-read compatibility according to their own retirement contracts.

## 2. Scope

This document governs:

- MoonMind Settings enrollment, validation, repair, reconnect, rotation, and disconnect for CLI OAuth profiles
- the one-consumer invariant for mutable OAuth state
- Provider Profile ownership, credential generation, capacity, cooldown, and readiness
- approved generic `codex-oauth-home@1` and `claude-oauth-home@1` materializers
- profile-owned credential attachments and cleanup behavior
- runtime-pack-specific authentication and version probes
- Host Class and launch-policy compatibility
- static-connected and on-demand host modes
- exact-host registration, harness, image, credential, model, and capability evidence
- generation drain, stale-host fencing, retry, cancellation, and janitor behavior
- bridge, Workflow Chat, checkpoint, remediation, publication, and historical-read integration
- replay-safe migration from legacy OAuth host implementations

It does not define a token broker, raw-token export, workflow-authored mounts, shared OAuth homes, interactive login inside an execution host, or a second Codex or Claude lifecycle coordinator.

## 3. Governing decisions

1. **MoonMind Settings is the OAuth enrollment authority.** Omnigent hosts do not ask users to authenticate again.
2. **Provider Profiles remain runtime-owned.** A Codex Provider Profile belongs to `codex_cli`. A Claude Provider Profile belongs to `claude_code`. Omnigent is the execution facade, not the credential owner.
3. **The execution plan selects a materializer.** Workflows never select credential files, volume names, mount paths, or login commands.
4. **OAuth state is mutable profile-owned state.** Codex or Claude may refresh or migrate it. The active authorized consumer requires writable access.
5. **OAuth capacity is one globally per Provider Profile.** Direct, legacy Omnigent, generic Omnigent, validation, repair, reconnect, rotation, and disconnect share the same capacity ledger.
6. **One acquired generation governs one consumer.** A stale host cannot continue after credential replacement or reconnect advances the generation.
7. **Host registration credentials are separate.** Provider OAuth never authenticates the host to the Omnigent server.
8. **Only bounded references cross durable boundaries.** Plans, runtime bindings, Temporal history, artifacts, checkpoints, and diagnostics contain ids, refs, generations, digests, and bounded status only.
9. **Static and on-demand modes share one contract.** Host mode changes realization, not credential or lifecycle semantics.
10. **One shared image does not share credentials.** A host gets only the selected runtime's credential bundle.
11. **Generic lifecycle, specific adapter.** Runtime-specific code is limited to credential materialization, bounded runtime probes, and capability normalization.
12. **Policy fails closed.** Missing capacity, wrong runtime, stale generation, invalid credential state, unsupported Host Class, failed auth probe, or incomplete cleanup never falls back to another runtime or credential.

## 4. Runtime-owned Provider Profiles

An Omnigent-backed execution does not use `runtime_id=omnigent` for provider credentials.

Representative Codex profile:

```yaml
profileId: codex_openai_oauth
runtimeId: codex_cli
providerId: openai
credentialSource: oauth_volume
runtimeMaterializationMode: oauth_home
credentialGeneration: 7
maxParallelRuns: 1
enabled: true
authState: connected
```

Representative Claude profile:

```yaml
profileId: claude_anthropic_oauth
runtimeId: claude_code
providerId: anthropic
credentialSource: oauth_volume
runtimeMaterializationMode: oauth_home
credentialGeneration: 4
maxParallelRuns: 1
enabled: true
authState: connected
```

The Omnigent Agent Profile declares compatible Provider Profile runtime, provider, authentication model, materializer, Host Class, and launch policy requirements. Authoring surfaces show only compatible profiles. The backend revalidates compatibility before plan persistence.

Provider Profile readiness remains authoritative. A host image, Host Class, or existing container cannot make a disconnected or incompatible profile launchable.

## 5. Why OAuth concurrency is fixed at one

CLI OAuth homes can contain access tokens, refresh tokens, account metadata, locks, caches, and format-version state that the CLI updates over time. Two consumers can race while refreshing, replacing, or migrating the same files even when the upstream provider accepts multiple access tokens.

The invariant is profile-wide:

```text
active direct consumers
+ active legacy Omnigent consumers
+ active generic Omnigent consumers
+ active credential-maintenance consumers
<= 1
```

The following boundaries enforce it:

| Boundary | Required behavior |
| --- | --- |
| OAuth Session start and finalize | Acquire credential-maintenance authority |
| Provider Profile API | Reject an OAuth profile configured above one consumer |
| Settings UI | Display fixed OAuth capacity rather than an editable default |
| Provider Profile Manager | Share one purpose-aware ledger across all consumer types |
| Execution admission | Acquire capacity before materializing credentials or mutating a host |
| Host binding | Permit at most one active credential-bearing host for the profile |
| Session launch | Permit at most one active provider session under that host and generation unless a later provider-specific proof expands the contract |
| Reconnect, rotation, and disconnect | Drain or terminate active consumers before replacing state |
| Generation reconciliation | Fence every host and retry using an older generation |
| Cleanup | Stop all credential consumers before releasing capacity |

Concurrency above one requires a separate provider-specific design proving validated mutable-state ownership. It is not an operator-tunable default.

## 6. Credential ownership model

Every generic credential materialization handle declares one ownership class:

```text
run_owned
profile_owned
host_owned
```

### Run-owned

The run creates credential state from a SecretRef and destroys it during cleanup. OpenCode API-key materialization is the reference example.

### Profile-owned

MoonMind Settings owns durable mutable credential state. A run mounts or attaches the selected generation, then unmounts it during cleanup. Ordinary run cleanup never deletes the profile's OAuth state.

Codex and Claude OAuth use this class.

### Host-owned

A connected host owns and manages its authentication outside MoonMind. MoonMind may attest readiness but does not copy or claim the credential body.

Ownership controls cleanup and must be explicit in the secret-free materialization handle. Inferring ownership from a mount path is forbidden.

## 7. Generic materializer contract

An OAuth materializer receives only trusted resolved inputs:

- Provider Profile id
- acquired Provider Profile lease
- acquired credential generation
- runtime id and provider id
- approved backing-state reference
- selected Host Class runtime uid, gid, and home
- selected runtime-pack ref
- launch policy and host mode
- execution scope and fencing authority

It returns a secret-free handle containing:

```yaml
materializerRef: codex-oauth-home@1
providerProfileRef: codex_openai_oauth
credentialGeneration: 7
ownership: profile_owned
attachments:
  - kind: volume
    sourceRef: <bounded-volume-ref>
    targetPath: /home/app/.codex
    accessMode: read-write
runtimeEnvironment: {}
cleanupRef: <bounded-cleanup-ref>
```

A materializer must:

1. Validate the Provider Profile runtime, provider, credential source, and generation.
2. Validate the selected runtime pack and Host Class allow the materializer.
3. Refuse stale or conflicting active ownership.
4. Return only approved attachment targets and access modes.
5. Persist durable cleanup authority before the first mutable host operation.
6. Never expose credential files or token bodies through its result.
7. Support idempotent retry under the same execution and fencing identity.
8. Refuse input drift under the same idempotency identity.
9. Unmount or detach profile-owned state without deleting it.
10. Preserve release-last Provider Profile ordering.

## 8. Codex OAuth materializer

The generic Codex materializer is:

```text
codex-oauth-home@1
```

Its canonical attachment is:

```yaml
targetPath: /home/app/.codex
accessMode: read-write
ownership: profile_owned
runtimeUid: 1000
runtimeGid: 1000
```

The selected runtime pack must define and attest:

```text
harnessId: codex-native
providerRuntimeId: codex_cli
binary: codex
version probe: codex --version
auth probe: codex login status
forbidden ambient credentials: unselected OpenAI API-key selectors and other runtime credential homes
```

The materializer does not copy the OAuth home into a run-owned volume. Authorized refreshes must persist to the Provider Profile-owned backing state.

A read-only seed or copy-on-start design is valid only under a separate contract that owns atomic writeback, refresh conflicts, generation advancement, and crash reconciliation. Silent loss of refreshed state is forbidden.

## 9. Claude Code OAuth materializer

The generic Claude materializer is:

```text
claude-oauth-home@1
```

Claude credential state may require more than one user-level path, including a directory and a user-level file. The materializer contract therefore supports a credential bundle with multiple approved attachments rather than assuming one directory per runtime.

A representative bundle is:

```yaml
materializerRef: claude-oauth-home@1
ownership: profile_owned
attachments:
  - targetPath: /home/app/.claude
    accessMode: read-write
  - targetPath: /home/app/.claude.json
    accessMode: read-write
```

The exact paths must be verified against the pinned Claude Code version in the selected shared image. The materializer cannot add an unregistered path at runtime.

The selected runtime pack must define and attest:

```text
harnessId: claude-native
providerRuntimeId: claude_code
binary: claude
version probe: claude --version
auth probe: claude auth status
forbidden ambient credentials: unselected Anthropic API-key selectors and other runtime credential homes
```

The same Provider Profile capacity, generation, fencing, cleanup, and release-last rules used for Codex apply to Claude.

## 10. Shared-image isolation

The shared MoonMind Omnigent host image may contain Codex, Claude Code, and OpenCode binaries. The selected host receives only one runtime's credentials.

A Codex host must prove:

- the Codex materializer and generation are present
- Claude and OpenCode credential attachments are absent
- conflicting API-key selectors are absent
- the Host Class declares `codex-native`
- the runtime pack is the selected Codex pack

A Claude host must prove the equivalent Claude-only credential state.

An OpenCode host must prove that neither OAuth home is mounted.

The image itself is never credential or harness authority.

## 11. Host Classes and runtime packs

Separate Host Classes may reference one shared image digest:

```text
omnigent-codex@1
omnigent-claude@1
omnigent-opencode@2
```

Each class declares only its approved harness implementations, runtime dependencies, materializers, architectures, integration modes, and features.

The immutable execution plan records:

- harness implementation ref
- Host Class ref
- shared image ref
- runtime-pack ref
- credential materializer ref
- Provider Profile selection
- launch policy
- model configuration
- execution realizer

A Host Class that points to the shared image does not authorize every runtime in that image.

## 12. Durable host binding and lease

The generic control plane uses the same host binding and host lease concepts for OAuth and API-key materializers.

A representative OAuth host binding is:

```yaml
bindingRef: omnigent-host-binding:codex_openai_oauth
providerProfileId: codex_openai_oauth
endpointRef: default
harnessId: codex-native
hostClassRef: omnigent-codex@1
runtimePackRef: codex-native-pack@1
credentialMaterializerRef: codex-oauth-home@1
credentialGeneration: 7
maxHosts: 1
maxSessionsPerHost: 1
```

A representative host lease records:

```yaml
hostLeaseRef: omnigent-host-lease:...
providerProfileId: codex_openai_oauth
providerLeaseRef: provider-profile-lease:...
bindingRef: omnigent-host-binding:codex_openai_oauth
credentialGeneration: 7
launchGeneration: 1
containerName: mm-host-...
omnigentHostId: host_...
status: assigned
```

The host lease is deterministic for the logical execution and fencing generation. Retries inspect and reconcile the existing authority before creating or replacing resources.

## 13. Launch ordering

Every OAuth-backed generic launch follows this order:

1. Verify the immutable execution plan and support combination.
2. Acquire the selected Provider Profile lease.
3. Record the acquired credential generation in the runtime binding.
4. Reserve host-binding and host-lease authority.
5. Persist cleanup authority for any planned mutation.
6. Materialize or attach the exact selected OAuth generation.
7. Prepare workspace, Skills, tools, GitHub credentials, and egress.
8. Launch the exact Host Class image.
9. Wait for the exact host registration.
10. Attest image, Omnigent build, harness implementation, runtime pack, runtime version, mounts, credential generation, auth status, model, Skills, tools, workspace, and egress.
11. Create or attach the canonical Omnigent session.
12. Submit the initial or follow-up turn through the canonical turn-command path.
13. Harvest terminal, event, resource, checkpoint, and publication evidence.
14. Stop or drain the provider session.
15. Stop and remove run-owned host state according to policy.
16. Detach credential attachments.
17. Preserve profile-owned OAuth backing state.
18. Complete cleanup evidence.
19. Release the Provider Profile lease last.

Failure at any point reconciles the same plan and runtime binding. It does not create a second unfenced owner.

## 14. Static-connected hosts

Static-connected mode remains valid for local and controlled deployments during migration.

A static host:

- uses the same shared image and runtime pack as on-demand mode
- receives only one selected OAuth profile generation
- registers one exact host identity
- remains subject to Provider Profile capacity and host leasing
- cannot act as an unrestricted multi-profile credential host
- cannot accept another session while the profile capacity contract forbids it
- must drain or stop its credential consumer before capacity releases

Static Compose service names may remain runtime-specific during migration. Their implementation should converge on one common host template and generic startup entrypoint.

## 15. On-demand hosts

On-demand mode launches a lease-owned container after capacity and durable authority exist.

The container uses:

- the selected digest-pinned shared image
- deterministic ownership labels and correlation identity
- non-root uid and gid
- read-only root filesystem
- bounded tmpfs and resource limits
- the policy-selected restricted-egress network
- one Provider Profile credential bundle
- one workspace
- one resolved Skill projection
- approved mounted tools
- a separate writable Omnigent state volume

Retries reuse or replace the same host only through current fencing authority. An old host, old activity, or janitor cannot stop or mutate the replacement generation.

## 16. Exact-host readiness

Before session or runner work begins, the exact host must prove:

- configured image ref matches the Host Class
- image digest and architecture are admitted
- `moonmind.omnigent.build_digest` matches the pinned Omnigent build
- Omnigent version matches the catalog authority
- selected harness implementation matches the plan
- selected runtime-pack ref is present and supported
- vendor CLI version matches the declared dependency
- credential attachments match the selected materializer
- credential generation matches the acquired generation
- owner, mode, and target paths are correct
- non-selected runtime credential state is absent
- runtime-specific auth probe succeeds without exposing tokens
- selected model is available when a reliable probe exists
- workspace, Skills, mounted tools, GitHub credentials, and egress match the plan
- the exact host registers under the expected owner and endpoint

A host name, harness name, image tag, or successful process start is not readiness evidence.

## 17. Generation rotation and drain

A successful reconnect or credential replacement advances `credentialGeneration`.

After generation advancement:

- new plans select only the new generation
- old hosts become stale
- active old-generation work follows explicit drain or termination policy
- retries cannot rematerialize the old generation as current
- cleanup for the old generation cannot delete new-generation resources
- validation and support evidence bound to the old generation becomes stale where generation is part of the support contract

Disconnect requires no remaining credential consumer. Forceful disconnect is a separate approved operation with explicit impact and cleanup evidence.

## 18. Cleanup and janitor behavior

Cleanup distinguishes resource ownership.

Run cleanup may remove:

- run-owned host container
- run-owned Omnigent state volume
- run-owned control material
- run-owned OpenCode credential state
- run-owned Skill projection
- run-owned temporary GitHub credential projection
- run-owned network or egress attachment where policy owns it

Run cleanup may not remove:

- Codex OAuth backing volume
- Claude OAuth backing state
- a replacement-generation host or volume
- another execution's workspace
- shared image layers
- deployment-owned policy or network resources

A janitor acts only from durable cleanup authority and current fencing generations. Provider Profile release remains last even when cleanup requires retries.

## 19. Credential-redacted evidence

Durable evidence may contain:

- Provider Profile id
- runtime and provider ids
- credential source and materializer refs
- credential generation
- attachment target paths when approved for operator evidence
- Host Class, image, runtime-pack, harness, model, policy, and realizer refs
- auth probe outcome and stable reason code
- lease, host, session, turn, cleanup, and janitor refs
- bounded timestamps and digests

Durable evidence may not contain:

- access or refresh tokens
- OAuth JSON bodies
- raw credential files
- secret-bearing environment values
- Docker inspection output that exposes secret material
- unbounded CLI authentication output
- host-local source paths that grant access authority

Auth probes return normalized status. Raw output is discarded or retained only through a separately reviewed protected evidence path with mandatory redaction.

## 20. Migration from legacy implementations

The transition occurs without changing the meaning of existing plans.

### Image reuse first

Codex and Claude static or legacy hosts may point to the same shared image digest before their execution realizer changes. This proves image reuse independently from lifecycle migration.

### Generic materialization second

Add and qualify `codex-oauth-home@1` and `claude-oauth-home@1` under the generic credential registry and runtime-pack contracts.

### Generic realizer third

New approved Agent Profiles may select:

```text
generic-omnigent-host@1
```

only after exact combination evidence passes.

### Product default migration fourth

Workflow Create, presets, schedules, reruns, branches, remediation, and continuation prefer the qualified Omnigent-backed target through explicit versioned rollout.

### Retirement last

Legacy `oauth_host_runtime.py`, profile-bound Codex realization, direct-runtime defaulting, duplicate Compose scripts, and deprecated environment aliases are removed only after:

- no new plan selects them
- active executions and cleanup drain
- Temporal replay passes
- historical reads remain available
- protected live generic parity passes
- rollback without the legacy path is exercised
- retention and retirement policy permits removal

Legacy code may remain as a replay-visible wrapper without remaining a selectable product path.

## 21. Failure behavior

Stable failures include at least:

```text
provider_profile_incompatible
provider_profile_not_ready
provider_profile_busy
credential_generation_stale
credential_materializer_unavailable
credential_attachment_invalid
credential_ownership_conflict
host_class_unavailable
runtime_pack_unavailable
host_image_mismatch
vendor_runtime_mismatch
harness_build_mismatch
authentication_not_ready
model_unavailable
host_registration_failed
egress_unavailable
cleanup_incomplete
```

A failure never silently selects another credential source, generation, runtime, harness, image, Host Class, model, launch policy, or realizer.

## 22. Acceptance criteria

- MoonMind Settings connects and validates Codex and Claude OAuth Provider Profiles without exposing token bodies.
- Provider Profile runtime ownership remains `codex_cli` or `claude_code` even when execution uses Omnigent.
- OAuth profile capacity is globally fixed at one across direct, legacy, generic, and maintenance consumers.
- Generic credential handles declare run-owned, profile-owned, or host-owned state.
- `codex-oauth-home@1` binds the acquired writable Codex OAuth generation through the generic realizer.
- `claude-oauth-home@1` binds every required acquired writable Claude credential path through the generic realizer.
- The shared image receives credentials only for the selected runtime.
- Exact-host probes validate runtime version and authentication without printing credential bodies.
- Static and on-demand modes consume the same plan, materializer, runtime-pack, generation, evidence, and cleanup rules.
- Rotation fences old hosts and retries.
- Run cleanup preserves profile-owned OAuth backing state.
- Provider Profile release occurs after every credential consumer stops and cleanup authority is recorded.
- Generic Codex and Claude support is claimed only for exact combinations with passing conformance evidence.
- Existing plans and histories retain truthful legacy realizer identity.
- Explicit generic plans never silently fall back.
- Duplicate OAuth host architecture is retired only after machine-checkable replay, rollback, drain, and historical-read criteria pass.

## 23. Non-goals

This contract does not permit:

- sharing a Codex OAuth home with Claude Code
- sharing one profile-owned OAuth home across concurrent hosts
- exporting tokens into environment variables for ordinary execution
- copying OAuth homes into artifacts, checkpoints, or workspaces
- starting interactive OAuth inside a workflow host
- using `runtime_id=omnigent` as credential ownership
- selecting arbitrary credential mount paths from workflow input
- one multi-profile static host with all provider credentials
- deleting OAuth backing state during run cleanup
- treating a shared image as shared credential or support authority
- creating separate permanent Codex and Claude lifecycle coordinators
- silent fallback to a direct or legacy runtime

## 24. Strategic rule

The long-term architecture is one generic Omnigent host and session plane with minimal runtime-specific adapters.

For OAuth-backed runtimes, the acceptable specialization is limited to:

- Provider Profile compatibility
- approved credential bundle shape
- runtime-pack registration
- version and authentication probes
- truthful harness capability normalization
- combination-specific support evidence

All other behavior should converge on the common execution plan, runtime binding, leases, host realization, canonical session and turn control plane, bridge, evidence, recovery, publication, and cleanup.
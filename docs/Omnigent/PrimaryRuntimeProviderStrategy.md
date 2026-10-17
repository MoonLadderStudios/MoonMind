# Omnigent Agent Backend Strategy

**Status:** Current strategy  
**Document Class:** System / Product Architecture  
**Owners:** MoonMind Platform  
**Last updated:** 2026-10-16  
**Authority:** Agent-backend direction under reliability-first simplification

## Related documents

[AGENTS.md](../../AGENTS.md), [Harness, Provider Profile, and Backend Selection](../UI/HarnessProfileBackendSelection.md), [Single-User Application Design](../SingleUserApplicationDesign.md), [Omnigent module entrypoint](README.md), [Contract Ownership](ContractOwnership.md), [Harness Platform](OmnigentHarnessPlatformDesign.md), [Shared Host Image](SharedHostImage.md), [selection and rollout controls](RuntimeProviderRollout.md), [Provider Profiles](../Security/ProviderProfiles.md), [Workflow Chat](../UI/WorkflowChatPanel.md), [repository access and durable work](../RepositoryAccessAndWorkspaceDesign.md), and [deployment updates](../Steps/DockerComposeUpdateSystem.md).

## Advance organizer

Omnigent is MoonMind's single agent backend: one lifecycle behind the Codex, Claude Code, OpenCode, and other approved harnesses. MoonMind keeps durable orchestration and its security, workspace, credential, result, and publication responsibilities. Reliability comes from fewer competing owners, useful defaults, bounded recovery, and preserved work, not a larger rollout or compatibility system.

Retirement of remaining alternatives is class-based new-admission control with owner-specific removal checks (`OmnigentModuleArchitecture.md` §5). Ordinary admission is certificate independent; explicit strict certification and the selection/rollout controls still in code are owned by [Conformance and Live Smoke](ConformanceAndLiveSmoke.md#deployed-release-support-evidence) and [selection and rollout controls](RuntimeProviderRollout.md).

## 1. Decision

**Omnigent is the single supported agent backend.** Harness-specific behavior belongs in thin trusted adapters. Retiring a direct or profile-bound path must preserve supported behavior and active work, but narrow reliability fixes do not wait for every harness, static mode, connector, or retirement to finish.

### 1.1 Seven required product outcomes

| Outcome | Required behavior |
| --- | --- |
| Shared execution host | The Omnigent host runs admitted agent work, without absorbing the API, Temporal, artifact service, or privileged deployment controller. |
| On-demand Codex, Claude Code, and OpenCode | Supported harnesses use the shared attempt-owned lifecycle. Ordinary use does not need one permanent vendor host. Explicit static operation is optional and supported only where its own behavior is established. |
| Workflow Detail interactions | Native chat exposes the bound session's supported turns, events, tools, approvals, terminals, and resources through existing authorization. Loading HTML is not readiness. Failures and durable results remain accessible without a second composer or host-management dashboard. |
| Retry, checkpoint, and artifact parity | Existing recovery and preservation owners handle interruption, continuation, restore, and saved output. Where the original host is unavailable, recovery uses actual durable evidence rather than restarting completed work. |
| Shared implementation | One owner handles each launch, session/turn mutation, capture, publication, and cleanup responsibility. Persistent records support those owners instead of becoming parallel orchestrators. |
| Reusable OAuth Provider Profiles | Existing Codex/Claude Profiles retain their identities, enrollment-owned homes, credential generation, and capacity ownership. No duplicate account, copied home, or second login is required. |
| Harness and one Provider Profile | Ordinary authoring uses Harness and Provider Profile; Backend is not an ordinary selector (§9). |

These outcomes are the acceptance contract for each claimed harness, not a fixed test inventory. Do not relabel unfinished required capability as unsupported to close the work.

## 2. What the agent backend owns

Omnigent provides the host/runner protocol, harness execution, live provider sessions, and native interactions. MoonMind's existing Temporal and module owners retain admission, workflow/step identity, selected Profile/model policy, credentials/capacity, workspace and context authority, Skills/tools, durable evidence, recovery, publication, and cleanup.

A durable event store and the lifecycle consuming it are not competing systems. Remove duplicate independent mutations, not persistence that keeps transcripts, commands, artifacts, and historical diagnostics useful after a host disappears. The bridge is not replaced by another session engine.

One operator per instance can use multiple accounts and concurrent workflows. Independent deployments do not share a database, rollout decision, or drain observation by assumption.

## 3. Stable product identity

Omnigent-backed execution keeps `agentKind=external` and `agentId=omnigent`. The selected harness has its own nested identity, such as `codex-native`, `claude-native`, or `opencode-native`.

Use **Harness** for Codex, Claude Code, and OpenCode; **Provider Profile** for the configured connection and defaults; and **Backend** for Omnigent. Use **agent backend** in architecture prose when distinguishing it from the MoonMind API. Reserve **Container** and **Host** for actual execution resources. [The selection design](../UI/HarnessProfileBackendSelection.md) owns product presentation and the mapping from serialized names.

Product family, harness, Provider Profile `runtime_id`, and vendor command/display names remain different domains. Profile runtime values keep their harness-ownership meaning, not Backend identity. Reuse the pure harness registry for registration-level consumers and the resolver for executable choices. Registry membership is not permission, and temporary lack of capacity is not loss of registration.

New producers use one canonical runtime-input boundary. Known legacy values can be converted by an identified migration/ingress owner without changing their meaning. Unknown or conflicting explicit input never becomes another runtime. Keep original historical decoding where required, not duplicate alias maps throughout the core.

## 4. Remaining alternatives

Code still carries the direct `codex_cli` and `claude_code` compatibility Backends, the retained profile-bound Codex realizer (the Codex default until generic Codex is promoted), runtime-provider rollout rules, and the code-owned retirement inventory in `moonmind/omnigent/legacy_retirement.py`. Simplify them through their consumers. Do not set every gate true, install another policy facade, or strip an active resource owner without a safe transition. [RuntimeProviderRollout.md](RuntimeProviderRollout.md) describes the controls as they exist.

## 5. Governing principles

### 5.1 One generic control plane

Reuse existing plans, runtime bindings, Profile capacity, workspace, session/turn, evidence, and cleanup owners. Do not add a supervisor around a generic realizer that already owns the lifecycle. A new harness must not need another Workflow, session database, finalizer, or rollback service.

### 5.2 One shared host image where practical

Use the deployment's installed managed host image, shared across harnesses where practical. Images are built from trusted inputs, contain no provider secrets, and have recorded immutable provenance. Sharing binaries does not share credentials, OAuth homes, sessions, or launch permission.

One deployment owner resolves and updates the needed server/host artifacts. A changed SHA, digest, or patch version is not itself incompatibility. Do not introduce an all-fields compatibility fingerprint. Integrity of a selected artifact and compatibility between components are separate checks.

### 5.3 Separate Host Classes may share one image

Retain Host Class distinctions only for genuine launch, isolation, or capability needs. A separate class for every harness/image/model combination is not required. Reuse existing descriptors rather than adding another support catalog.

### 5.4 Runtime differences belong in a trusted runtime pack

Trusted descriptors/adapters contain genuine CLI, credential-path, environment, and protocol differences. Workflows cannot supply arbitrary commands, mounts, or environment allowlists through them. Do not fetch live model catalogs during schema import or mirror registry data into a second authority.

### 5.5 Credential materialization remains runtime-specific and minimal

The materializer owns the selected credential format and lifecycle. Run-owned material is cleaned after its consumers stop. Profile-owned OAuth homes are released, not deleted by ordinary run cleanup. Host-owned credentials are not copied or silently claimed. Credentialless execution creates no dummy secret and does not inherit keyed credentials or billing policy. Materialization, renewal, and cleanup use existing operation/ownership records, not another generation service or universal token-file mechanism.

### 5.6 MoonMind owns OAuth enrollment

Settings/Profile enrollment supplies the admitted account. Omnigent execution reuses it without a second login or new Profile type. Codex and Claude Profiles retain their underlying `codex_cli` and `claude_code` identities. Concurrent direct, static, and on-demand consumers cannot write the same mutable OAuth home without its exclusive authority.

### 5.7 Credential isolation is stricter than image isolation

A host receives only its admitted model/repository credentials. Scrub conflicting ambient selectors through trusted delivery. Managed-publisher destination credentials stay outside the agent. Read-only token files or a helper returning a broad token are not confinement against arbitrary code. Preserve explicit exposure/high-security policy without building a new proxy merely to claim support.

### 5.8 Support is exact and evidence-gated

Evidence identifies the actual code, image, harness, credential boundary, model, and operations exercised. Evidence for one adapter does not prove a different credential or session protocol, but common production mechanisms can share representative tests instead of repeating the full cross-product.

Use current-candidate CI and focused real-boundary tests. Keep live-provider qualification separately authorized and honestly limited. Neither an installed binary nor a success flag qualifies a product journey. Absent live access for an unrelated deleted alias is not a reason to block safe repository cleanup.

### 5.9 No silent fallback

Preserve admitted harness, account, model/cost/privacy, source, host-mode constraints, and publication intent. Failure cannot silently select a direct path, another realizer, or broader authority. Recover using the bounded owner, distinguishing capacity waits, unavailable observations, and actual incompatibility.

A compatible installation update for future work is not an unrequested account/harness change. Already-started attempts retain their actual provenance. A changed execution objective or authority uses fresh admission, not a rewritten old plan.

### 5.10 Replay and historical truth outlive path retirement

Recorded inputs, digests, results, and histories keep their meaning. Preserve executable compatibility only for actual active, pending, replay/reset, or cleanup consumers. Read-only historical access need not keep an old launcher or supervisor running. A maintenance window can stop incompatible writers but cannot make incompatible recorded histories safe; use replay evidence or a controlled transition.

### 5.11 Recovery and preservation parity

Reconcile the recorded attempt, session/turn effects, phase receipts, and saved results before retrying. Live-session continuation and restoration into a fresh execution are different operations. Restoring files does not revive credentials, leases, approvals, or permission to repeat remote effects.

Persist confirmed compute and required saved-content evidence before later publication/reporting or destructive cleanup. A failed publication does not erase a valid save, and successful saving does not upgrade failed/canceled compute. Verified artifact storage can supply save-only durability without a GitHub identity; a remote recovery push still needs admitted destination authority. A local path or incomplete upload is not a durable saved result.

The finalization and janitor owners share a durable preservation decision across restart. Retain failed saves only under bounded quota/recovery policy and report incompleteness. Release model capacity and credentials after their consumers are confirmed stopped, independently of non-sensitive content retention. No second finalizer or permanent live host is required merely to retain files.

## 6. Target topology

Ordinary authoring resolves one meaningful selection into an existing plan, workspace, and attempt-owned Omnigent host. The canonical session/turn boundary serves interactions and records evidence. Existing capture, publisher, recovery, and cleanup owners finish the operation.

Reduce idle services by removing unused/default-on work. Combine worker responsibilities only when trust, resource, and restart requirements permit it. Queue names alone do not establish process isolation or aggregate concurrency. Preserve responsive cancellation and cleanup without another worker supervisor.

## 7. Shared image contract

[SharedHostImage.md](SharedHostImage.md) owns build and artifact details: trusted build-time tools, immutable selected artifacts, architecture support, and SBOM/provenance.

## 8. Runtime-pack contract

The pack/materializer owners define executable interfaces ([SharedHostImage.md](SharedHostImage.md) §2). Keep descriptors small, trusted, secret-free, and versioned where their wire behavior requires it. Tool-version inspection must not require provider authentication. Runtime readiness still checks the real capabilities and credentials the requested operation needs.

## 9. Product selection and defaults

Ordinary authoring uses **Harness and one Provider Profile**, with the same server-side agreement checks. The selected/default profile establishes a compatible initial harness. Changing Harness filters profiles without silently substituting an explicitly selected account. Advisory discovery/capacity failures preserve valid inventory and drafts rather than selecting another account.

**Backend** is Omnigent and is not shown on the ordinary form. The Advanced Backend selector appears only while a supported direct compatibility Backend serves the selected Harness, and a non-Omnigent choice stays summarized when Advanced is collapsed. When no alternative remains, remove the selector and keep recorded Backend in execution details. Do not keep obsolete paths merely to populate a dropdown. Internal Agent Profile, Host Class, materializer, and realizer identities are not another selection chain.

The workflows list leads with recorded Provider Profile, with optional secondary Harness text, not today's default. [The selection design](../UI/HarnessProfileBackendSelection.md) defines pending, unknown, multiple-profile, history, and filter semantics.

### One shared selection boundary

`resolve_runtime_target_selection` in `moonmind/workflows/executions/runtime_target_selection.py` is the integration point for Create, presets, schedules, edits/reruns, fresh retries, branches, remediation, continuation, API/MCP, and worker normalization. Admission composes it with Profile/configuration resolution. Omitted and default-equivalent requests resolve to the same Omnigent policy without an accidental direct fallback. Preserve authored versus defaulted intent. Simplify its providers and remove redundant defaults with their callers rather than adding a parallel selector or frontend policy.

### Installed runtime selection

Default selection follows the installed managed runtime, checked against actual capability and authority. Rollout rows, qualification switches, and canary cohorts that remain in code are transition controls, not additional authorities to grow. Unknown or incompatible explicit selections remain actionable.

### Preserved identity on continuation

Already-started work retains its recorded meaning and evidence. New launches, including recurring occurrences, follow installed runtime selection while preserving authored harness/Profile, model/cost/privacy, source, and publication intent. Schedules do not pin image digests or runtime-provider rollout versions, and a patch update must not require recreating or reapproving schedules.

## 10. Retiring an alternative path

Use one bounded transition through existing deployment ownership: establish the replacement behavior, stop incompatible new admissions, reconcile actual old work and retained-history requirements, switch the selection, and remove the obsolete path with its consumers. This is an operational dependency, not another persisted state machine.

Use current records and scoped observation for the affected deployment; missing visibility is unknown, not clean drainage. An unused config alias needs caller/serialization evidence, not a live provider canary. An active OAuth consumer or history-visible command needs its actual safety evidence. Fresh installs without old consumers need no retirement census. Preserve operator URLs, settings, data, credentials, and the only recoverable work.

## 11. Required acceptance gates

The shared normal path must demonstrate launch, usable session/chat, correctly scoped credentials, saved results, and relevant retry/cancel/recovery behavior through actual consumers. Use common-path CI plus tests for genuinely different credential, storage, or protocol boundaries, and validate replay changes with appropriate histories. Report source inspection, fixtures, current CI, live observations, and deployed state separately. Missing required evidence cannot become success, while unrelated qualification is not a blanket gate on simplification.

A change must remove competing mechanisms, not hide them or rename a registry.

## 12. Non-goals

No new rollout service, retirement inventory, compatibility fingerprint, session orchestrator, shared raw-credential home, account/tenant model, or mandatory static vendor host. No arbitrary workflow-authored image or privileged mount. No weakening of real security, active-work, data-integrity, or historical interpretation requirements.

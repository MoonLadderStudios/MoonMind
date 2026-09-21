# Omnigent Primary Runtime Provider Strategy

**Status:** Canonical desired state; implementation and deployment evidence remain separate  
**Document Class:** System / Product Architecture  
**Owners:** MoonMind Platform  
**Last updated:** 2026-09-21  
**Authority:** Long-term runtime-provider direction under reliability-first simplification

## Related documents

[AGENTS.md](../../AGENTS.md), [Single-User Application Design](../SingleUserApplicationDesign.md), [Omnigent module entrypoint](README.md), [Contract Ownership](ContractOwnership.md), [Harness Platform](OmnigentHarnessPlatformDesign.md), [Shared Host Image](SharedHostImage.md), [runtime selection and transition](RuntimeProviderRollout.md), [Provider Profiles](../Security/ProviderProfiles.md), [Workflow Chat](../UI/WorkflowChatPanel.md), [repository access and durable work](../RepositoryAccessAndWorkspaceDesign.md), and [deployment updates](../Steps/DockerComposeUpdateSystem.md).

## Advance organizer

Omnigent is the destination for one agent-runtime lifecycle behind Codex, Claude Code, OpenCode, and other approved harnesses. MoonMind keeps durable orchestration and its security, workspace, credential, result, and publication responsibilities. Reliability comes from fewer competing owners, useful defaults, bounded recovery, and preserved work, not a larger rollout or compatibility system.

This strategy revises the earlier exact-combination promotion and staged-retirement requirements. It does not claim that their current code has been removed. The [earlier strategy](https://github.com/MoonLadderStudios/MoonMind/blob/6fdaab848e8f9fd9c5279ea36186482cab05733d/docs/Omnigent/PrimaryRuntimeProviderStrategy.md) remains available for concrete historical interpretation. Old machinery is not a permanent requirement merely because it exists. Until the owning migrations land, `README.md` exact-qualification rules, `ContractOwnership.md` per-combination and staged-retirement assignments, and `SharedHostImage.md` §§5–6 remain the authoritative implemented contract for current behavior; this document states the desired target and must not be read as having already removed those controls.

## 1. Decision

**Omnigent becomes the single normal agent runtime provider.** Harness-specific behavior belongs in thin trusted adapters. Replacing direct/profile-bound paths must preserve supported behavior and actual active work, but narrow reliability fixes do not wait for every harness, static mode, connector, or migration to finish.

### 1.1 Seven required product outcomes

| Outcome | Required behavior |
| --- | --- |
| Primary runtime container | The Omnigent host runs admitted agent work, without absorbing the API, Temporal, artifact service, or privileged deployment controller. |
| On-demand Codex, Claude Code, and OpenCode | Supported harnesses use the shared attempt-owned lifecycle. Ordinary use does not need one permanent vendor host. Explicit static operation is optional and supported only where its own behavior is established. |
| Workflow Detail interactions | Native chat exposes the bound session's supported turns, events, tools, approvals, terminals, and resources through existing authorization. Loading HTML is not readiness. Failures and durable results remain accessible without a second composer or host-management dashboard. |
| Retry, checkpoint, and artifact parity | Existing recovery and preservation owners handle interruption, continuation, restore, and saved output. Where the original host is unavailable, recovery uses actual durable evidence rather than restarting completed work. |
| Shared implementation | One owner handles each launch, session/turn mutation, capture, publication, and cleanup responsibility. Persistent records support those owners instead of becoming parallel orchestrators. |
| Reusable OAuth Provider Profiles | Existing Codex/Claude Profiles retain their identities, enrollment-owned homes, credential generation, and capacity ownership. No duplicate account, copied home, or second login is required. |
| One ordinary Profile choice | The existing Runtime and one Profile authoring boundary remains. Internal harness/configuration/Host Class/materializer/realizer identities do not become additional required selectors. |

These are required outcomes, not a claim of current support or a fixed test inventory. Prove the relevant production boundaries and reuse shared evidence. Do not silently relabel unfinished required capability as unsupported to close the program.

## 2. What “primary runtime provider” means

Omnigent provides the host/runner protocol, harness execution, live provider sessions, and native interactions. MoonMind's existing Temporal and module owners retain admission, workflow/step identity, selected Profile/model policy, credentials/capacity, workspace and context authority, Skills/tools, durable evidence, recovery, publication, and cleanup.

A durable event store and the lifecycle consuming it are not necessarily competing systems. Remove duplicate independent mutations, not persistence that keeps transcripts, commands, artifacts, and historical diagnostics useful after a host disappears. The bridge is not replaced by another session engine.

One operator per instance can use multiple accounts and concurrent workflows. Independent deployments do not share a database, rollout decision, or drain observation by assumption. No tenant or human-role system is required.

## 3. Stable product identity

Omnigent-backed execution keeps `agentKind=external` and `agentId=omnigent`. The selected harness has its own nested identity, such as `codex-native`, `claude-native`, or `opencode-native`.

Product family, harness, Provider Profile runtime, and vendor command/display names remain different domains. Reuse the existing pure harness registry for registration-level consumers and the existing resolver for executable choices. Registry membership is not permission, while temporary lack of capacity is not loss of registration.

New producers use one canonical runtime-input boundary. Known legacy values can be converted by an identified migration/ingress owner without changing their meaning. Unknown or conflicting explicit input never becomes another runtime. Keep original historical decoding where required, not duplicate alias maps throughout the core or a global ban on vendor names.

## 4. Current transition state

At the September 21 review baseline `6fdaab848e8f9fd9c5279ea36186482cab05733d`, the repository contains direct compatibility paths, Codex profile-bound supervision, generic realizer routing, runtime-provider rollout rules, and a code-owned retirement inventory. The static-Claude module already declares a retained optional path. These are source observations, not deployed support claims.

The desired steady state does not preserve the old Codex phase machine, per-combination canaries, six rollback switches, nine ordered removal stages, or a permanent inventory of deleted components. Simplify them through their existing consumers. Do not just set every gate true, install another policy facade, or strip an active resource owner without a safe transition.

The existing shared selection boundary remains the integration point. Actual implementation and migration work is tracked by #3931, #3932, #3935, and #3835. [RuntimeProviderRollout.md](RuntimeProviderRollout.md) distinguishes the target from currently readable legacy fields and controls.

## 5. Governing principles

### 5.1 One generic control plane

Reuse existing plans, runtime bindings, Profile capacity, workspace, session/turn, evidence, and cleanup owners. Do not add a supervisor around a generic realizer that already owns the lifecycle. A new harness must not need another Workflow, session database, finalizer, or rollback service.

### 5.2 One shared host image where practical

Use the deployment's installed managed host image, shared across harnesses where practical. Images are built from trusted inputs, contain no provider secrets, and have recorded immutable provenance. Sharing binaries does not share credentials, OAuth homes, sessions, or launch permission.

One deployment owner resolves and updates the needed server/host artifacts. A changed SHA, digest, or patch version is not itself incompatibility. Do not introduce a replacement all-fields compatibility fingerprint. Integrity of a selected artifact and compatibility between components are separate checks.

### 5.3 Separate Host Classes may share one image

Retain Host Class distinctions only for genuine launch, isolation, or capability needs. A separate class for every harness/image/model combination is not a strategic requirement. Reuse existing descriptors rather than adding another support catalog. Preserve explicitly selected policy and supported host behavior.

### 5.4 Runtime differences belong in a trusted runtime pack

Trusted descriptors/adapters contain genuine CLI, credential-path, environment, and protocol differences. Workflows cannot supply arbitrary commands, mounts, or environment allowlists through them. Reuse the existing registry and generation path. Do not fetch live model catalogs during schema import or mirror registry data into a second authority.

### 5.5 Credential materialization remains runtime-specific and minimal

The existing materializer owns the selected credential format and lifecycle. Run-owned material is cleaned after its consumers stop. Profile-owned OAuth homes are released, not deleted by ordinary run cleanup. Host-owned credentials are not copied or silently claimed. Credentialless execution creates no dummy secret and does not inherit keyed credentials or billing policy.

Materialization, renewal, and cleanup use existing operation/ownership records. Do not create another generation service or universal token-file mechanism. Preserve the actual process boundary, not only a label claiming isolation.

### 5.6 MoonMind owns OAuth enrollment

Existing Settings/Profile enrollment supplies the admitted account. Corresponding Omnigent execution reuses it without a second login or new Profile type. Codex and Claude Profiles retain their underlying `codex_cli` and `claude_code` identities. Concurrent direct, static, and on-demand consumers cannot write the same mutable OAuth home without its existing exclusive authority.

### 5.7 Credential isolation is stricter than image isolation

A host receives only its admitted model/repository credentials. Scrub conflicting ambient selectors through existing trusted delivery. Managed-publisher destination credentials stay outside the agent. Read-only token files or a helper returning a broad token are not confinement against arbitrary code. Preserve explicit exposure/high-security policy without building a new proxy merely to claim support.

### 5.8 Support is exact and evidence-gated

Evidence identifies the actual code, image, harness, credential boundary, model, and operations exercised. Compatibility depends on required interfaces and behavior. Evidence for one adapter does not prove a different credential or session protocol, but common production mechanisms can share representative tests instead of independently repeating the full cross-product.

Use existing current-candidate CI and focused real-boundary tests. Keep live-provider qualification separately authorized and honestly limited. Neither an installed binary nor a success flag qualifies a product journey. Conversely, absent live access for an unrelated deleted alias is not a reason to prohibit safe repository cleanup or burn implementation retries.

### 5.9 No silent fallback

Preserve admitted harness, account, model/cost/privacy, source, host-mode constraints, and publication intent. Failure cannot silently select a direct path, another realizer, or broader authority. Recover using the existing bounded owner, distinguishing capacity waits, unavailable observations, and actual incompatibility.

A compatible installation update for future work is not an unrequested account/harness change. Already-started attempts retain their actual provenance. A genuinely changed execution objective or authority uses existing fresh admission, not a rewritten old plan.

### 5.10 Replay and historical truth outlive cutover

Recorded inputs, digests, results, and histories keep their meaning. Preserve executable compatibility only for actual active, pending, replay/reset, or cleanup consumers. Read-only historical access need not keep an old launcher or supervisor running forever.

A maintenance window can stop incompatible writers but cannot make incompatible recorded histories safe. Use relevant replay evidence or a controlled transition. No history deletion, arbitrary retention reduction, or permanent retained fleet is implied by simplification.

### 5.11 Recovery and preservation parity

Reconcile the recorded attempt, session/turn effects, phase receipts, and saved results before retrying. Live-session continuation and restoration into a fresh execution are different operations. Restoring files does not revive credentials, leases, approvals, or permission to repeat remote effects.

Persist confirmed compute and required saved-content evidence before later publication/reporting or destructive cleanup. A failed publication does not erase a valid save. Successful saving does not upgrade failed/canceled compute. Verified artifact storage can supply save-only durability without a GitHub identity; a remote recovery push still needs admitted destination authority. A local path or incomplete upload is not a durable saved result.

The existing finalization and janitor owners share a durable preservation decision across restart. Retain failed saves only under bounded quota/recovery policy and report incompleteness. Release model capacity and credentials after their consumers are confirmed stopped under existing authority, independently of non-sensitive content retention. No second finalizer or permanent live host is required merely to retain files.

## 6. Target topology

Ordinary authoring resolves one meaningful selection into an existing plan, workspace, and attempt-owned Omnigent host. The canonical session/turn boundary serves interactions and records evidence. Existing capture, publisher, recovery, and cleanup owners finish the operation.

Reduce idle services first by removing unused/default-on work. Combine worker responsibilities only when trust, resource, and restart requirements actually permit it. Reuse fleet-specific dependencies and the existing worker lifecycle. Queue names alone do not establish process isolation or aggregate concurrency. Preserve responsive cancellation and cleanup without another worker supervisor or service-count quota.

## 7. Shared image contract

[SharedHostImage.md](SharedHostImage.md) owns build and artifact details. Keep trusted build-time tools, immutable selected artifacts, architecture support, and relevant SBOM/provenance. Its descriptions of old exact-combination promotion are transition context, not an obligation to reconstruct this strategy's retired rollout machinery. Reconcile providing implementations as they change.

## 8. Runtime-pack contract

The existing pack/materializer owners define executable interfaces. Keep descriptors small, trusted, secret-free, and versioned where their actual wire behavior requires it. Tool-version inspection must not require provider authentication. Runtime readiness still checks the real capabilities and credentials that the requested operation needs.

## 9. Product selection and defaults

Preserve the existing Runtime and one Profile authoring boundary and actual server-side agreement checks. Advanced details are not a chain of mandatory Target/Harness/Agent Profile selectors. Profile choice preserves account, model policy, and supported explicit configuration. Advisory discovery/capacity failures preserve valid inventory and drafts rather than selecting another account.

### One shared selection boundary

`runtime_target_selection.py` remains the integration point for Create, presets, schedules, edits/reruns, fresh retries, branches, remediation, continuation, API/MCP, and worker normalization. Simplify its providers and remove redundant defaults with their callers. Do not add a parallel selector or duplicate frontend policy.

### Default promotion is per combination

This former heading is retained for existing references, not as a requirement for per-combination promotion. The target has one installed managed runtime selection with actual capability/authority checks. Independent canary policy, qualification booleans, rollout generations, and display ordering must not compete to choose it. Unknown or incompatible explicit selections remain actionable.

### Preserved identity on continuation

Already-started work retains its recorded meaning and evidence. New launches, including recurring occurrences, follow installed runtime selection while preserving authored harness/Profile, model/cost/privacy, source, and publication intent. Schedules do not independently pin image digests or runtime-provider rollout versions. Preserve schedule identity, cadence, paused state, and meaningful choices during migration. A patch update alone must not require recreating or reapproving every schedule.

## 10. Migration stages

There is no mandatory six-phase product migration or nine-stage deletion sequence. Use one bounded transition through existing deployment ownership: establish the replacement behavior, stop incompatible new admissions, reconcile actual old work and retained-history requirements, switch the existing selection, and remove the obsolete path with its consumers. This is an operational dependency, not another persisted state machine.

Use current records and scoped observation tools for the affected deployment. Missing visibility is unknown, not clean drainage. An unused config alias needs appropriate caller/serialization evidence, not a live provider canary. An active OAuth consumer or history-visible command needs its actual safety evidence. Do not apply every product criterion to every file.

Fresh installs without old consumers should not perform a permanent retirement census. Independent deployments are updated and observed independently. Preserve operator URLs, settings, data, credentials, and the only recoverable work. Recovery uses the same portable controller, with rollback only where schema/data/history compatibility permits it.

## 11. Required acceptance gates

The shared normal path must demonstrate launch, usable session/chat, correctly scoped credentials, saved results, and relevant retry/cancel/recovery behavior. Existing Profile enrollment and canonical command delivery must work through actual consumers. Required source and publication behavior remains with its existing owners, not a new umbrella test suite.

Use common-path CI plus tests for genuinely different credential, storage, or protocol boundaries. Validate real replay changes with appropriate histories. Report source inspection, fixtures, current CI, live observations, and deployed state separately. Missing required evidence cannot become success, while unrelated qualification is not a blanket gate on simplification.

The change must remove competing mechanisms, not just hide them or rename a registry. No fixed class/phase/metric count, whole-catalog conformance ledger, or documentation-wording test is required.

## 12. Non-goals

No new rollout service, retirement inventory, compatibility fingerprint, session orchestrator, shared raw-credential home, account/tenant model, or mandatory static vendor host. No arbitrary workflow-authored image or privileged mount. No weakening of real security, active-work, data-integrity, or historical interpretation requirements.

## 13. Documentation rule

Distinguish desired primary behavior, actual supported paths, and temporary compatibility. A design change is not a live cutover. Remove conflicting old requirements rather than append more validators or exceptions. Keep operational instructions with existing issues/update owners and concise providing contracts. The historical strategy remains in Git, not another permanent active checklist.

# 🌙 MoonMind Roadmap

> Durable, declarative roadmap for making **Omnigent the primary runtime provider for MoonMind over time**, with the cross-cutting goals of **safety, resilience, and observability**.
>
> The destination is not limited to Codex. Codex, Claude Code, OpenCode, and future approved harnesses should enter one generic Omnigent execution plane. The migration remains evidence-gated. Direct and legacy runtime paths continue as explicit compatibility, replay, rollback, and historical-read substrate until their retirement criteria pass.
>
> The canonical runtime-provider strategy is [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](Omnigent/PrimaryRuntimeProviderStrategy.md). The harness mechanics are defined by [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](Omnigent/OmnigentHarnessPlatformDesign.md).
>
> **Document class:** this file is a *canonical declarative document*. It states the durable destination, ownership boundaries, evidence rules, acceptance-claim identifiers, and safety gates that persist across implementation waves. The dated, imperative execution tracker, milestone checklists, PR-by-PR status, tracker maps, and rollout sequencing are disposable working scaffolding and live at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md). When the tracker and this declarative roadmap disagree, the declarative design wins.

---

## Destination

MoonMind orchestrates provider-maintained agents through standard interfaces and runtime adapters. The target runtime provider is **Omnigent**.

A normal UI-authored repository workflow should eventually execute through:

```text
external/omnigent
  -> immutable Omnigent Agent Profile
  -> compatible Provider Profile
  -> one generic Omnigent execution plan and runtime binding
  -> policy-selected Host Class
  -> trusted runtime pack and credential materializer
  -> shared digest-pinned MoonMind Omnigent host image where practical
  -> selected Codex, Claude Code, OpenCode, or future approved harness
  -> canonical session, turn, Workflow Chat, evidence, and cleanup plane
```

MoonMind should not retain a permanent separate execution architecture for every provider CLI. Genuine runtime differences belong behind small registered runtime-pack, credential-materializer, and capability adapters.

Completed historical milestones have been removed from the active roadmap. The durable acceptance-claim identifiers below stay stable even as active execution milestones are renumbered in the tracker.

---

## Primary runtime-provider rules

The durable destination follows these rules:

- **Omnigent is the preferred runtime provider.** New managed coding-agent support should enter through the generic Omnigent harness platform unless a reviewed limitation requires another boundary.
- **MoonMind retains authority.** Temporal orchestration, Provider Profiles, OAuth enrollment, credentials, workspaces, Skills, model and policy selection, publication, checkpoints, remediation, evidence, and cleanup remain MoonMind-owned.
- **One top-level identity.** Omnigent-backed harnesses use `agentKind=external`, `agentId=omnigent`. Harness identity is nested immutable authority.
- **One generic lifecycle.** Codex, Claude Code, OpenCode, and future approved harnesses should share execution planning, leases, host realization, sessions, turns, bridge behavior, evidence, publication, and cleanup.
- **One shared host image where practical.** Codex, Claude Code, and OpenCode should reuse one digest-pinned MoonMind Omnigent host image. Separate Host Classes and support rows may point to the same image digest.
- **Credentials remain isolated.** A shared image never means shared OAuth homes, API keys, credential mounts, or support authority.
- **Differences stay at adapters.** Runtime-specific logic should be limited to trusted runtime-pack descriptors, credential materializers, bounded probes, and truthful capability normalization.
- **Support remains combination-specific.** A binary being present in an image does not qualify its harness. Evidence binds the exact image, harness, runtime pack, materializer, model, policy, architecture, and realizer.
- **No silent fallback.** An explicit Omnigent plan never silently switches to a direct runtime, legacy realizer, another harness, another Provider Profile, another model, or a broader policy.
- **Migration preserves history.** Existing plans and Temporal histories retain their recorded realizer and runtime provenance until replay, rollback, and retention criteria permit removal.

---

## Target ownership split

- **MoonMind owns** Workflow authoring, Temporal orchestration, workflow/run/step identity, Agent Profile and Provider Profile selection, Provider Profile capacity, OAuth enrollment, credential-generation fencing, policy selection, canonical workspaces, Skills and mounted tools, checkpoint/resume/branching, remediation, retrieval, durable artifacts, publication, cleanup, and operator audit evidence.
- **Omnigent owns** the host and runner protocol, harness discovery, the selected live provider process inside the authorized host environment, provider-session interactions, and live upstream events and resources.
- **The MoonMind Omnigent bridge owns** profile-authorized session creation or attachment, canonical session and turn correlation, event normalization and replay, Workflow Detail projection, native Workflow Chat authorization, controls, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex and direct Claude remain migration compatibility substrate** until their supported Omnigent combinations pass the required product and release gates. Historical direct provenance must remain truthful.
- **The legacy Codex profile-bound Omnigent realizer remains explicit compatibility substrate** until generic Codex parity, rollback, and replay criteria pass.
- **OpenCode is the first proven generic-host integration**, not a permanent reason to keep a dedicated OpenCode-only host architecture.
- **The stock proxy topology remains the primary supported browser acceptance path.** Embedded behavior is promoted only through its own compatibility evidence.

---

## Migration shape

The transition follows six durable stages. Detailed execution sequencing belongs in the temporary tracker and GitHub issues.

1. **Shared image reuse.** Promote the OpenCode-derived host image lineage into a neutral shared image and verify Codex, Claude Code, OpenCode, and Omnigent by immutable digest.
2. **Runtime-pack registration.** Make startup, environment shaping, credential staging, model probing, readiness, and attestation consume versioned trusted runtime-pack descriptors.
3. **Generic credential materialization.** Complete generic Codex and Claude OAuth materializers while preserving OpenCode's run-owned API-key materializer and strict cross-runtime isolation.
4. **Generic realizer qualification.** Qualify Codex and Claude combinations on `generic-omnigent-host@1` through deterministic and protected-live evidence.
5. **Product default migration.** Prefer qualified Omnigent Agent Profiles in Workflow Create, presets, schedules, reruns, branches, remediation, continuation, and Workflow Chat.
6. **Legacy retirement.** Stop admitting new legacy paths, preserve replay and historical reads, then remove duplicate runtime, Compose, and credential-host code only when machine-checkable retirement criteria pass.

---

## Omnipresent goals

Every milestone is additionally gated by these durable properties:

- **Safety.** Credential, filesystem, network, publish, approval, retrieval, and control boundaries are enforced at trusted substrate boundaries. Workflows and hosts receive capabilities, refs, and immutable snapshots, not raw infrastructure authority or reusable credential bodies.
- **Resilience.** Runs prefer idempotent retry, evidence-gated resume, branch isolation, bounded degraded mode, and durable cleanup over silent restart-from-scratch. Provider Profiles, billing-relevant settings, constraints, and checkpoint authority are never silently substituted.
- **Observability.** Live state, terminal outcomes, denials, degraded behavior, artifacts, cleanup, recovery decisions, retrieval delivery, runtime-pack identity, host-image identity, materializer identity, and rollout state are inspectable through MoonMind-owned projections, manifests, audit events, and telemetry rather than a second-source runtime dashboard.

---

## Completion and evidence rules

Roadmap status follows evidence, not issue bookkeeping:

- A merged implementation or closed issue may establish useful substrate without satisfying its full acceptance claim.
- A PR whose own verifier reports `ADDITIONAL_WORK_NEEDED`, `BLOCKED`, missing live proof, or unexecuted controlling tests does not close the corresponding roadmap gate.
- A task that requires credentialed, protected, browser-originated, restart, network-enforcement, or provider-conformance evidence remains open until that evidence is independently resolvable and linked.
- A workflow file, fake provider, semantic action stub, self-asserted passing field, caller-supplied expected event list, or installed CLI is not live provider proof.
- “Supported” means the support matrix links passing evidence for that exact combination. “Implemented,” “foundation,” “installed,” and “designed” are weaker states.
- Normal product paths must fail closed rather than silently substitute a Provider Profile, host mode, policy, network, credential, runtime, harness, realizer, repository state, checkpoint, or retrieval scope.
- One shared image does not allow evidence from one harness to qualify another harness.
- Issue closure never rewrites historical runtime provenance or removes the obligation to preserve Temporal replay.
- A closed tracker with known residual work must be reopened or receive a follow-up issue before the roadmap can treat the acceptance claim as owned.

---

## Declarative design first

Each milestone begins by reconciling the canonical declarative documents that own its target-state contracts. Implementation that discovers drift ends with a documentation reconciliation pass. This roadmap states durable desired state. It is not the sole architecture specification, and it is not the place for dated implementation diaries or phased rollout checklists.

The primary runtime-provider strategy owns the long-term direction. The harness platform design owns generic planning and realization mechanics. OAuth, Provider Profile, bridge, control-plane, checkpoint, remediation, and conformance documents own their narrower contracts.

---

## Durable acceptance-claim identifiers

These exact identifiers are pinned by `tests/unit/docs/test_final_docs_cleanup_policy.py` and `tests/integration/docs/test_final_docs_cleanup_contract.py`. They remain stable even when active execution milestones are renumbered in the tracker:

- [ ] **5.1 Checkpoint boundary and completeness** — implementation foundation landed; independently resolvable acceptance evidence remains required.
- [ ] **5.4 Resume-from-checkpoint default flow** — production orchestration must choose safe reattach, cold restore, branch-required, or explicit unavailable outcomes.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — isolated corrected-instruction turns, selectors, compare, promote, and archive in Workflow Detail.
- [ ] **6.2 Omnigent remediation context enrichment** — bounded evidence with target-authorized typed actions and closed residual authority gaps.
- [ ] **7.1 Initial context injection for Omnigent** — durable controlling verification evidence for first-message `ContextPack` injection.

Changing an identifier above is a deliberate owner-approved invariant change. Update the pinning contract tests in the same change rather than deleting an identifier to make a roadmap edit pass.

---

## Durable substrate assumptions

These are shipped, durable capability statements that the roadmap treats as baseline desired state rather than active milestones:

- Omnigent uses the canonical external-agent identity `agentKind=external`, `agentId=omnigent`.
- The generic harness platform provides immutable catalogs, Agent Profiles, credential bindings, materializers, Host Classes, execution plans, runtime bindings, support keys, and a generic host realizer.
- `integration.omnigent.execute` can create or reattach a session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult` for supported combinations.
- Profile authorization, provider leases, host bindings, host leases, credential generations, lifecycle transitions, and redacted preflight evidence are durable without placing credential bodies in Temporal, bridge, checkpoint, workspace, or artifact payloads.
- Static and on-demand hosts use complete image references, UID/GID `1000:1000`, `/home/app`, separate provider credential and Omnigent state, read-only root filesystems, bounded temporary storage, deterministic ownership labels, and explicit cleanup.
- OpenCode has the first deployment-backed generic Host Class, materializer, image, and execution path. That foundation should be generalized rather than preserved as a permanent OpenCode-only architecture.
- The run workflow records per-step Omnigent identity, so Omnigent checkpoint captures select the `external_state_ref` lane, and restore validation rejects stale, mismatched, non-artifact, local-path, or credential-shaped authority.
- The Checkpoint Branch API and persistence model already support create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, immutable instruction digests, workspace policy, git binding, and remediation-created branches.
- Persistent immutable Omnigent policy versions and Agent Profile versions exist with authenticated lifecycle APIs, effective-launch linkage, and bridge evidence. Complete cross-boundary consumption, approvals, ownership, and product-management journeys remain tracked execution work.

---

## Where the imperative tracker lives

Milestone-by-milestone status, current-state notes, per-PR disposition, the open and closed tracker map, priority ordering, and rollout sequencing are dated execution scaffolding, not durable desired state. They live in the disposable tracker at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md) and are refreshed or deleted as execution proceeds.
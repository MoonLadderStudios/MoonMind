# 🌙 MoonMind Roadmap

> Durable, declarative roadmap for moving MoonMind toward **Omnigent host as the unified managed agent runtime**, with a **Codex-first cutover** held to the cross-cutting goals of **safety, resilience, and observability**.
>
> The destination is Codex CLI running through profile-bound Omnigent hosts from the normal MoonMind Workflow interface. Claude-through-Omnigent work has early substrate but stays outside the supported critical path until the Codex contracts and evidence gates are stable.
>
> **Document class:** this file is a *canonical declarative document*. It states the durable destination, ownership boundaries, evidence rules, acceptance-claim identifiers, and safety gates that persist across implementation waves. The dated, imperative execution tracker — milestone checklists, PR-by-PR status, tracker maps, and rollout sequencing — is disposable working scaffolding and lives at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md). When the tracker and this declarative roadmap disagree, the declarative design wins.

---

## Destination

MoonMind orchestrates provider-maintained agents through standard interfaces and runtime adapters. The target runtime is **Omnigent host as the unified managed agent runtime**: a normal UI-authored repository workflow executes in the exact authorized workspace through a policy-selected stock Codex Omnigent host, resumes from MoonMind-owned checkpoint evidence, and is governed by reusable policy and agent-profile versions with enforced network boundaries.

Completed historical milestones have been removed from the active roadmap; the durable acceptance-claim identifiers below stay stable even as active execution milestones are renumbered in the tracker.

---

## Target ownership split

- **MoonMind owns** Workflow authoring, Temporal orchestration, workflow/run/step identity, Provider Profile selection and capacity, policy/profile selection, canonical workspaces, checkpoint/resume/branching, remediation, retrieval, durable artifacts, publication, and operator audit evidence.
- **Omnigent host owns** the live provider process inside the authorized host environment, host-side session resources, provider/harness interactions, and live upstream events.
- **The MoonMind Omnigent bridge owns** profile-authorized session creation or attachment, event normalization and replay, Workflow Detail projection, controls, resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Direct Codex remains migration compatibility substrate** while the deployed cutover phase remains `opt_in`. Historical direct provenance must remain truthful, and explicit Omnigent selection must never silently fall back to direct Codex.
- **The stock proxy topology is the primary supported acceptance path.** Embedded mode remains experimental.
- **Claude Code remains outside the current Omnigent critical path.** Existing direct Claude support remains available; early Claude-through-Omnigent substrate is not a support claim.

---

## Omnipresent goals

Every milestone is additionally gated by these durable properties:

- **Safety.** Credential, filesystem, network, publish, approval, retrieval, and control boundaries are enforced at trusted substrate boundaries. Workflows and hosts receive capabilities, refs, and immutable snapshots — not raw infrastructure authority or reusable credential bodies.
- **Resilience.** Runs prefer idempotent retry, evidence-gated resume, branch isolation, bounded degraded mode, and durable cleanup over silent restart-from-scratch. Provider Profiles, billing-relevant settings, constraints, and checkpoint authority are never silently substituted.
- **Observability.** Live state, terminal outcomes, denials, degraded behavior, artifacts, cleanup, recovery decisions, retrieval delivery, and rollout state are inspectable through MoonMind-owned projections, manifests, audit events, and telemetry rather than a second-source runtime dashboard.

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

## Declarative design first

Each milestone begins by reconciling the canonical declarative documents that own its target-state contracts. Implementation that discovers drift ends with a documentation reconciliation pass. This roadmap states durable desired state; it is not the sole architecture specification, and it is not the place for dated implementation diaries or phased rollout checklists.

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

- Omnigent uses the canonical external-agent identity `agentKind=external`, `agentId=omnigent`; `integration.omnigent.execute` can create or reattach a session, post the first message idempotently, stream events, harvest terminal evidence, and return a canonical `AgentRunResult`.
- Profile authorization, provider leases, host bindings, host leases, credential generations, lifecycle transitions, and redacted preflight evidence are durable without placing credential bodies in Temporal, bridge, checkpoint, workspace, or artifact payloads.
- Static and on-demand hosts use complete image references, UID/GID `1000:1000`, `/home/app`, separate provider OAuth and Omnigent state, read-only root filesystems, bounded temporary storage, deterministic ownership labels, and explicit cleanup.
- The run workflow records per-step Omnigent identity, so Omnigent checkpoint captures select the `external_state_ref` lane, and restore validation rejects stale, mismatched, non-artifact, local-path, or credential-shaped authority.
- The Checkpoint Branch API and persistence model already support create, turn launch, continue, fork, compare, promote, archive, source checkpoint identity, immutable instruction digests, workspace policy, git binding, and remediation-created branches.
- Persistent immutable Omnigent policy versions and agent-profile versions exist with authenticated lifecycle APIs, effective-launch linkage, and bridge evidence; complete cross-boundary consumption, approvals, ownership, and product-management journeys remain tracked execution work.

---

## Where the imperative tracker lives

Milestone-by-milestone status, current-state notes, per-PR disposition, the open/closed tracker map, priority ordering, and rollout sequencing are dated execution scaffolding, not durable desired state. They live in the disposable tracker at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md) and are refreshed or deleted as execution proceeds.

# Omnigent Contract Ownership Map

**Document Class:** Module ownership map
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-10-14
**Authority:** Maps every `docs/Omnigent/` path and known duplicated section to
its one surviving owner. Owners hold the contract; all other docs point.

## Unique contracts and surviving owners

| # | Contract | Surviving owner |
| --- | --- | --- |
| 1 | Pinned upstream transport, provider/service session facade, native Workflow Chat facade, bridge session store | `OmnigentBridge.md` |
| 2 | Adapter launch modes, workspace/mount/network contract, readiness, session/stream/artifact contract | `OmnigentAdapter.md` |
| 3 | Module packages, dependency direction, ports | `OmnigentModuleArchitecture.md` §§1–4, 6 |
| 4 | Retained duplicate architecture and legacy new-admission control (code-owned table) | `OmnigentModuleArchitecture.md` §5 over `moonmind/omnigent/legacy_retirement.py` |
| 5 | Target harness-platform model (catalog, attestation, plans, bindings, capability negotiation) | `OmnigentHarnessPlatformDesign.md` (Status: Proposed) |
| 6 | Product selection, defaults, migration stages, acceptance gates | `PrimaryRuntimeProviderStrategy.md` |
| 7 | Shared image, runtime packs, Host Classes, credential ownership, realizer admission | `SharedHostImage.md` |
| 8 | Credential materializer mechanisms, enrollment, launch ordering, rotation, cleanup detail | `OmnigentHostOAuth.md` |
| 9 | OpenCode runtime contract on the shared image | `OpenCodeHost.md` |
| 10 | Agent / Provider Profile identities and discovery/launch security | `AgentProfiles.md` |
| 11 | Canonical turn-command boundary, admission, terminal meanings | `CanonicalTurnCommandBoundary.md` |
| 12 | Lifecycle reconciler transition contract | `OmnigentLifecycleReconciler.md` |
| 13 | Control-plane aggregates, concurrency/fencing, telemetry/timeline | `ControlPlaneAggregates.md`, `ControlPlaneConcurrencyAndFencing.md`, `OmnigentSemanticTelemetryAndTimeline.md` |
| 14 | Versioned rollout states, canary, rollback, migration status | `RuntimeProviderRollout.md` |
| 15 | Codex support evidence, active cutover diagnostic/evidence projection (phases retired for new-work admission), optional remediation observations and strict certification | `CodexSupportAndCutover.md` |
| 16 | Codex create-to-host wire contract | `CodexCreateToHostContract.md` |
| 17 | Codex product-path reconciliation | `NormalCodexProductPathReconciliation.md` |
| 18 | Conformance tiers and live-smoke boundaries | `ConformanceAndLiveSmoke.md` |
| 19 | Concurrency qualification record | `ConcurrencyQualification.md` |
| 20 | Fault-injection suite and invariants | `OmnigentFaultInjectionSuite.md` |
| 21 | Mounted runtime tools and `gh` capability | `OmnigentHostMountedTools.md` |
| 22 | Policy authority, persistence, approvals | `PolicyAuthority.md` |
| 23 | Embedded host-auth compatibility surface | `EmbeddedHostAuthCompatibility.md` |
| 24 | Combined-stack validation and rollback runbook | `CombinedStackValidationAndRollback.md` |
| 25 | Upstream-duplication audit (caller-backed ownership vs pinned upstream) | `UpstreamDuplicationAudit.md` |

## Duplicated sections and their surviving owners

These sections restated an owner-held contract. Each now points at its owner
instead of carrying a second description:

| Duplicated section | Surviving owner |
| --- | --- |
| `PrimaryRuntimeProviderStrategy.md` §7 shared-image rules | `SharedHostImage.md` §1 |
| `PrimaryRuntimeProviderStrategy.md` §8 runtime-pack descriptor rules | `SharedHostImage.md` §2 |
| `OmnigentHostOAuth.md` §10 shared-image isolation | `SharedHostImage.md` §§3–4 |
| `OmnigentHostOAuth.md` §11 Host Class / runtime-pack listing | `SharedHostImage.md` §3 |
| `OpenCodeHost.md` §1 shared-image implementation | `SharedHostImage.md` §1 |
| `OpenCodeHost.md` §2 governing image rules | `SharedHostImage.md` §1 |
| `OpenCodeHost.md` §3 Host Class mapping | `SharedHostImage.md` §3 |
| `OpenCodeHost.md` §6 shared-image credential isolation | `SharedHostImage.md` §4 |

Deliberately *not* merged (distinct responsibilities):

- Omnigent vs ManagedAgents execution: provider-native session semantics stay
  in `docs/ManagedAgents/`; only the shared container-job/MCP contract is
  cross-linked (`DockerBackendService.md` §15).
- Run-owned vs profile-owned credential cleanup: never collapsed into one
  "delete credentials" instruction (`SharedHostImage.md` §4).
- `CodexSupportAndCutover.md` vs `RuntimeProviderRollout.md`: the former owns
  Codex evidence and the retained phase diagnostic projection; the latter owns the
  surviving selection/rollout controls and identifies retired configuration.
- `CodexCreateToHostContract.md` vs
  `NormalCodexProductPathReconciliation.md`: the former owns the wire contract;
  the latter owns the reconciliation. Preserve executable wire behavior and
  supported product outcomes through their implementation tests. Update prose
  when its providing implementation changes; wording is not a tested contract
  or a permanent obligation to retain superseded machinery.

## Per-file disposition

| File | Disposition | Design status |
| --- | --- | --- |
| `README.md` | Module entrypoint | Current |
| `ContractOwnership.md` | Ownership map | Current |
| `SharedHostImage.md` | Owner: image, packs, Host Classes, credential ownership | Implemented; qualification/promotion evidence-gated. Per-combination assignments below remain authoritative for current behavior; `PrimaryRuntimeProviderStrategy.md` states the desired simplification target. Retirement is class-based new-admission control only: removing a retained path deletes its code and row while the surviving owner's replay, historical-read, cleanup, and credential checks keep passing (`OmnigentModuleArchitecture.md` §5.2) |
| `PrimaryRuntimeProviderStrategy.md` | Owner: selection/defaults/stages/gates; §§7–8 point to `SharedHostImage.md` | Desired simplification target; ordinary certificate-independent admission is implemented, while surviving selection controls remain code-owned |
| `OmnigentHostOAuth.md` | Owner: materializer mechanisms; §§10–11 point to `SharedHostImage.md` | Current desired state |
| `OpenCodeHost.md` | Owner: OpenCode runtime contract; §§1–3, 6 point to `SharedHostImage.md` | Current |
| `OmnigentBridge.md` | Owner: transport and native UI contract | Design with open questions (§21) |
| `OmnigentAdapter.md` | Owner: adapter launch/lifecycle contract | Current |
| `OmnigentModuleArchitecture.md` | Owner: packages, dependencies, retirement description | Current |
| `OmnigentHarnessPlatformDesign.md` | Owner: target harness-platform model | Proposed (target, not shipped) |
| `RuntimeProviderRollout.md` | Owner: rollout mechanism | Implemented mechanism; promotion per combination |
| `CodexSupportAndCutover.md` | Owner: Codex rows, phases, remediation matrix | Exact evidence pending |
| `CodexCreateToHostContract.md` | Owner: create-to-host wire contract | Accepted; executable consumers own behavior coverage |
| `NormalCodexProductPathReconciliation.md` | Owner: product-path reconciliation | Accepted; executable consumers own behavior coverage |
| `AgentProfiles.md` | Owner: Profile identities | Desired-state design |
| `CanonicalTurnCommandBoundary.md` | Owner: turn-command boundary | Implemented |
| `OmnigentLifecycleReconciler.md` | Owner: transition contract | Accepted; executable consumers own behavior coverage |
| `ControlPlaneAggregates.md` | Owner: durable aggregates | Proposed design |
| `ControlPlaneConcurrencyAndFencing.md` | Owner: fencing generations | Proposed design |
| `OmnigentSemanticTelemetryAndTimeline.md` | Owner: telemetry and timeline | Implemented |
| `ConformanceAndLiveSmoke.md` | Owner: conformance tiers | Current |
| `ConcurrencyQualification.md` | Owner: concurrency qualification | Target design |
| `OmnigentFaultInjectionSuite.md` | Owner: fault-injection suite | Desired-state design |
| `OmnigentHostMountedTools.md` | Owner: mounted tools | Desired-state design |
| `PolicyAuthority.md` | Owner: policy authority | Canonical desired state |
| `EmbeddedHostAuthCompatibility.md` | Owner: embedded compat surface | Experimental, not default |
| `CombinedStackValidationAndRollback.md` | Owner: validation/rollback runbook | Current |
| `UpstreamDuplicationAudit.md` | Owner: caller-backed upstream-duplication audit | Current |

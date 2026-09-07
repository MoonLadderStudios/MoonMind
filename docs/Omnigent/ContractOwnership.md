# Omnigent Contract Ownership Map

**Document Class:** Module ownership map
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-09-07
**Authority:** Maps every `docs/Omnigent/` path and known duplicated section to
its one surviving owner. Owners hold the contract; all other docs point.
**Issue:** [MoonLadderStudios/MoonMind#3962](https://github.com/MoonLadderStudios/MoonMind/issues/3962).

Parent: #3929. Coordinates with #3928 (integration boundaries) and
#3832–#3835 (qualification, default promotion, Compose consolidation,
retirement). Completed scaffolding is disposed under #3963; current rollout
work stays in its owning issue plan (`docs/tmp/OmnigentBridgeRollout.md`).

## Unique contracts and surviving owners

| # | Contract | Surviving owner |
| --- | --- | --- |
| 1 | Pinned upstream transport, provider/service session facade, native Workflow Chat facade, bridge session store | `OmnigentBridge.md` |
| 2 | Adapter launch modes, workspace/mount/network contract, readiness, session/stream/artifact contract | `OmnigentAdapter.md` |
| 3 | Module packages, dependency direction, ports | `OmnigentModuleArchitecture.md` §§1–4, 6 |
| 4 | Retained duplicate architecture and removal staging (code-owned inventory) | `OmnigentModuleArchitecture.md` §5 over `moonmind/omnigent/legacy_retirement.py` |
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
| 15 | Codex exact support rows, cutover phases, remediation matrix | `CodexSupportAndCutover.md` |
| 16 | Codex create-to-host wire contract | `CodexCreateToHostContract.md` |
| 17 | Codex product-path reconciliation | `NormalCodexProductPathReconciliation.md` |
| 18 | Conformance tiers and live-smoke boundaries | `ConformanceAndLiveSmoke.md` |
| 19 | Concurrency qualification record | `ConcurrencyQualification.md` |
| 20 | Fault-injection suite and invariants | `OmnigentFaultInjectionSuite.md` |
| 21 | Mounted runtime tools and `gh` capability | `OmnigentHostMountedTools.md` |
| 22 | Policy authority, persistence, approvals | `PolicyAuthority.md` |
| 23 | Embedded host-auth compatibility surface | `EmbeddedHostAuthCompatibility.md` |
| 24 | Combined-stack validation and rollback runbook | `CombinedStackValidationAndRollback.md` |

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

Deliberately *not* merged (distinct responsibilities, per #3962):

- Omnigent vs ManagedAgents execution: provider-native session semantics stay
  in `docs/ManagedAgents/`; only the shared container-job/MCP contract is
  cross-linked (`DockerBackendService.md` §15).
- Run-owned vs profile-owned credential cleanup: never collapsed into one
  "delete credentials" instruction (`SharedHostImage.md` §4).
- `CodexSupportAndCutover.md` vs `RuntimeProviderRollout.md`: the former owns
  Codex exact rows and cutover phases; the latter owns the versioned rollout
  mechanism for every combination.
- `CodexCreateToHostContract.md` vs
  `NormalCodexProductPathReconciliation.md`: the former owns the wire contract;
  the latter owns the reconciliation. Both are test-pinned and keep their text.

## Per-file disposition

| File | Disposition | Design status / outstanding refs |
| --- | --- | --- |
| `README.md` | Module entrypoint (new, #3962) | Current |
| `ContractOwnership.md` | Ownership map (new, #3962) | Current |
| `SharedHostImage.md` | Owner: image, packs, Host Classes, credential ownership | Implemented; qualification/promotion/retirement evidence-gated (#3832–#3835) |
| `PrimaryRuntimeProviderStrategy.md` | Owner: selection/defaults/stages/gates; §§7–8 point to `SharedHostImage.md` | Canonical desired state, evidence-gated per combination |
| `OmnigentHostOAuth.md` | Owner: materializer mechanisms; §§10–11 point to `SharedHostImage.md` | Current desired state |
| `OpenCodeHost.md` | Owner: OpenCode runtime contract; §§1–3, 6 point to `SharedHostImage.md` | Current |
| `OmnigentBridge.md` | Owner: transport and native UI contract | Design with open questions (§21); #3635 |
| `OmnigentAdapter.md` | Owner: adapter launch/lifecycle contract | Current |
| `OmnigentModuleArchitecture.md` | Owner: packages, dependencies, retirement description | Current; #3711 (retirement execution #3712) |
| `OmnigentHarnessPlatformDesign.md` | Owner: target harness-platform model | Proposed (target, not shipped) |
| `RuntimeProviderRollout.md` | Owner: rollout mechanism | Implemented mechanism; promotion per combination |
| `CodexSupportAndCutover.md` | Owner: Codex rows, phases, remediation matrix | Exact evidence pending; #3518, #3563, #3564, #3622, #3626 |
| `CodexCreateToHostContract.md` | Owner: create-to-host wire contract (test-pinned) | Accepted; #3449 |
| `NormalCodexProductPathReconciliation.md` | Owner: product-path reconciliation (test-pinned) | Accepted; #3565 |
| `AgentProfiles.md` | Owner: Profile identities | #3517 |
| `CanonicalTurnCommandBoundary.md` | Owner: turn-command boundary | #3707 |
| `OmnigentLifecycleReconciler.md` | Owner: transition contract | Accepted; #3702 |
| `ControlPlaneAggregates.md` | Owner: durable aggregates | #3703 |
| `ControlPlaneConcurrencyAndFencing.md` | Owner: fencing generations | #3704 |
| `OmnigentSemanticTelemetryAndTimeline.md` | Owner: telemetry and timeline | #3708 |
| `ConformanceAndLiveSmoke.md` | Owner: conformance tiers | Current; #3480, #3508, #3642, #3710 |
| `ConcurrencyQualification.md` | Owner: concurrency qualification | #3885 |
| `OmnigentFaultInjectionSuite.md` | Owner: fault-injection suite | #3709 |
| `OmnigentHostMountedTools.md` | Owner: mounted tools | Desired-state design |
| `PolicyAuthority.md` | Owner: policy authority | Canonical desired state; #3515 |
| `EmbeddedHostAuthCompatibility.md` | Owner: embedded compat surface | Experimental, not default |
| `CombinedStackValidationAndRollback.md` | Owner: validation/rollback runbook | Current; #3564 |

## Reduction report

Reduction is an outcome of this pass, not a justification for dropping
contracts: every contract above retains exactly one surviving owner and no
owner text was deleted, only duplicated restatements were replaced with
pointers. Measured with `git diff` word counts on `docs/Omnigent/`:

- Eight duplicated sections replaced by surviving-owner pointers
  (Strategy §§7–8, HostOAuth §§10–11, OpenCodeHost §§1–3, 6): about 400
  words of second descriptions removed.
- Two new routing docs added: `README.md` entrypoint (~615 words) and this
  map (~1,030 words).
- Ten existing files gained owner cross-links (`Related documents` blocks
  plus ownership notes in `CodexSupportAndCutover.md`,
  `OmnigentHarnessPlatformDesign.md`, `OpenCodeHost.md` §15).
- Net word delta is positive by design: routing words replaced duplicate
  contract words. No relied-upon contract lost its owner; the guard test
  `tests/unit/docs/test_omnigent_consolidation_3962.py` pins the entrypoint,
  the per-file coverage of this map, the surviving credential/exact-support
  phrases, the pointer replacements, and link/anchor validity.

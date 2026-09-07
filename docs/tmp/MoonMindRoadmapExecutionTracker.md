# MoonMind Roadmap — Execution Tracker (disposable)

> **Document class:** Dated execution scaffolding, not canonical architecture.
> **Recovery/remediation review:** September 7, 2026, `main` at `49fca8528f39b38ddb1f09d580c061d8df03140f`.
> **Other historical rows:** Carried from the July 29, 2026 tracker and explicitly not re-audited here.
> Durable outcomes and stable acceptance identifiers remain in [MoonMind Roadmap](../MoonMindRoadmap.md). Canonical providing contracts win over this tracker. #3965 owns eventual transfer/disposal without losing unresolved obligations.

## Current direction and scope of this refresh

#3825 remains the primary generic Omnigent program. Codex, Claude Code, keyed OpenCode, and credentialless OpenCode use shared execution, workspace, session/turn, recovery, artifact, and cleanup owners with exact per-combination qualification. Normal authoring uses Runtime and one Profile. Direct/profile-bound paths are explicit compatibility, not automatic failure fallbacks.

This refresh replaces the old recovery/remediation current-state claims, including the claim that coordinator/branch execution methods have no production callers. It does not declare every historical milestone complete or re-audit all current issue states. Historical Codex-first sequencing below does not impose a new global prerequisite on the current generic program.

**Evidence collected:** Source, existing issue discussions, and merged PR descriptions. No deployed reproduction, live provider calls, new browser execution, Docker/Temporal/PostgreSQL fault injection, or repository test execution occurred in this review. The user reports that remediation and failed-step recovery are not working properly. The source gaps below are reproducible targets, not a claim that every reported incident has one proven cause.

## Recovery and remediation: source-backed rebaseline

| Boundary | Delivered substrate to preserve | Remaining gap / owner |
| --- | --- | --- |
| Failed-step admission | `service.py` validates source run/input/plan/checkpoint and creates linked recovery; typed target and entry policy exist | Typed target admission explicitly rejects publication/restoration targets and preserved-step failed-step requests. Finish the real phase/restore/preserved-output handoff, not merely remove guards. #3510, coordinating #4018/#1090 |
| Recovery capabilities | Versioned capability descriptors and a shared recovery decision helper exist | The top-level Omnigent descriptor still identifies `codex-native` / profile-bound session restore. Resolve support by actual admitted generic configuration; enforce boundary support consistently. #3510 with #3933/#3832 |
| Omnigent execution recovery | Activities now call existing `recover_from_checkpoint` / `branch_from_checkpoint`; generic realizer dispatch also exists | The profile-bound path explicitly rejects cold restore without an owned workspace-restoration boundary. Trace and qualify the generic equivalent separately. Do not revive the obsolete no-callers diagnosis. #3510 |
| Checkpoint Branch execution | Intent-only launch, deterministic identities, canonical command claims, `MoonMind.CheckpointBranchTurn`, real AgentRun execution, checkpoint and terminal retention exist | Source/destination authority is conflated: selected Profile/policy changes are rejected and the source execution plan is reused. Distinguish scoped child continuation from explicitly independently admitted operator work; never remove publication-scope guards wholesale. #3621 |
| Branch preservation | Existing terminal retention and cancellation/fallback records preserve useful bounded evidence | Branch workflow captures only after successful AgentRun return using a fixed sandbox archive route; later capture failure creates a generic error result instead of preserving the successful child envelope at this handoff. Determine actual workspace ownership and integrate shared save/finalization. #4016 with #3621 |
| Repair verification | Typed `RemediationVerificationPhase`, outcome vocabulary, fresh-record reads, cancellation, and bounded stabilization exist | Branch verification reads the source execution row rather than the exact branch result/objective. The default reader accepts but does not enforce pinned run identity. Long-running pending verification needs a durable completion path independent of short polls. #3622 |
| Action availability | Existing typed adapters and capability intersections exist; unsupported operations can be disabled | Session routing is still managed-session-shaped and several host/lease/session/helper/cleanup verifier contracts are explicitly unavailable. Finish the narrowly supported useful generic actions or keep them disabled, not blanket enablement. #3624 |
| Cumulative in-workflow repair | Cadence, named-Skill inputs, candidate-head/progress evidence and escaped-regression assets exist | Prove actual multi-attempt candidate preservation, latest-report materialization, no-progress decisions and phase-aware resume through production composition. User-visible completion is not established by helper fixtures. #3512 |
| Operator release | #3691 delivered versioned matrix/gate infrastructure and reported hermetic/browser tests | Its own remaining-risk section says credentialed operator and live egress verification were unavailable. Preserve machinery and finish the missing exact claimed evidence. #3626 with #3832 |

Source owners at the reviewed revision:

- `moonmind/workflows/temporal/service.py`, `recovery_decision.py`, and `recovery_entry.py`.
- `moonmind/workflows/executions/runtime_capabilities.py`.
- `moonmind/workflows/temporal/activities/omnigent_activities.py` and the selected generic realizer.
- `api_service/services/checkpoint_branch_turn_execution.py` and `moonmind/workflows/temporal/workflows/checkpoint_branch_turn.py`.
- `api_service/services/remediation_actions.py` and `moonmind/workflows/temporal/remediation_verification.py`.
- Existing Workflow/Step Execution, artifact, approval, canonical turn, runtime binding, and workspace/finalization owners.

## Active recovery/remediation ownership

| Owner | Scope |
| --- | --- |
| #3510 | Unchanged-input failed/selected-step recovery, exact capability and phase admission, actual restoration, preserved prior steps, truthful product actions |
| #3512 | Product integration and cumulative remediation acceptance, using existing child owners rather than another repair engine |
| #3621 | Existing branch-turn owner's generic-runtime/source-versus-destination admission and continuation correctness |
| #3622 | Exact-result verification, pinned identity, durable pending-to-terminal verification, and immutable-source result interpretation |
| #3624 | Useful generic action adapters/readiness and owning verifier integration without widening authority |
| #3626 | Operator remediation qualification and separately gated autonomous rollout, reusing the implemented matrix |
| #4015 / #4016 / #4017 | Capture, independent compute/save/publication finalization, and bounded retained-work ownership |
| #4018 / #1090 | Publication-only recovery and the one authored publication contract |
| #4020 | Result/continuation/publication-only presentation, consuming canonical outcomes |
| #3832 / #3833 / #3950 | Exact generic qualification, Runtime + Profile cross-surface admission, and required CI enforcement |
| #3965 | Remaining historical tracker handoff and disposal |

Older #3510/#3512 closures and #362x follow-up closures are not current acceptance proof. Reopening a remaining owner credits delivered code rather than instructing agents to rebuild it. Leave delivered approval, UI, or narrow authority repairs closed unless new source evidence identifies remaining scope for those particular owners.

## Recovery acceptance gates retained

- [ ] **5.1 Checkpoint boundary and completeness:** Independently readable, correctly scoped content and validated manifests, including after source disposal.
- [ ] **5.4 Resume-from-checkpoint default flow:** Normal product admission and actual recovery choose supported reattachment/cold restore/branch-required/unavailable behavior, with explicit phase and no repeated accepted work.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps:** Existing turn execution supports its exact admitted source/destination semantics, normal Profile selection, isolation, continue/fork, compare, publish/promote/archive distinctions, and verified results.
- [ ] **6.2 Omnigent remediation context enrichment:** Authorized bounded evidence, useful exact action capabilities, approvals, result-bound verification, cumulative attempts, and retained operator qualification.

These are the roadmap's stable claims, not the old tracker's local milestone numbering. **7.1 Initial context injection for Omnigent** remains preserved below and in the roadmap; it is not closed by this review.

## Immediate execution order

1. Land bounded diagnosis and correctness regressions at the failing production boundaries. Keep valid existing implementation and safety guards.
2. Complete unchanged-input recovery and independently admitted corrective work using the shared runtime/workspace/turn owners. Automatic child branches retain their parent's frozen publication scope. Changed publication requires explicit normal independent admission, not a per-turn override.
3. Bind verification to the exact linked result and complete its durable pending lifecycle. Verify cumulative candidate progression and no-repeat behavior across interruption.
4. Finish shared preservation handoffs before claiming source-host-independent recovery. Determine which cleanup operation actually deletes the workspace.
5. Qualify each advertised generic combination through existing exact-artifact/protected-live infrastructure. Separate manual diagnosis, manual mutation, and autonomous rollout gates. Do not block a narrow fix on every unrelated connector, static-host, or retirement item.

The July checkpointless-headless diagnosis is historical, not a current blanket finding. The canonical cadence now requires either a workflow-owned candidate checkpoint or an explicitly permitted same-branch, exact-SHA, authorized, uncontaminated, remotely verified source. This review requires proof of those guards through the real loop. It does not claim an arbitrary surviving checkout is safe, nor that a branch-based continuation is cold checkpoint restoration.

## Historical substrate ledger — July 29, not current support claims

The earlier implementation wave recorded these foundations:

| Historical issue / PR | Delivered slice recorded by the old tracker | Acceptance obligation retained |
| --- | --- | --- |
| #3509 / #3554 | Versioned Omnigent checkpoint identity, artifact validation and cold-restore input material | Completeness, production restoration and product recovery proof |
| #3511 / #3544 | Remediation Create/context/read tools and typed-action foundations | Exact target authority, useful adapters, approvals, verification and operator acceptance through current owners |
| #3513 / #3545 | Initial ContextPack persistence, digest binding and first-message linkage | Independently resolvable controlling verification; stable claim 7.1 |
| #3514 / #3552 | Session-scoped retrieval capability, budgets, accounting, delivery and revocation substrate | Actual runtime invocation, complete authoring/continuation coverage and denial/delivery proof |
| #3515 / #3546 | Persistent immutable policy versions and effective-launch evidence | Cross-boundary consumption, approval, ownership, dependent use and migration |
| #3517 / #3547 | Immutable profile model/API, bundle validation and inventory/list UI | Full management, provenance, synchronization, launch validation and consumer coverage |
| #3518 / #3548 | Compatibility inventory, versioned support/promotion machinery | Actual evidence-gated promotion, histories/rollback and retirement; machinery alone is not removal |
| #3519 / #3549 | Embedded readiness, diagnostics and evidence validation | Exact unchanged-host compatibility and safe upgrade/rollback proof |
| #3520 / #3550 | Early Claude profile/host/recovery substrate | Exact generic Claude lifecycle, cold recovery, branch/RAG/remediation and non-regression proof |
| #3553 / #3556 | Managed-session/workflow-failure repairs and headless crash mitigation | Actual safe cumulative-source admission, not an inferred security guarantee |

The old description of deployed `opt_in`, open PR #3555, or issue status is not a September deployment observation. Current runtime support/promotion/retirement comes from its providing registry, policy and evidence owners. Do not resurrect the old six-phase framework or add mandatory profile selectors to match this history.

## Unreviewed historical milestone obligations retained for handoff

The following compact inventory retains the old local item identifiers and their unique requirements. It is **not** a declaration that each named old issue is currently open, incomplete, or the latest owner. #3965 must map each to current implementation/evidence and an existing successor before removing it. Unknown status is unresolved, not complete.

### Historical Milestone 1 — Normal product path and protected acceptance

- **1.0:** Reconcile Create-to-host, workspace, adapter, OAuth, combined-stack and managed/external execution ownership.
- **1.1, #3507:** Authored repository/branch/attachments/Skills/tools, canonical workspace materialization, checkpoint/external state, publication/output manifest, diagnostics, partial startup, shared runtime and claimed static/on-demand behavior.
- **1.2, #3508:** Real authorized browser-originated enrolled-profile journey covering exact stock host, reads/mutation, restart/replay, failure, cancellation, cleanup/janitor and denial/secret-safety evidence.
- **1.3:** Independently resolvable digest-checked linkage to #3508, #3448 and current support/promotion owners.

### Historical Milestone 2 — Resume and Checkpoint Branches

- **2.0–2.1:** Preserve delivered checkpoint/Step Execution/branch contract reconciliation and versioned capture/restore material; do not rebuild the foundation.
- **2.2:** Complete stable claim 5.1 proof of boundary, artifacts, identity/generation validation and source-independent restoration.
- **2.3–2.4:** Default evidence-gated recovery and isolated branch execution/product controls now use #3510/#3621 and current shared owners.
- **2.5:** Required replay/restart, duplicate delivery, partial artifact, capacity, cancellation, cleanup and bounded checkpointless-source tests remain acceptance obligations.

### Historical Milestone 3 — Operator remediation

- **3.0:** Canonical authoring, evidence, action, cumulative attempt, approval, branch, verification and rollout contracts are reconciled by this documentation change; runtime acceptance remains open.
- **3.1:** Target-authorized janitor/helper/host/lease effects remain with #3624 and their actual owners. Earlier findings are rechecked before reopening delivered repairs.
- **3.2:** Product UI, durable approvals, verification, locks/cooldowns, no-progress, prevention and cancellation proof remain #3512 and its existing children.
- **3.3:** Corrective branches use #3621; no modified-input failed-step Resume or per-turn publication escape.
- **3.4:** Autonomous mutation stays separately gated by #3626 after the operator matrix and policy/telemetry/cancellation proof.

### Historical Milestone 4 — Omnigent RAG

- **4.0:** Reconcile first-message and in-session scope, capability exchange, budgets, degraded behavior, delivery/revocation, and authoring.
- **4.1 / stable claim 7.1:** Preserve independently resolvable controlling initial-context verification before claiming completion.
- **4.2, #3514:** Actual host/tool discovery and invocation, bounded session delivery and acknowledgment, overlay/fallback/timeout, retention/redaction and denial.
- **4.3:** Same retrieval contract across Create, schedules, persistent configurations, Checkpoint Branches and remediation. No raw embedding, retrieval-store, artifact-store or general infrastructure credentials in agents.

### Historical Milestone 5 — Policy, profiles and restricted egress

- **5.0:** Reconcile policy/configuration, Settings/Provider Profiles, adapter/workspace/checkpoint/remediation/RAG and observability ownership.
- **5.1, #3515:** One immutable effective policy across launch, bridge, workspace, checkpoint, retrieval, remediation, approval and audit; ownership, dependent use, activation impact and environment-default migration.
- **5.2, #3516 / historical PR #3555 / historical claim 11.1:** Actual Docker/network allow-deny, DNS, redirects, IPv6, direct-IP bypass, stale attestation, gateway health, claimed host/workload modes, cleanup and negative conformance. An installed/configured gateway is not enforcement proof.
- **5.3, #3517:** Create/clone/edit-as-new-version/detail/diff/usage/deletion, upstream sync, bundle provenance, bounded real smoke validation and bootstrap migration. Reconcile old selector requirements with current Runtime + one Profile; subordinate execution configuration remains under its existing owner.

### Historical Milestone 6 — Cutover, compatibility and retirement

- **6.0–6.2:** Retain useful compatibility classification, support-row/evidence machinery and truthful onboarding. Earlier completed editorial/machinery slices do not establish current deployed support.
- **6.3:** Replace self-asserted support with observed, provenance-bound protected evidence.
- **6.4:** Qualify and promote each normal authoring surface/cohort with bounded telemetry, objective thresholds and rollback.
- **6.5:** Preserve direct/generic provenance, historical reads, actual persisted schema/history consumers and supported mixed-worker replay/reset obligations.
- **6.6:** Stop new admission, drain real work/resources and resolve rollback/history retention before actual launcher/UI/config deletion. Keep necessary event/read evidence without permanent duplicate execution engines.
- **6.7:** Release metadata reports actual exact supported rows and promotion state. Use #3825/#3832/#3833/#3925's current owners, not a new cutover framework.

### Historical Milestone 7 — Embedded compatibility

- **7.0, #3519:** Transfer any unmet acceptance to the current existing compatibility owner before disposal.
- **7.1:** Exact unchanged-host registration, reconnect/restart, auth rotation/revocation, session/event/resource/control, claimed host modes, network, failure, upgrade and rollback evidence.
- **7.2:** Independently qualify topology/version changes and retain a tested rollback; no hidden proxy/embedded switch, host fork or second login. Existing proxy-first support cannot qualify a different topology.

### Historical Milestone 8 — Claude parity

- **8.0, #3520:** Preserve unmet obligations through the current generic program rather than assuming old closure proves parity.
- **8.1:** Shared account/credential capacity, host/binding generations, workspace, policy, egress, lifecycle, cleanup and janitor guarantees.
- **8.2:** Truthful runtime/bridge/UI/checkpoint/RAG/remediation support and degraded cases.
- **8.3:** Exact enrolled-profile auth reuse, normal user journey, restart/replay, cold restore, retrieval, repair, direct compatibility and Codex/OpenCode non-regression evidence for every advertised Claude row.

The historical profile-bound/Codex-first sequencing is superseded by the current generic program's actual dependencies. No optional static topology or completed Codex retirement is automatically a prerequisite to qualifying Claude's on-demand row.

## Disposal condition

Remove this tracker only when every active recovery/remediation gap and every unresolved historical obligation above has an explicit current owner/evidence disposition under #3965. Preserve the roadmap's stable acceptance meanings and real security/replay/artifact tests. Issue closure, a merged validator, a test name, or an unexecuted live row is not completion evidence.

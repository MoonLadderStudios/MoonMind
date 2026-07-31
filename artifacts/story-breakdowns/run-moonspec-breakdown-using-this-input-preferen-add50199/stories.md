# Lore VCS Integration Story Breakdown

- Source: `docs/Workflows/LoreVcsIntegrationDesign.md`
- Source document class: `canonical-declarative`
- Extracted: 2026-07-31T04:50:50.132207+00:00

## Design summary

MoonMind replaces GitHub-coupled repository assumptions with provider-discriminated Git and Lore targets. Lore-backed runs operate on one complete exact-revision workspace, publish through conditional provider-native authority, project review one way into GitHub, key CI to immutable Lore revisions, and delegate protected merges to a Lore coordinator. The design requires atomic contract/evidence cutover, portable pinned tooling, least privilege, replay-safe reconciliation, and explicit runtime conformance while leaving projection, Lore server operations, and protected-branch merge ownership outside MoonMind.

## Coverage points

- **DESIGN-REQ-001 — Provider-discriminated repository contract** (`requirement`, 3.2–3.5): Replace GitHub-coupled authoring with explicit provider, connection, repository, branch, revision, and remote-tip identities.
- **DESIGN-REQ-002 — Connection and readiness compilation** (`integration`, 3.3 and 3.8): Resolve deployment-owned connections, derive provider-aware capabilities, and fail closed before mutation or launch.
- **DESIGN-REQ-003 — Exact complete workspace delivery** (`requirement`, 3.6–3.7): Prepare one exact full Lore workspace and bind that authority into every supported runtime lane.
- **DESIGN-REQ-004 — Cache and checkpoint integrity** (`state-model`, 3.7): Keep mutable state private, verify shared immutable cache objects, and restore checkpoints from revision plus bounded deltas.
- **DESIGN-REQ-005 — Pinned portable tooling** (`integration`, 3.9–3.10): Deliver the pinned real client and resolved provider-aware Skill with verified metadata and host compatibility.
- **DESIGN-REQ-006 — Least-privilege repository operations** (`security`, 3.11 and 3.18): Separate read, write, lock, review, CI, and merge authorities while keeping secrets out of durable and agent-visible state.
- **DESIGN-REQ-007 — Deterministic provider-native publication** (`requirement`, 3.12): Scan, stage, atomically publish, remotely verify, and represent none/no-op behavior truthfully.
- **DESIGN-REQ-008 — Unified publication evidence** (`artifact`, 3.13): Use one validated provider-neutral evidence envelope across managed and agent-owned publication.
- **DESIGN-REQ-009 — Conditional review projection** (`integration`, 3.14–3.15): Request projection against an exact tip and reconcile bounded, closed-vocabulary bridge status to an exact PR head.
- **DESIGN-REQ-010 — Lore-backed review remediation** (`requirement`, 2.2 and 3.15): Read generated GitHub review state but apply and publish every content fix through a new Lore revision.
- **DESIGN-REQ-011 — Exact-revision CI** (`integration`, 3.16): Run CI on the complete immutable Lore revision and gate freshness with exact observed identities.
- **DESIGN-REQ-012 — Provider-authoritative merge automation** (`integration`, 3.17): Compile Git targets to GitHub merge and Lore targets to exact-evidence coordinator submission and bounded reconciliation.
- **DESIGN-REQ-013 — Lock-safe retry and recovery** (`state-model`, 3.11 and 3.19): Make mutations and lock cleanup idempotent and reconcile authority after lost responses or terminal transitions.
- **DESIGN-REQ-014 — Operator evidence and diagnostics** (`observability`, 3.20): Expose stable codes, immutable artifacts, freshness, deadlines, and separate authoritative/projected identities.
- **DESIGN-REQ-015 — Tactics authority compatibility** (`constraint`, 2.3): Preserve complete root Content authority, Content-only revision identity, filtered projection, and external bridge ownership.
- **DESIGN-REQ-016 — Atomic cutover and replay boundary** (`migration`, 3.2): Move all new authoring and evidence surfaces together while retaining only a frozen decoder for durable histories.
- **DESIGN-REQ-017 — Explicit non-goal boundaries** (`non-goal`, 4. Non-goals): Keep projection, server operations, protected merge, authorization, and provider-state parsing outside inappropriate MoonMind boundaries.
- **DESIGN-REQ-018 — Runtime conformance matrix** (`constraint`, 6. Conformance): Prove or reject every advertised runtime × capability × boundary combination with boundary-level cases.

## Canonical claims

- **NON-GOAL-001** (4. Non-goals): MoonMind does not become the Lore-to-Git projection worker.
- **NON-GOAL-002** (4. Non-goals): Projected GitHub content never synchronizes back into Lore.
- **NON-GOAL-003** (4. Non-goals): MoonMind does not replace or bypass the Lore merge coordinator.
- **NON-GOAL-004** (4. Non-goals): Lore server hardening and operations remain deployment responsibilities.
- **NON-GOAL-005** (4. Non-goals): A filtered Git checkout cannot substitute for a complete Lore workspace.
- **NON-GOAL-006** (4. Non-goals): Lore uses standard tool and Skill projection rather than a dedicated Omnigent host.
- **NON-GOAL-007** (4. Non-goals): Durable state is never inferred from human-oriented CLI prose.
- **NON-GOAL-008** (4. Non-goals): Tool installation does not grant repository authority.
- **AUTHORITY-001** (5. Constraints & decisions): Lore repository id and exact revision signature are canonical; branch identity remains separate.
- **AUTHORITY-002** (5. Constraints & decisions): Repository-content changes flow only through Lore; GitHub is a one-way review projection.
- **CONTRACT-001** (5. Constraints & decisions): New workflows use one top-level provider-discriminated repository target.
- **CONTRACT-002** (5. Constraints & decisions): Historical revision selection is explicit and read-only by default.
- **CONTRACT-003** (5. Constraints & decisions): RepositoryConnection selects endpoint, trust, credentials, policy, integrations, and client compatibility.
- **CONTRACT-004** (5. Constraints & decisions): A deployment-seeded default Git connection preserves the low-ceremony GitHub path.
- **CONTRACT-005** (5. Constraints & decisions): Managed and agent-owned publication share moonmind.publish.repository.v1.
- **CONTRACT-006** (5. Constraints & decisions): Publication evidence and default-connection cutover is atomic across producers and consumers.
- **WORKSPACE-001** (5. Constraints & decisions): Lore workspaces reproduce the exact complete revision, including root Content.
- **WORKSPACE-002** (5. Constraints & decisions): Checkpoints reconstruct mutable provider state instead of archiving it.
- **WORKSPACE-003** (5. Constraints & decisions): Only verified immutable objects may be shared; mutable state is run-private.
- **WORKSPACE-004** (5. Constraints & decisions): One sandbox-authority workspace is bound into every supported runtime lane.
- **TOOLING-001** (5. Constraints & decisions): Agents receive the real pinned, checksum-verified Lore client.
- **TOOLING-002** (5. Constraints & decisions): Manifest-driven readiness validates the connection-selected client and fails closed.
- **TOOLING-003** (5. Constraints & decisions): Portable lore-vcs Skill content owns agent procedure while MoonMind supplies substrate.
- **SECURITY-001** (5. Constraints & decisions): Repository operations are split into least-privilege authority boundaries.
- **SECURITY-002** (5. Constraints & decisions): Raw credentials and private trust material never enter durable state.
- **SECURITY-003** (5. Constraints & decisions): Required outbound scanning occurs before remote publication.
- **PUBLISH-001** (5. Constraints & decisions): Publication succeeds only after exact remote revision verification.
- **PUBLISH-002** (5. Constraints & decisions): Branch updates use atomic expected-tip semantics.
- **PUBLISH-003** (5. Constraints & decisions): Review requests are conditional on the exact current Lore revision.
- **PUBLISH-004** (5. Constraints & decisions): Publish mode none emits no publication evidence and maps to PUBLISH_DISABLED.
- **REVIEW-001** (5. Constraints & decisions): PR completion requires an exact current Lore-to-Git/PR mapping.
- **REVIEW-002** (5. Constraints & decisions): Content-only Lore revisions remain publishable, testable, and reviewable.
- **SKILL-001** (5. Constraints & decisions): Repository-mutating Skills declare provider support and evidence schema.
- **CI-001** (5. Constraints & decisions): CI input and freshness are keyed by immutable repository revision.
- **MERGE-001** (5. Constraints & decisions): Protected Lore merge authority belongs only to the external coordinator.
- **MERGE-002** (5. Constraints & decisions): Merge automation compiles by provider and Lore fails before launch without a coordinator.
- **LOCK-001** (5. Constraints & decisions): Locks have typed run ownership and verified terminal reconciliation.
- **QUALITY-001** (5. Constraints & decisions): Orchestration is replay-safe, compact, secret-free, and idempotent.
- **QUALITY-002** (5. Constraints & decisions): Consumers reconcile authoritative state instead of trusting notifications.
- **QUALITY-003** (5. Constraints & decisions): Observability distinguishes authoritative Lore identity from projected Git identity.
- **QUALITY-004** (5. Constraints & decisions): Client/server compatibility and executable identity are explicit.
- **QUALITY-005** (5. Constraints & decisions): Unsupported runtime combinations fail before launch.

## Ordered story candidates

### STORY-001 — Compile provider-aware repository targets and readiness

- Short name: `repository target`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.2 Canonical contract replacement and atomic cutover, 3.3 Repository connections and the default Git path, 3.4 Authored repository target, 3.5 Resolved repository identity, 3.8 Required capabilities and readiness registry
- Canonical claims: AUTHORITY-001, CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004, QUALITY-004
- Coverage: DESIGN-REQ-001, DESIGN-REQ-002
- Depends on: None
- Narrative: As a MoonMind operator or workflow author, I need one explicit Git-or-Lore target to resolve through a policy-owned connection before any execution begins.
- Independent test: Submit representative Git, Lore, exact-read, invalid legacy, invalid mutation-selector, unknown-capability, and unavailable-connection requests and verify normalized identities or stable pre-launch failures.

Acceptance criteria:

- New submissions accept only the provider-discriminated top-level union and persist connectionRef.
- The common Git path injects repository-connection:git-default and invokes the existing GitHub resolver.
- Repository, branch, immutable revision, and remote-tip expectation remain distinct.
- Capability derivation is provider/publish/Skill/Tool aware; unknown tokens fail closed.
- Connection policy and observed client evidence must match before mutation.

Requirements:

- Implement compiler, validation, connection reconciliation, and registry readiness as one coherent boundary.
- Retain only a frozen versioned legacy decoder for already-recorded histories.

### STORY-002 — Prepare and bind exact Lore workspaces

- Short name: `exact workspace`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.6 Repository provider adapter, 3.7 Workspace authority, runtime delivery, cache isolation, and checkpoints, 6. Conformance
- Canonical claims: WORKSPACE-001, WORKSPACE-002, WORKSPACE-003, WORKSPACE-004, NON-GOAL-005, QUALITY-005
- Coverage: DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015, DESIGN-REQ-018
- Depends on: None
- Narrative: As a MoonMind operator or workflow author, I need the complete exact Lore revision, including root Content, to reach managed and Omnigent lanes through one sandbox authority.
- Independent test: Prepare a full-tree Lore fixture, bind the same authority into each supported lane, mutate externally, checkpoint/restore clean and dirty states, corrupt cache content, and verify exact behavior or structured rejection.

Acceptance criteria:

- The adapter prepares the exact revision with root and plugin Content and no second checkout.
- Managed and supported Omnigent lanes bind the same sandbox-authority workspace.
- External changes are scanned before status, checkpoint, or clean conclusions.
- Mutable state, credentials, journals, and locks are excluded from caches and checkpoints.
- Dirty restore reapplies a bounded delta, rechecks containment/symlinks, scans, and restages intended paths.
- Unsupported lane combinations and oversized checkpoints fail before launch or restore.

Requirements:

- Provide provider adapter workspace, binding, cache, checkpoint, and restore boundaries.

### STORY-003 — Deliver pinned Lore tooling and portable provider Skills

- Short name: `lore tooling`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.9 Agent-visible Lore tooling and runtime lanes, 3.10 Lore VCS Agent Skill and provider support metadata, 4. Non-goals, 6. Conformance
- Canonical claims: TOOLING-001, TOOLING-002, TOOLING-003, SKILL-001, NON-GOAL-006, NON-GOAL-007, NON-GOAL-008, QUALITY-004, QUALITY-005
- Coverage: DESIGN-REQ-005, DESIGN-REQ-017, DESIGN-REQ-018
- Depends on: A custom Lore-only Omnigent host.
- Narrative: As a MoonMind operator or workflow author, I need agents to receive the verified Lore client and portable provider guidance without tool presence granting authority.
- Independent test: Resolve a lore-vcs Skill and immutable tool bundle onto compatible hosts, then exercise digest/version/provider mismatches, Git-only Skills, human-only CLI output, and unauthorized operations.

Acceptance criteria:

- The real lore executable is pinned by archive and executable digests and exposed at its ordinary name.
- Connection policy is compared with observed manifest/version evidence and mismatches fail closed.
- Resolved Skill metadata preserves supported providers and evidence schema without precedence privilege inheritance.
- Durable adapter state uses validated structured output or typed APIs, never human prose.
- Tool installation alone grants no read, write, lock, review, CI, or merge authority.
- Runtime routing rejects incompatible hosts before session creation.

Requirements:

- Create the portable lore-vcs Skill and capability-driven bundle preflight.

### STORY-004 — Enforce least-privilege repository security boundaries

- Short name: `repository security`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.3 Repository connections and the default Git path, 3.11 Bounded executable Tools and lock lifecycle, 3.18 Security and credential materialization
- Canonical claims: SECURITY-001, SECURITY-002, NON-GOAL-004, NON-GOAL-008
- Coverage: DESIGN-REQ-006, DESIGN-REQ-017
- Depends on: Lore server hardening, backup, upgrades, or public exposure policy.
- Narrative: As a MoonMind operator or workflow author, I need credentials and high-authority operations to be isolated, late-bound, policy-scoped, and absent from durable evidence.
- Independent test: Exercise read-only, managed publication, isolated auto, lock, review, and merge modes and inspect payloads, histories, artifacts, process exposure, and authorization failures.

Acceptance criteria:

- Read-only and managed publication runs do not receive reusable remote-write credentials.
- Auto mutation is admitted only for an explicitly compatible Skill on an isolated enforcement-capable runtime.
- Merge-coordinator credentials never enter an ordinary agent shell.
- Repository and operation allowlists are checked before preparation and mutation.
- SecretRef material is resolved late and absent from histories, artifacts, prompts, review requests, and generated Git history.
- MoonMind never silently substitutes endpoint, identity, trust, resolver, provider, or runtime.

Requirements:

- Implement typed authority surfaces and safe credential materialization.

### STORY-005 — Publish revisions atomically with unified evidence

- Short name: `verified publish`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.2 Canonical contract replacement and atomic cutover, 3.12 Publish modes and deterministic publication, 3.13 Unified repository publication evidence
- Canonical claims: CONTRACT-005, CONTRACT-006, SECURITY-003, PUBLISH-001, PUBLISH-002, PUBLISH-004, REVIEW-002
- Coverage: DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-015, DESIGN-REQ-016
- Depends on: Projection bridge implementation and protected Lore merge.
- Narrative: As a MoonMind operator or workflow author, I need managed and agent-owned publication to scan, conditionally update, remotely verify, and emit the same immutable evidence.
- Independent test: Publish changed, unchanged, Content-only, scan-blocked, moved-branch, managed, and agent-owned fixtures and validate exact remote state, outcomes, and canonical evidence.

Acceptance criteria:

- Managed branch/pr publication scans external changes, rejects contamination/locks/protected targets, stages intended paths, scans outbound content, and mutates only after approval.
- Branch publication uses exact compare-and-set or an equivalent exclusive lease; widened or unconditional force is prohibited.
- Success and no-op both require exact remote-tip verification.
- moonmind.publish.repository.v1 is the only new-write evidence schema for managed and auto publication.
- Mode none emits no publication evidence and finalizes as PUBLISH_DISABLED.
- Content-only Lore changes remain successful revisions even when the projected Git tree is unchanged.
- All producers, validators, consumers, Skills, and the seeded connection cut over atomically.

Requirements:

- STORY-001
- STORY-002
- STORY-003
- STORY-004

### STORY-006 — Project exact Lore revisions for review and remediation

- Short name: `review projection`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 2.1 Authority model, 2.2 Primary scenarios, 2.3 Compatibility with the Tactics authority and projection contract, 3.14 Conditional review-request handoff, 3.15 Projection status, workflow state, and bounded waiting
- Canonical claims: AUTHORITY-002, PUBLISH-003, REVIEW-001, REVIEW-002, NON-GOAL-001, NON-GOAL-002
- Coverage: DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-017
- Depends on: Implementing the Lore-to-Git bridge or GitHub-to-Lore synchronization.
- Narrative: As a MoonMind operator or workflow author, I need PR publication and review fixes to preserve one-way Lore authority while reconciling an exact generated review projection.
- Independent test: Publish a Lore revision, race the conditional request, reconcile every projection status and deadline, advance the branch, and remediate a generated PR while verifying that all content writes return through Lore.

Acceptance criteria:

- Review requests are idempotent per exact revision and accepted only while the branch tip matches.
- PR success requires active-policy mapping whose Git commit equals the PR head and whose Lore revision equals the current branch tip.
- Pending and mapped-without-PR remain awaiting_external; divergence, failure, invalid status, and hard timeout use stable outcomes.
- Resume reconciles projection without republishing an already verified branch.
- GitHub review data may be read when authorized, but fixes create a new Lore revision and never mutate generated refs.
- The external bridge owns mapping, generated history/refs/PRs, divergence quarantine, and projection filtering.

Requirements:

- STORY-005

### STORY-007 — Gate workflows on exact-revision CI evidence

- Short name: `revision ci`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 2.2 Run exact-revision CI, 3.16 Exact-revision CI request and terminal evidence
- Canonical claims: CI-001, AUTHORITY-001, REVIEW-002
- Coverage: DESIGN-REQ-011, DESIGN-REQ-015
- Depends on: None
- Narrative: As a MoonMind operator or workflow author, I need CI results and projected checks to remain immutable and fresh only for the exact complete repository revision.
- Independent test: Run success, failure, cancellation, invalid-status, branch-advance, Content-only, and optional projected-check cases and compare requested, observed, and live branch-tip revisions.

Acceptance criteria:

- Requests identify provider, connection, repository, branch, immutable revision, and stable idempotency.
- The controller materializes the complete revision including root Content and returns immutable typed evidence.
- Success gates only when requested, observed, and current branch-tip revisions match exactly.
- Branch advance makes old CI evidence stale without mutating its artifact.
- Unknown/blank statuses fail closed; cancellation never satisfies a gate.
- GitHub Check Runs are projections and never become the workspace input.

Requirements:

- STORY-001
- STORY-002

### STORY-008 — Compile merge automation to repository authority

- Short name: `provider merge`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 2.2 Request provider-authoritative merge automation, 3.17 Provider-authoritative merge automation and coordinator evidence, 4. Non-goals
- Canonical claims: MERGE-001, MERGE-002, NON-GOAL-003, AUTHORITY-002
- Coverage: DESIGN-REQ-012, DESIGN-REQ-017
- Depends on: Replacing the coordinator or directly merging protected Lore branches.
- Narrative: As a MoonMind operator or workflow author, I need one operator merge-automation choice to use GitHub authority for Git and coordinator authority for Lore with exact evidence.
- Independent test: Compile Git and Lore targets, omit a Lore coordinator, submit exact Lore evidence, reconcile every coordinator status/deadline/resume path, and attempt forbidden direct merges.

Acceptance criteria:

- Git targets retain Git-provider merge automation; Lore targets never start a GitHub-merging pr-resolver.
- Lore selection without a compatible coordinator fails before launch.
- Merge requests carry exact source/target, CI, projection, approval, policy, requester, and idempotency evidence.
- Pending states remain bounded awaiting_external; merged completes only with exact coordinator evidence.
- Stale/policy rejection, failure, invalid status, and timeout map to stable terminal codes.
- Resume reuses the request id without republishing or duplicating the logical merge.
- A GitHub merge without coordinator-confirmed Lore merge is an authority violation.

Requirements:

- STORY-006
- STORY-007

### STORY-009 — Reconcile locks and repository mutations safely

- Short name: `safe recovery`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.11 Bounded executable Tools and lock lifecycle, 3.19 Concurrency, idempotency, and recovery
- Canonical claims: LOCK-001, QUALITY-001, QUALITY-002, PUBLISH-001, PUBLISH-002
- Coverage: DESIGN-REQ-013
- Depends on: None
- Narrative: As a MoonMind operator or workflow author, I need retries, cancellation, runtime loss, and lost responses to converge without duplicate revisions, stale success, or abandoned run-owned locks.
- Independent test: Inject lost responses, duplicate Activities, branch movement, conflicting foreign locks, cancellation, timeout, runtime loss, and cleanup failure and inspect authoritative remote/lock state.

Acceptance criteria:

- Every mutation uses a stable logical idempotency key.
- Retries reconcile exact remote/request state and never create a revision solely because a response was lost.
- Unexpected branch movement blocks without force repair.
- Acquire, release, and cleanup are typed and idempotent across all terminal and retry boundaries.
- Foreign locks block publication and are never broken automatically.
- Cleanup failure is durable, operator-visible evidence.
- Notifications only wake consumers; exact identifiers and revisions determine progress.

Requirements:

- STORY-002
- STORY-005

### STORY-010 — Expose authoritative repository evidence and diagnostics

- Short name: `repository evidence`
- Source: `docs/Workflows/LoreVcsIntegrationDesign.md` — 3.20 Diagnostics, artifacts, and UI, 6. Conformance
- Canonical claims: QUALITY-003, QUALITY-005, AUTHORITY-001
- Coverage: DESIGN-REQ-014, DESIGN-REQ-018
- Depends on: None
- Narrative: As a MoonMind operator or workflow author, I need operators to distinguish Lore authority from Git projection and act on stable blockers, freshness, and bounded waits.
- Independent test: Render and query representative success, pending, stale, invalid, blocked, cleanup-failed, Content-only, and projected states and validate labels, artifact refs, codes, deadlines, and secret redaction.

Acceptance criteria:

- Artifacts preserve immutable target, readiness, binding, status, checkpoint, lock, scan, publication, projection, CI, and merge evidence.
- Stable diagnostic codes identify each documented blocker/failure boundary.
- UI labels authoritative provider/repository/branch/revision separately from generated Git commit/PR.
- Freshness for projection, CI, review, and merge is shown against the current Lore revision.
- awaiting_external reasons, soft/hard deadlines, and reconcile actions are visible.
- Diagnostics and artifacts are bounded and redacted.
- Only runtime matrix cells with conformance evidence are advertised.

Requirements:

- STORY-001
- STORY-002
- STORY-005
- STORY-006
- STORY-007
- STORY-008
- STORY-009

## Coverage matrix

| Coverage obligation | Owning stories |
|---|---|
| NON-GOAL-001 | STORY-006 |
| NON-GOAL-002 | STORY-006 |
| NON-GOAL-003 | STORY-008 |
| NON-GOAL-004 | STORY-004 |
| NON-GOAL-005 | STORY-002 |
| NON-GOAL-006 | STORY-003 |
| NON-GOAL-007 | STORY-003 |
| NON-GOAL-008 | STORY-003, STORY-004 |
| AUTHORITY-001 | STORY-001, STORY-007, STORY-010 |
| AUTHORITY-002 | STORY-006, STORY-008 |
| CONTRACT-001 | STORY-001 |
| CONTRACT-002 | STORY-001 |
| CONTRACT-003 | STORY-001 |
| CONTRACT-004 | STORY-001 |
| CONTRACT-005 | STORY-005 |
| CONTRACT-006 | STORY-005 |
| WORKSPACE-001 | STORY-002 |
| WORKSPACE-002 | STORY-002 |
| WORKSPACE-003 | STORY-002 |
| WORKSPACE-004 | STORY-002 |
| TOOLING-001 | STORY-003 |
| TOOLING-002 | STORY-003 |
| TOOLING-003 | STORY-003 |
| SECURITY-001 | STORY-004 |
| SECURITY-002 | STORY-004 |
| SECURITY-003 | STORY-005 |
| PUBLISH-001 | STORY-005, STORY-009 |
| PUBLISH-002 | STORY-005, STORY-009 |
| PUBLISH-003 | STORY-006 |
| PUBLISH-004 | STORY-005 |
| REVIEW-001 | STORY-006 |
| REVIEW-002 | STORY-005, STORY-006, STORY-007 |
| SKILL-001 | STORY-003 |
| CI-001 | STORY-007 |
| MERGE-001 | STORY-008 |
| MERGE-002 | STORY-008 |
| LOCK-001 | STORY-009 |
| QUALITY-001 | STORY-009 |
| QUALITY-002 | STORY-009 |
| QUALITY-003 | STORY-010 |
| QUALITY-004 | STORY-001, STORY-003 |
| QUALITY-005 | STORY-002, STORY-003, STORY-010 |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001 |
| DESIGN-REQ-003 | STORY-002 |
| DESIGN-REQ-004 | STORY-002 |
| DESIGN-REQ-005 | STORY-003 |
| DESIGN-REQ-006 | STORY-004 |
| DESIGN-REQ-007 | STORY-005 |
| DESIGN-REQ-008 | STORY-005 |
| DESIGN-REQ-009 | STORY-006 |
| DESIGN-REQ-010 | STORY-006 |
| DESIGN-REQ-011 | STORY-007 |
| DESIGN-REQ-012 | STORY-008 |
| DESIGN-REQ-013 | STORY-009 |
| DESIGN-REQ-014 | STORY-010 |
| DESIGN-REQ-015 | STORY-002, STORY-005, STORY-006, STORY-007 |
| DESIGN-REQ-016 | STORY-005 |
| DESIGN-REQ-017 | STORY-003, STORY-004, STORY-006, STORY-008 |
| DESIGN-REQ-018 | STORY-002, STORY-003, STORY-010 |

## Dependencies

- STORY-001: No story dependency.
- STORY-002: No story dependency.
- STORY-003: A custom Lore-only Omnigent host.
- STORY-004: Lore server hardening, backup, upgrades, or public exposure policy.
- STORY-005: Projection bridge implementation and protected Lore merge.
- STORY-006: Implementing the Lore-to-Git bridge or GitHub-to-Lore synchronization.
- STORY-007: No story dependency.
- STORY-008: Replacing the coordinator or directly merging protected Lore branches.
- STORY-009: No story dependency.
- STORY-010: No story dependency.

## Out-of-scope boundaries

- MoonMind does not implement or operate the external Lore-to-Git projection bridge.
- MoonMind does not create GitHub-to-Lore content synchronization.
- MoonMind does not replace the protected-branch Lore merge coordinator.
- MoonMind does not harden or operate the Lore server.
- MoonMind does not use a filtered Git clone or a dedicated Lore-only Omnigent host as the canonical workspace path.
- Tool presence and human CLI prose are not authorization or durable state evidence.

## Coverage gate

PASS - every major design point is owned by at least one story.

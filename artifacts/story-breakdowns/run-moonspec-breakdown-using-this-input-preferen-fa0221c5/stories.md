# Checkpoint Branch System - Story Breakdown

- Source: `docs/Workflows/CheckpointBranchSystem.md`
- Source title: Checkpoint Branch System
- Source document class: `canonical-declarative`
- Extracted at: `2026-07-02T00:26:37Z`
- Coverage gate: PASS - every major design point is owned by at least one story.
- Output mode: `jira`

## Design Summary

The Checkpoint Branch System defines a product-level continuation graph above Step Executions. It lets operators, workflows, or policy fork durable checkpoints into named branch lanes, execute immutable branch turns with explicit workspace/runtime policy, isolate repository work, compare candidates, and promote at most one branch back into canonical workflow progress. The design emphasizes MoonMind-owned authority over provider bindings, append-only artifact evidence, fail-closed safety, idempotent APIs, UI affordances, and phased rollout from manual branch graph/runtime support through compare/promote, provider continuation, and policy-driven exploration.

## Coverage Points

- `DESIGN-REQ-001` (requirement): Checkpoint branch primitive - Create independent named continuations from eligible checkpoints.
- `DESIGN-REQ-002` (artifact): Durable branch evidence - Preserve append-only evidence through checkpoints, Step Execution manifests, artifacts, diagnostics, comparisons, and promotion records.
- `DESIGN-REQ-003` (constraint): Repository isolation - Repository-mutating branches use isolated git branches, worktrees, or provider workspace bindings.
- `DESIGN-REQ-004` (security): Immutable auditable instructions - Store branch-turn instructions by artifact ref and digest and prohibit mutation after launch.
- `DESIGN-REQ-005` (requirement): Repeated branch turns - Allow multiple turns on a branch while preserving lineage.
- `DESIGN-REQ-006` (requirement): Compare and promote branches - Compare candidates through artifacts and explicitly promote zero or one.
- `DESIGN-REQ-007` (integration): Typed runtime policies - Represent runtime/provider semantics with declared policies.
- `DESIGN-REQ-008` (constraint): No silent canonical advancement - Exploration, publication, and completion do not silently advance canonical workflow state.
- `DESIGN-REQ-009` (state-model): Product graph above executions - Model branches above logical steps, Step Executions, checkpoints, turns, and child branches.
- `DESIGN-REQ-010` (state-model): Product/git identity separation - Keep product branch and git work branch identities distinct.
- `DESIGN-REQ-011` (requirement): Branch turn semantic execution - Branch turns are new semantic executions, not retries.
- `DESIGN-REQ-012` (constraint): MoonMind authority over provider ids - MoonMind-owned records and artifacts are authoritative; provider ids are bindings.
- `DESIGN-REQ-013` (state-model): Fan-out/fan-in model - Multiple branches can fork from one checkpoint; combining results requires promotion or explicit evidence input.
- `DESIGN-REQ-014` (state-model): Lifecycle states and operations - Expose explicit states and canonical idempotent operations.
- `DESIGN-REQ-015` (requirement): Branch data records - Persist branch records, turn records, git bindings, artifact associations, and manifest branch metadata.
- `DESIGN-REQ-016` (integration): API contracts - Provide REST APIs for discovery, create, continue, fork, compare, promote, and archive.
- `DESIGN-REQ-017` (constraint): Workspace policy recording - Record selected workspace policy in branch metadata, turn metadata, manifests, and diagnostics.
- `DESIGN-REQ-018` (security): Safe git branch naming - Generate deterministic, sanitized, protected-ref-safe, collision-checked work branch names.
- `DESIGN-REQ-019` (requirement): Runtime session policy defaults - Use explicit runtime context policies and safe defaults for create, continue, fork, same conversation, and Omnigent.
- `DESIGN-REQ-020` (artifact): Compact context bundle - Provide immutable digest-addressed context bundles with refs, bounded summaries, lineage, policies, source checkpoint identity, and builder metadata.
- `DESIGN-REQ-021` (security): Context payload exclusions - Never inline raw logs, raw diffs, provider payloads, or credentials in compact context.
- `DESIGN-REQ-022` (integration): Omnigent v1 fresh branch mode - Create fresh Omnigent sessions from MoonMind evidence using instruction refs and new idempotency keys.
- `DESIGN-REQ-023` (integration): Omnigent v2 gated continuation - Require typed lifecycle activities and capability gates for same-session provider continuation.
- `DESIGN-REQ-024` (artifact): Artifact-backed comparison - Persist diff refs, quality verdicts, diagnostics, and bounded summaries for comparisons.
- `DESIGN-REQ-025` (security): Security fail-closed behavior - Fail closed for checkpoint, auth, digest, policy, protected ref, provider, approval, and budget failures.
- `DESIGN-REQ-026` (requirement): Branch Explorer UI - Provide Branch Explorer, branch actions, mainline affordances, and safety previews.
- `DESIGN-REQ-027` (artifact): Minimum artifact inventory - Emit minimum named artifacts for branches, turns, promotions, and comparisons.
- `DESIGN-REQ-028` (migration): Phased rollout boundaries - Separate manual branch graph/fresh turns, compare/promote, provider continuation, and policy exploration phases.
- `DESIGN-REQ-029` (requirement): Required test coverage - Cover schema, checkpoint validation, git isolation, runtime, promotion, and Omnigent contracts.
- `DESIGN-REQ-030` (constraint): Open product choices - Preserve unresolved choices for downstream clarification.

## Story Candidates

### STORY-001 - Persist checkpoint branch graph and lifecycle state

Short name: `branch-graph`

As a workflow operator, I need MoonMind to persist explicit Checkpoint Branch and Branch Turn records above Step Executions so every continuation lane has durable lineage, lifecycle state, and source checkpoint identity.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 1. Purpose, 2.1 Add a product-level branch graph above Step Executions, 3. Conceptual model, 5. Core invariants, 6. Branch lifecycle, 7. Data model, 20. Desired end state
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#1-purpose-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#1-purpose-claim-002`, `docs/Workflows/CheckpointBranchSystem.md#21-add-a-product-level-branch-graph-above-step-executions-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#3-conceptual-model-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#5-core-invariants-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#6-branch-lifecycle-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#7-data-model-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#20-desired-end-state-claim-001`
Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-005`, `DESIGN-REQ-009`, `DESIGN-REQ-013`, `DESIGN-REQ-014`, `DESIGN-REQ-015`

Why: This is the backbone for every downstream branch operation.

Scope:
- Branch and turn persistence records
- Root checkpoint and parent lineage fields
- Lifecycle states and canonical operation names
- Step Execution manifest branch metadata extension

Out of scope:
- Running agent work
- Git workspace materialization
- Dashboard rendering

Independent test: Create branches and turns against fixture checkpoints and assert records, state transitions, lineage, source checkpoint identity, and manifest metadata.

Acceptance criteria:
- A branch cannot be created without a valid source checkpoint ref or typed source state ref.
- Records preserve workflow, run, logical step, source execution ordinal, checkpoint boundary, ref, and digest when available.
- Branch and turn states match the canonical lifecycle vocabulary.
- Branch records remain distinct from Step Execution identities.
- Child branches preserve parent branch and parent turn lineage.

Requirements:
- Persist branch, turn, artifact, git binding, and manifest branch metadata records.
- Represent fan-out and child forks.
- Keep failed, archived, superseded, and unpromoted evidence append-only.

Dependencies: None

Assumptions:
- Existing Step Execution persistence can be extended with optional branch metadata.

Needs clarification:
- [NEEDS CLARIFICATION] Whether branch creation always creates a linked follow-up Workflow Execution or can run inside the same logical Workflow Execution.

### STORY-002 - Launch immutable branch turns as new Step Execution evidence

Short name: `branch-turns`

As a workflow runtime, I need each branch turn to execute as a new semantic unit with immutable instruction refs, declared policies, compact context, and Step Execution evidence.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 2.3 Branching is not ordinary retry, 5. Core invariants, 10. Runtime/session policy, 11. Context bundle, 16. Artifact requirements
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#23-branching-is-not-ordinary-retry-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#5-core-invariants-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#10-runtime-session-policy-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#11-context-bundle-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#16-artifact-requirements-claim-001`
Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-007`, `DESIGN-REQ-011`, `DESIGN-REQ-019`, `DESIGN-REQ-020`, `DESIGN-REQ-021`, `DESIGN-REQ-027`

Why: Branch work must not be represented as retries or mutable prompts.

Scope:
- Instruction artifact refs and digests
- Runtime policy declaration
- Immutable context bundle
- New Step Execution or typed continuation evidence
- Minimum branch-turn artifacts

Out of scope:
- Provider-specific continuation internals
- Promotion and comparison decisions

Independent test: Launch a branch turn and verify new evidence, immutable digest, context bundle ref, required artifacts, and compact-state rejection for raw/sensitive payloads.

Acceptance criteria:
- Agent/tool work creates a new Step Execution identity or typed continuation evidence.
- Instruction text is artifact-backed with digest before launch and immutable afterward.
- Workspace and runtime policies are declared before launch and copied to metadata, manifest, and diagnostics.
- Context bundle includes refs, bounded summaries, source checkpoint identity, lineage, instructions, baseline, and builder metadata.
- Context validation rejects raw logs, raw diffs, provider payloads, and credentials.

Requirements:
- Differentiate branch turns from Activity retry.
- Support runtime context policies from the design.
- Emit minimum branch-turn artifacts.

Dependencies: STORY-001

Assumptions:
- Artifact storage provides digest-addressed refs.

### STORY-003 - Isolate branch workspaces with safe git bindings

Short name: `git-isolation`

As a repository maintainer, I need code-changing branches to run in isolated git branches or worktrees with deterministic protected-ref-safe naming.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 2.2 Separate product branches from git branches, 5. Core invariants, 9. Workspace and git policy, 14. Security and safety, 18.3 Git isolation tests
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#22-separate-product-branches-from-git-branches-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#5-core-invariants-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#9-workspace-and-git-policy-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#14-security-and-safety-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#18-testing-requirements-claim-001`
Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-010`, `DESIGN-REQ-017`, `DESIGN-REQ-018`, `DESIGN-REQ-025`, `DESIGN-REQ-029`

Why: Repository mutation requires explicit isolation and fail-closed validation.

Scope:
- Git binding records
- Workspace creation modes and policies
- Deterministic sanitized branch names
- Protected-ref and collision checks
- Workspace restoration diagnostics

Out of scope:
- Provider session continuation
- Pull request creation beyond metadata

Independent test: Use fixture repos to assert branch/worktree isolation, deterministic names, protected-ref refusal, idempotent reuse, and mismatched collision failure.

Acceptance criteria:
- Repository-mutating branches require distinct git branch, worktree, or provider workspace binding.
- Product branch id and git work branch name are stored separately.
- Workspace policy is recorded in all required branch, turn, manifest, and diagnostics places.
- Generated names are deterministic and sanitized.
- Protected refs, detached heads, unknown refs, and mismatched collisions fail closed.

Requirements:
- Support the workspace modes and policies from the design.
- Emit workspace_restore and git_binding artifacts.

Dependencies: STORY-001

Assumptions:
- Fixture repositories can cover behavior without remote pushes.

Needs clarification:
- [NEEDS CLARIFICATION] Whether archived branch git refs are deleted, retained, or left to repository retention policy.

### STORY-004 - Expose idempotent checkpoint branch APIs

Short name: `branch-apis`

As a dashboard or workflow client, I need typed idempotent APIs to discover, create, continue, fork, compare, promote, publish, and archive branches.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 6.2 Operations, 8. API surface, 9.4 Publish vs promote, 14. Security and safety
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#24-branches-are-candidates-until-promoted-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#6-branch-lifecycle-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#8-api-surface-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#14-security-and-safety-claim-001`
Coverage IDs: `DESIGN-REQ-006`, `DESIGN-REQ-008`, `DESIGN-REQ-014`, `DESIGN-REQ-016`, `DESIGN-REQ-025`

Why: Stable API contracts are required for UI and automated policy clients.

Scope:
- Discovery routes
- Create/continue/fork/compare/promote/archive/publish operation wiring
- Request validation
- Publication/promotion separation

Out of scope:
- Rich Branch Explorer rendering
- Provider execution internals

Independent test: Exercise route contracts with valid and invalid requests and assert idempotency, typed failures, no implicit promotion, expected-head checks, and non-destructive archive.

Acceptance criteria:
- Clients can list checkpoints, branches, details, and turns.
- Side-effecting operations accept idempotency keys.
- Publishing does not promote.
- Promotion requires expected head, gate evidence, side-effect disposition, and approval evidence when policy requires it.
- Archive hides active work without deleting evidence.

Requirements:
- Expose source API paths.
- Fail closed for invalid source, auth, digest, policy, protected ref, provider, approval, and budget cases.
- Use typed payloads rather than inferred logs or branch names.

Dependencies: STORY-001, STORY-003

Assumptions:
- Gate verdicts are available as step evidence artifacts.

Needs clarification:
- [NEEDS CLARIFICATION] Whether promotion updates mainline through Continue-As-New, workflow update, or linked accepted-branch relation.

### STORY-005 - Support Omnigent branch runtime modes safely

Short name: `omnigent-branches`

As a workflow integrator, I need Omnigent branch turns to use fresh sessions for v1 and capability-gated provider continuation for v2.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 2.5 Provider sessions are runtime bindings, not branch authority, 10. Runtime/session policy, 12. Omnigent integration, 14. Security and safety, 18.6 Omnigent tests
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#25-provider-sessions-are-runtime-bindings,-not-branch-authority-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#10-runtime-session-policy-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#12-omnigent-integration-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#14-security-and-safety-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#18-testing-requirements-claim-001`
Coverage IDs: `DESIGN-REQ-007`, `DESIGN-REQ-012`, `DESIGN-REQ-019`, `DESIGN-REQ-022`, `DESIGN-REQ-023`, `DESIGN-REQ-025`, `DESIGN-REQ-029`

Why: Omnigent has explicit v1 and v2 branch semantics in the design.

Scope:
- Omnigent v1 fresh session flow
- instructionRef payload shape
- Prior capture refs as evidence
- Branch-turn idempotency key
- Capability gate for lifecycle continuation

Out of scope:
- General branch graph persistence
- Non-Omnigent adapters unless sharing the same contract

Independent test: Adapter-boundary tests verify fresh v1 sessions, instructionRef payloads, prior refs as evidence, distinct idempotency, and gated same-session continuation.

Acceptance criteria:
- Omnigent v1 validates checkpoint and restores/synthesizes isolated workspace before creating a new session.
- Requests use parameters.omnigent.prompt.instructionRef.
- Prior captures are evidence refs, not branch identity.
- Idempotency key includes workflow id, branch id, and branch turn id.
- Same-session continuation is rejected unless lifecycle activities and capability gates are enabled.

Requirements:
- Capture Omnigent output into artifacts.
- Bind Omnigent results to branch-turn evidence.
- Treat provider ids as diagnostics only.

Dependencies: STORY-002, STORY-003

Assumptions:
- Adapter capability discovery can be exposed through the existing boundary.

### STORY-006 - Compare, promote, publish, and archive branch evidence

Short name: `branch-outcomes`

As a workflow owner, I need durable comparison, promotion, publication, and archival records so I can evaluate candidates and explicitly accept one result.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 2.4 Branches are candidates until promoted, 3.3 Branch fan-out and fan-in, 6.3 Promotion, 9.4 Publish vs promote, 13. Branch comparison, 16. Artifact requirements, 18.5 Promotion tests
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#24-branches-are-candidates-until-promoted-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#3-conceptual-model-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#13-branch-comparison-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#16-artifact-requirements-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#18-testing-requirements-claim-001`
Coverage IDs: `DESIGN-REQ-006`, `DESIGN-REQ-008`, `DESIGN-REQ-013`, `DESIGN-REQ-024`, `DESIGN-REQ-027`, `DESIGN-REQ-029`, `DESIGN-REQ-030`

Why: Branch exploration needs evidence-backed fan-in without destroying alternatives.

Scope:
- Comparison artifacts
- Promotion records
- Publication status metadata
- Archive records
- Non-deletion of competing branches

Out of scope:
- Branch execution launch mechanics
- Dashboard visualization beyond records

Independent test: Create two fixture branches, compare them, publish one, promote it with matching expected head, and assert artifacts, records, invalidations, metadata, and surviving alternatives.

Acceptance criteria:
- Comparison produces durable artifact-backed records with branch ids, base checkpoint, diff refs, quality verdicts, diagnostics, and summary refs.
- Promotion records branch, turn, Step Execution, accepted output, git/PR, gate, side-effect, invalidation, approval, and policy evidence.
- Promotion requires fresh head validation and passed gates.
- Publication and promotion remain separate.
- Promotion does not delete competing branches; archive is non-destructive.

Requirements:
- Emit comparison and promotion artifacts from the design.
- Preserve open product choices around async comparison and no-code diagnosis.

Dependencies: STORY-004

Assumptions:
- Range diff and gate artifacts may be asynchronous for large branches.

Needs clarification:
- [NEEDS CLARIFICATION] Whether branch comparison should be synchronous for small diffs and asynchronous for large diffs.
- [NEEDS CLARIFICATION] Whether branch promotion should support accepted no-code diagnosis as a first-class output.

### STORY-007 - Render Branch Explorer and safety previews

Short name: `branch-ui`

As a dashboard user, I need a Branch Explorer, branch actions, mainline affordances, evidence links, and pre-action safety previews.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 15. UI requirements, 19. Open questions
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#15-ui-requirements-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#19-open-questions-claim-001`
Coverage IDs: `DESIGN-REQ-026`, `DESIGN-REQ-030`

Why: The branch graph must be visible and operable without overwhelming workflow detail.

Scope:
- Branch Explorer tree
- Mainline-focused workflow detail affordances
- Branch action controls
- Evidence/diff/provider diagnostics links
- Creation and promotion previews

Out of scope:
- Backend execution semantics
- Rich comparison UI beyond bounded summaries if not available

Independent test: UI tests with mocked branch APIs verify tree rendering, mainline defaults, action availability, preview fields, and dense branch behavior.

Acceptance criteria:
- Workflow detail defaults to mainline and shows branch count/status near checkpoints and failed/blocked steps.
- Branch Explorer shows hierarchy, states, and evidence links.
- Users can initiate branch actions when policy allows.
- Creation preview shows checkpoint, workspace policy, git branch, runtime policy, publish mode, side-effect risk, budget impact, and approvals.
- Promotion preview shows head, gate, git/PR, invalidations, side effects, approvals, and competing branches.

Requirements:
- Keep default detail simple.
- Support many branches without log/git inference.

Dependencies: STORY-004, STORY-006

Assumptions:
- Backend APIs provide bounded summaries and artifact refs.

Needs clarification:
- [NEEDS CLARIFICATION] How the UI should display many branches from one checkpoint without overwhelming the main workflow detail page.

### STORY-008 - Gate automated branch exploration by policy

Short name: `branch-policy`

As a platform operator, I need workflow templates or presets to request bounded automated branch exploration only under explicit policy.

Source reference: `docs/Workflows/CheckpointBranchSystem.md`; sections: 14. Security and safety, 17.4 Phase 4 - Policy-driven branch exploration, 19. Open questions
Canonical claim IDs: `docs/Workflows/CheckpointBranchSystem.md#14-security-and-safety-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#17-migration-and-rollout-claim-001`, `docs/Workflows/CheckpointBranchSystem.md#19-open-questions-claim-001`
Coverage IDs: `DESIGN-REQ-025`, `DESIGN-REQ-028`, `DESIGN-REQ-030`

Why: Policy-driven exploration is a later phase and must be bounded before automation creates branches.

Scope:
- checkpointBranching configuration
- Trigger filtering
- Branch and turn caps
- Approval-gated promotion
- Default workspace policy and branch template refs

Out of scope:
- Phase 1 manual branch creation
- Provider continuation implementation beyond existing gates

Independent test: Preset fixtures assert trigger filtering, caps, approval-gated promotion, instruction refs, workspace defaults, and budget/approval fail-closed behavior.

Acceptance criteria:
- Automated exploration is disabled unless explicitly enabled in authorized template or preset.
- Triggers are limited to configured values.
- maxBranchesPerCheckpoint and maxTurnsPerBranch are enforced.
- Promotion remains approval-gated unless policy explicitly allows another safe mode.
- Budget, side-effect, unsupported provider, and approval failures fail closed.

Requirements:
- Record policy-created branches the same as operator-created branches.
- Preserve rollout boundaries until selected.

Dependencies: STORY-004, STORY-006

Assumptions:
- Workflow presets are the durable policy location after manual phases land.

Needs clarification:
- [NEEDS CLARIFICATION] Which workflows may permit auto-generated branch exploration without user approval?

## Coverage Matrix

- `docs/Workflows/CheckpointBranchSystem.md#1-purpose-claim-001` -> STORY-001
- `docs/Workflows/CheckpointBranchSystem.md#1-purpose-claim-002` -> STORY-001
- `docs/Workflows/CheckpointBranchSystem.md#21-add-a-product-level-branch-graph-above-step-executions-claim-001` -> STORY-001
- `docs/Workflows/CheckpointBranchSystem.md#22-separate-product-branches-from-git-branches-claim-001` -> STORY-003
- `docs/Workflows/CheckpointBranchSystem.md#23-branching-is-not-ordinary-retry-claim-001` -> STORY-002
- `docs/Workflows/CheckpointBranchSystem.md#24-branches-are-candidates-until-promoted-claim-001` -> STORY-004, STORY-006
- `docs/Workflows/CheckpointBranchSystem.md#25-provider-sessions-are-runtime-bindings,-not-branch-authority-claim-001` -> STORY-005
- `docs/Workflows/CheckpointBranchSystem.md#3-conceptual-model-claim-001` -> STORY-001, STORY-006
- `docs/Workflows/CheckpointBranchSystem.md#5-core-invariants-claim-001` -> STORY-001, STORY-002, STORY-003
- `docs/Workflows/CheckpointBranchSystem.md#6-branch-lifecycle-claim-001` -> STORY-001, STORY-004
- `docs/Workflows/CheckpointBranchSystem.md#7-data-model-claim-001` -> STORY-001
- `docs/Workflows/CheckpointBranchSystem.md#8-api-surface-claim-001` -> STORY-004
- `docs/Workflows/CheckpointBranchSystem.md#9-workspace-and-git-policy-claim-001` -> STORY-003
- `docs/Workflows/CheckpointBranchSystem.md#10-runtime-session-policy-claim-001` -> STORY-002, STORY-005
- `docs/Workflows/CheckpointBranchSystem.md#11-context-bundle-claim-001` -> STORY-002
- `docs/Workflows/CheckpointBranchSystem.md#12-omnigent-integration-claim-001` -> STORY-005
- `docs/Workflows/CheckpointBranchSystem.md#13-branch-comparison-claim-001` -> STORY-006
- `docs/Workflows/CheckpointBranchSystem.md#14-security-and-safety-claim-001` -> STORY-003, STORY-004, STORY-005, STORY-008
- `docs/Workflows/CheckpointBranchSystem.md#15-ui-requirements-claim-001` -> STORY-007
- `docs/Workflows/CheckpointBranchSystem.md#16-artifact-requirements-claim-001` -> STORY-002, STORY-006
- `docs/Workflows/CheckpointBranchSystem.md#17-migration-and-rollout-claim-001` -> STORY-008
- `docs/Workflows/CheckpointBranchSystem.md#18-testing-requirements-claim-001` -> STORY-003, STORY-005, STORY-006
- `docs/Workflows/CheckpointBranchSystem.md#19-open-questions-claim-001` -> STORY-007, STORY-008
- `docs/Workflows/CheckpointBranchSystem.md#20-desired-end-state-claim-001` -> STORY-001
- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-003
- `DESIGN-REQ-004` -> STORY-002
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-004, STORY-006
- `DESIGN-REQ-007` -> STORY-002, STORY-005
- `DESIGN-REQ-008` -> STORY-004, STORY-006
- `DESIGN-REQ-009` -> STORY-001
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-002
- `DESIGN-REQ-012` -> STORY-005
- `DESIGN-REQ-013` -> STORY-001, STORY-006
- `DESIGN-REQ-014` -> STORY-001, STORY-004
- `DESIGN-REQ-015` -> STORY-001
- `DESIGN-REQ-016` -> STORY-004
- `DESIGN-REQ-017` -> STORY-003
- `DESIGN-REQ-018` -> STORY-003
- `DESIGN-REQ-019` -> STORY-002, STORY-005
- `DESIGN-REQ-020` -> STORY-002
- `DESIGN-REQ-021` -> STORY-002
- `DESIGN-REQ-022` -> STORY-005
- `DESIGN-REQ-023` -> STORY-005
- `DESIGN-REQ-024` -> STORY-006
- `DESIGN-REQ-025` -> STORY-003, STORY-004, STORY-005, STORY-008
- `DESIGN-REQ-026` -> STORY-007
- `DESIGN-REQ-027` -> STORY-002, STORY-006
- `DESIGN-REQ-028` -> STORY-008
- `DESIGN-REQ-029` -> STORY-003, STORY-005, STORY-006
- `DESIGN-REQ-030` -> STORY-006, STORY-007, STORY-008

## Dependencies

- `STORY-001` depends on no prior stories
- `STORY-002` depends on STORY-001
- `STORY-003` depends on STORY-001
- `STORY-004` depends on STORY-001, STORY-003
- `STORY-005` depends on STORY-002, STORY-003
- `STORY-006` depends on STORY-004
- `STORY-007` depends on STORY-004, STORY-006
- `STORY-008` depends on STORY-004, STORY-006

## Out of Scope

- Creating spec.md files or specs/ directories: Breakdown only writes temporary derived story artifacts.
- Implementing code from the design: This step only decomposes the canonical design into story candidates.
- Treating rollout phases as story boundaries by default: Stories are split by independently valuable outcomes, not phase checklist items.

## Coverage Gate

PASS - every major design point is owned by at least one story.

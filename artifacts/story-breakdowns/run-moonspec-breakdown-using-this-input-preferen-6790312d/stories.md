# MoonSpec Story Breakdown: Checkpoint Branch System

- Source: `docs/Workflows/CheckpointBranchSystem.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-1087`
- Output mode: `jira`
- Extracted at: `2026-07-02T10:54:29Z`
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The Checkpoint Branch System defines a durable product-level branch graph above Step Executions, allowing operators, workflows, and policy to create isolated continuations from validated checkpoints. Branch turns execute with immutable instruction artifacts, explicit workspace and runtime policies, artifact-backed evidence, and optional git/provider bindings, while publication and promotion remain separate. The system emphasizes fail-closed safety, bounded artifact refs, dashboard branch exploration, and phased delivery of comparison, promotion, provider continuation, and policy-driven exploration.

## Stories

### STORY-001: Persist the checkpoint branch graph and lifecycle

- Short name: `branch-graph`
- Source sections: 1. Purpose, 2.1 Add a product-level branch graph above Step Executions, 3. Conceptual model, 4. Terminology, 5. Core invariants, 6. Branch lifecycle, 7. Data model, 20. Desired end state
- Claim IDs: `docs-workflows-checkpoint-branch-system#s1-purpose`, `docs-workflows-checkpoint-branch-system#s1-goals`, `docs-workflows-checkpoint-branch-system#s2-1-graph`, `docs-workflows-checkpoint-branch-system#s3-model`, `docs-workflows-checkpoint-branch-system#s4-terms`, `docs-workflows-checkpoint-branch-system#s5-invariants`, `docs-workflows-checkpoint-branch-system#s6-lifecycle`, `docs-workflows-checkpoint-branch-system#s7-data`, `docs-workflows-checkpoint-branch-system#s20-end`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-007`, `DESIGN-REQ-008`, `DESIGN-REQ-021`
- Dependencies: None

**Description**

As a workflow operator, I can create and inspect Checkpoint Branch records and Branch Turn lineage from eligible checkpoints so that candidate continuations are explicit, durable, and distinct from retries.

**Independent Test**

Create a branch from a validated checkpoint through the service boundary and assert the persisted branch, turn, state, source checkpoint identity, lineage, and Step Execution relationship are discoverable without relying on logs or git branch names.

**Acceptance Criteria**

- Branch creation requires a source checkpoint ref and records workflow id, run id, checkpoint boundary, checkpoint ref, and checkpoint digest when available.
- Branch and turn states match the documented lifecycle and expose create, continue, fork, archive, and publish-ready state transitions without treating retries as branches.
- Branch evidence is append-only; failed, archived, superseded, and unpromoted branches remain queryable.
- Terminology-sensitive operations distinguish retry, step re-execution, recovery, checkpoint branch, branch turn, promotion, and publication.

**Requirements**

- Implement persistent branch, branch turn, branch artifact, and manifest branch metadata models.
- Ensure every branch has a root checkpoint and pinned source identity.
- Represent branch fan-out and child branch relationships without altering the Step Execution identity tuple.

**Source Design Coverage**

- `DESIGN-REQ-001`: Owns the base branch primitive.
- `DESIGN-REQ-002`: Owns the durable branch graph.
- `DESIGN-REQ-007`: Owns lifecycle states and core branch operations.
- `DESIGN-REQ-008`: Owns branch persistence and manifest metadata.
- `DESIGN-REQ-021`: Provides the structural end state for later stories.

**Assumptions**

- Existing Step Execution checkpoints expose enough identity to validate source checkpoint refs.

**Needs Clarification**

- Whether branch creation always creates a linked follow-up Workflow Execution or can run inside the same logical Workflow Execution.

### STORY-002: Launch branch turns with immutable instructions and context bundles

- Short name: `branch-turns`
- Source sections: 2.3 Branching is not ordinary retry, 6. Branch lifecycle, 7.5 Step Execution manifest extension, 10. Runtime/session policy, 11. Context bundle, 16. Artifact requirements
- Claim IDs: `docs-workflows-checkpoint-branch-system#s2-3-turns`, `docs-workflows-checkpoint-branch-system#s6-lifecycle`, `docs-workflows-checkpoint-branch-system#s7-data`, `docs-workflows-checkpoint-branch-system#s10-runtime`, `docs-workflows-checkpoint-branch-system#s11-context`, `docs-workflows-checkpoint-branch-system#s16-artifacts`, `docs-workflows-checkpoint-branch-system#s20-end`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-017`
- Dependencies: `STORY-001`

**Description**

As a workflow runtime, I can launch branch turns as new semantic executions with immutable instruction artifacts, runtime policy, workspace policy, context bundle refs, and Step Execution evidence.

**Independent Test**

Continue an existing branch and verify a new Branch Turn, instruction artifact digest, context bundle artifact, runtime idempotency key, Step Execution manifest branch block, and terminal turn evidence are produced.

**Acceptance Criteria**

- Branch turns cannot mutate instruction artifact, digest, source checkpoint, workspace policy, or runtime policy after launch.
- Agent/tool branch work creates a new Step Execution identity or references a typed runtime continuation operation recorded as Step Execution evidence.
- Context bundles include refs and bounded summaries only and omit raw logs, raw diffs, provider payloads, and credentials.
- Runtime idempotency keys include branch and branch turn identity.

**Requirements**

- Store branch-turn instruction refs and digests.
- Build immutable context bundles with source checkpoint identity, branch lineage, workspace baseline, instruction refs, prior evidence refs, and builder metadata.
- Write minimum branch-turn artifacts and Step Execution manifest branch metadata.

**Source Design Coverage**

- `DESIGN-REQ-004`: Owns immutable branch-turn execution evidence.
- `DESIGN-REQ-011`: Owns runtime policy declaration.
- `DESIGN-REQ-012`: Owns compact context bundle generation.
- `DESIGN-REQ-017`: Owns branch-turn artifacts.

**Assumptions**

- Artifact storage already supports digest-addressed instruction and context artifacts.

### STORY-003: Isolate repository-changing branches with safe git bindings

- Short name: `git-isolation`
- Source sections: 2.2 Separate product branches from git branches, 7.3 workflow_checkpoint_branch_git_bindings, 9. Workspace and git policy, 14. Security and safety, 18.3 Git isolation tests
- Claim IDs: `docs-workflows-checkpoint-branch-system#s2-2-git`, `docs-workflows-checkpoint-branch-system#s7-data`, `docs-workflows-checkpoint-branch-system#s9-workspace-git`, `docs-workflows-checkpoint-branch-system#s14-safety`, `docs-workflows-checkpoint-branch-system#s18-tests`, `docs-workflows-checkpoint-branch-system#s20-end`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-010`, `DESIGN-REQ-015`, `DESIGN-REQ-019`
- Dependencies: `STORY-001`

**Description**

As a workflow operator, repository-mutating checkpoint branches use isolated git work branches or worktrees whose names are deterministic, safe, collision-checked, and separate from product branch identity.

**Independent Test**

Create two repository-mutating branches from one checkpoint and verify each receives a distinct sanitized work branch from the expected base, protected refs are rejected, and existing refs are reused only when metadata matches the same Checkpoint Branch.

**Acceptance Criteria**

- Product branch id and git work branch name are stored separately and neither substitutes for the other.
- Repository-mutating branches use a distinct git branch, worktree, or provider workspace binding before code work begins.
- Generated branch names are deterministic under idempotency key and reject protected, empty, detached, unknown, or colliding refs unless metadata proves ownership.
- Workspace policy and baseline are recorded in branch metadata, turn metadata, Step Execution manifest, and diagnostics.

**Requirements**

- Implement git binding records for repository, base branch, base commit, work branch, worktree ref, head commit, patch ref, PR URL, and publish status.
- Support documented branch creation modes and workspace policies.
- Fail closed on git base mismatch, branch collision, protected branch ref, or incompatible workspace policy.

**Source Design Coverage**

- `DESIGN-REQ-003`: Owns product-vs-git identity separation.
- `DESIGN-REQ-010`: Owns workspace and git policy enforcement.
- `DESIGN-REQ-015`: Owns git and workspace safety.
- `DESIGN-REQ-019`: Owns git isolation tests.

**Assumptions**

- The repository checkout can identify the protected source branch before branch work starts.

**Needs Clarification**

- Whether archived branch git refs are retained, deleted, or delegated to repository retention policy.

### STORY-004: Expose checkpoint branch APIs for discovery and control

- Short name: `branch-api`
- Source sections: 8. API surface, 6.2 Operations, 9.4 Publish vs promote, 14. Security and safety, 16. Artifact requirements
- Claim IDs: `docs-workflows-checkpoint-branch-system#s8-api`, `docs-workflows-checkpoint-branch-system#s6-lifecycle`, `docs-workflows-checkpoint-branch-system#s2-4-promotion`, `docs-workflows-checkpoint-branch-system#s9-4-publish-promote`, `docs-workflows-checkpoint-branch-system#s14-safety`, `docs-workflows-checkpoint-branch-system#s16-artifacts`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-007`, `DESIGN-REQ-009`, `DESIGN-REQ-015`, `DESIGN-REQ-017`
- Dependencies: `STORY-001`, `STORY-002`

**Description**

As an API client, I can list checkpoints and checkpoint branches, create branches, continue and fork branch turns, publish or archive branches, and inspect branch evidence through typed HTTP endpoints.

**Independent Test**

Exercise the branch API create, continue, fork, archive, and discovery endpoints against a seeded workflow checkpoint, then assert idempotent responses and artifact-backed evidence refs are returned without exposing raw secrets or direct storage grants.

**Acceptance Criteria**

- Discovery endpoints return checkpoints, branch lists, branch details, and branch turns for a workflow.
- Create, continue, fork, archive, and publish operations require idempotency keys and record audit-backed evidence.
- Archive is non-destructive and keeps records, artifacts, git refs, and provider diagnostics inspectable.
- API responses preserve publication and promotion as separate concepts and never imply that a published branch is canonical.

**Requirements**

- Implement documented HTTP endpoints and representative request contracts.
- Return artifact refs as identifiers rather than direct access grants.
- Apply checkpoint validation and policy checks before side-effecting branch operations.

**Source Design Coverage**

- `DESIGN-REQ-005`: Preserves promotion separation in API contracts.
- `DESIGN-REQ-007`: Exposes branch operations.
- `DESIGN-REQ-009`: Owns the branch API surface.
- `DESIGN-REQ-015`: Owns API-level safety validation.
- `DESIGN-REQ-017`: Owns operation evidence references.

**Assumptions**

- Existing execution APIs provide workflow-scoped authorization and artifact ref handling.

### STORY-005: Compare branches and explicitly promote one result

- Short name: `compare-promote`
- Source sections: 2.4 Branches are candidates until promoted, 6.3 Promotion, 9.4 Publish vs promote, 13. Branch comparison, 14. Security and safety, 16. Artifact requirements, 20. Desired end state
- Claim IDs: `docs-workflows-checkpoint-branch-system#s2-4-promotion`, `docs-workflows-checkpoint-branch-system#s6-lifecycle`, `docs-workflows-checkpoint-branch-system#s9-4-publish-promote`, `docs-workflows-checkpoint-branch-system#s13-comparison`, `docs-workflows-checkpoint-branch-system#s14-safety`, `docs-workflows-checkpoint-branch-system#s16-artifacts`, `docs-workflows-checkpoint-branch-system#s20-end`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-014`, `DESIGN-REQ-015`, `DESIGN-REQ-017`, `DESIGN-REQ-021`
- Dependencies: `STORY-001`, `STORY-003`, `STORY-004`

**Description**

As an operator, I can compare candidate branches using durable evidence and explicitly promote one branch result into canonical workflow progress only after validation, gate, side-effect, and approval requirements pass.

**Independent Test**

Compare two completed branches, then promote the branch whose expected head matches and gates pass; verify comparison and promotion artifacts are written, competing branches remain inspectable, and mismatched heads or missing approvals fail closed.

**Acceptance Criteria**

- Comparison produces artifacts containing branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and bounded summary refs.
- Promotion records branch id, turn id, Step Execution id, accepted output refs, git/PR refs, gate verdict refs, side-effect disposition refs, invalidation effects, and approval evidence.
- Promotion requires expected head validation, passed gates, applicable approval, and fresh branch-head validation.
- Promotion does not delete competing branches and publication remains separate from canonical acceptance.

**Requirements**

- Implement comparison artifact generation and compare API behavior.
- Implement promotion operation with explicit policy gates and audit artifacts.
- Fail closed on approval_required, side_effect_policy_blocked, budget_exhausted, checkpoint invalidity, or expected-head mismatch.

**Source Design Coverage**

- `DESIGN-REQ-005`: Owns explicit promotion.
- `DESIGN-REQ-014`: Owns artifact-backed comparison.
- `DESIGN-REQ-015`: Owns promotion and comparison safety gates.
- `DESIGN-REQ-017`: Owns comparison and promotion artifacts.
- `DESIGN-REQ-021`: Owns compare/promote end state.

**Assumptions**

- Gate verdicts and side-effect classifications already have durable artifact refs or can be represented as new refs.

**Needs Clarification**

- Whether promotion updates mainline through Continue-As-New, a workflow update, or a linked accepted-branch relation.
- Whether branch comparison is synchronous for small diffs and asynchronous for large diffs.
- Whether no-code diagnosis can be promoted as a first-class accepted output.

### STORY-006: Support provider-bound branch continuation behind capability gates

- Short name: `provider-continuation`
- Source sections: 2.5 Provider sessions are runtime bindings, not branch authority, 10. Runtime/session policy, 12. Omnigent integration, 14. Security and safety, 18.6 Omnigent tests
- Claim IDs: `docs-workflows-checkpoint-branch-system#s2-5-provider`, `docs-workflows-checkpoint-branch-system#s10-runtime`, `docs-workflows-checkpoint-branch-system#s12-omnigent`, `docs-workflows-checkpoint-branch-system#s14-safety`, `docs-workflows-checkpoint-branch-system#s18-tests`
- Coverage IDs: `DESIGN-REQ-006`, `DESIGN-REQ-011`, `DESIGN-REQ-013`, `DESIGN-REQ-015`, `DESIGN-REQ-019`
- Dependencies: `STORY-002`, `STORY-003`

**Description**

As a runtime integrator, I can execute checkpoint branches through provider-specific bindings such as Omnigent while keeping provider sessions diagnostic, capability-gated, and subordinate to MoonMind branch evidence.

**Independent Test**

Run an Omnigent v1 branch turn and verify it creates a fresh session with a branch-turn idempotency key, passes instructions by instructionRef, records prior session refs as evidence only, and rejects same-session continuation unless a typed provider capability is enabled.

**Acceptance Criteria**

- Omnigent v1 validates checkpoint evidence, restores or synthesizes isolated workspace state, creates a new Omnigent session, and binds captured output to branch turn evidence.
- Provider ids, session ids, runner ids, file ids, and URLs are stored only as diagnostics or runtime binding metadata.
- External provider continuation is rejected unless adapter capability and typed lifecycle activities are available.
- Provider continuation never reuses the parent attempt idempotency key when branch-turn instructions produce a different first-message digest.

**Requirements**

- Implement fresh_omnigent_session_from_checkpoint behavior for v1.
- Represent external_provider_continuation only through explicit runtimeContextPolicy and capability-gated adapter contracts.
- Store Omnigent capture artifacts and prior session refs without making provider state authoritative.

**Source Design Coverage**

- `DESIGN-REQ-006`: Owns provider authority boundaries.
- `DESIGN-REQ-011`: Owns provider runtime policy selection.
- `DESIGN-REQ-013`: Owns Omnigent branch modes.
- `DESIGN-REQ-015`: Owns provider fail-closed safety.
- `DESIGN-REQ-019`: Owns Omnigent tests.

**Assumptions**

- Omnigent v1 streaming-gateway activity returns terminal AgentRunResult as described.

### STORY-007: Add dashboard branch explorer and safety previews

- Short name: `branch-ui`
- Source sections: 15. UI requirements, 13. Branch comparison, 14. Security and safety, 20. Desired end state
- Claim IDs: `docs-workflows-checkpoint-branch-system#s15-ui`, `docs-workflows-checkpoint-branch-system#s13-comparison`, `docs-workflows-checkpoint-branch-system#s14-safety`, `docs-workflows-checkpoint-branch-system#s20-end`
- Coverage IDs: `DESIGN-REQ-014`, `DESIGN-REQ-015`, `DESIGN-REQ-016`, `DESIGN-REQ-021`
- Dependencies: `STORY-004`, `STORY-005`

**Description**

As an operator, I can inspect checkpoint branches in the workflow detail page, invoke branch actions, view evidence and diagnostics, and review safety previews before creating or promoting a branch.

**Independent Test**

Open a workflow with one mainline checkpoint and multiple branches, then verify the default view remains mainline-focused, Branch Explorer displays branch and turn state, actions are available according to policy, and create/promote previews show safety inputs before submission.

**Acceptance Criteria**

- Workflow detail defaults to mainline with branch count and status affordances near checkpoints and failed or blocked steps.
- Branch Explorer displays checkpoint-to-branch-to-turn structure while preserving drill-in access to archived or failed evidence.
- UI actions support create, continue, fork, compare, promote, publish, archive, view evidence, view git diff, and view provider diagnostics where policy allows.
- Create and promotion previews show source checkpoint, workspace policy, git branch, runtime policy, publish mode, side-effect risk, budget impact, approvals, branch head, gate verdict, git/PR refs, invalidations, and competing branches.

**Requirements**

- Add branch discovery and action surfaces to workflow detail.
- Render artifact-backed comparison and evidence summaries without inlining large or sensitive evidence.
- Represent policy-disabled actions with actionable blocked state consistent with existing UI conventions.

**Source Design Coverage**

- `DESIGN-REQ-014`: Owns UI consumption of comparison artifacts.
- `DESIGN-REQ-015`: Owns safety previews.
- `DESIGN-REQ-016`: Owns dashboard Branch Explorer and actions.
- `DESIGN-REQ-021`: Owns operator-facing desired workflow.

**Assumptions**

- Dashboard can reuse existing workflow detail routing and artifact preview components.

**Needs Clarification**

- How the UI should display many branches from one checkpoint without overwhelming the workflow detail page.

### STORY-008: Gate checkpoint branch rollout with focused tests and open-decision handling

- Short name: `branch-verification`
- Source sections: 17. Migration and rollout, 18. Testing requirements, 19. Open questions, 5. Core invariants
- Claim IDs: `docs-workflows-checkpoint-branch-system#s17-rollout`, `docs-workflows-checkpoint-branch-system#s18-tests`, `docs-workflows-checkpoint-branch-system#s19-open`, `docs-workflows-checkpoint-branch-system#s5-invariants`
- Coverage IDs: `DESIGN-REQ-015`, `DESIGN-REQ-018`, `DESIGN-REQ-019`, `DESIGN-REQ-020`
- Dependencies: `STORY-001`, `STORY-002`, `STORY-003`, `STORY-004`, `STORY-005`, `STORY-006`

**Description**

As a platform maintainer, I can verify checkpoint branch behavior across schema, checkpoint validation, git isolation, runtime turns, promotion, and provider integration while preserving unresolved decisions as explicit blockers or assumptions.

**Independent Test**

Run the selected unit and integration test suites for branch schemas, checkpoint validation, git isolation, runtime branch turns, promotion, and Omnigent branch behavior, and verify unsupported unresolved choices fail fast or are excluded from enabled capabilities.

**Acceptance Criteria**

- Schema tests reject missing checkpoint refs, missing instruction refs/digests, post-launch turn mutation, identity conflation, and raw large or sensitive compact state.
- Checkpoint validation tests cover valid, missing, corrupted, plan-mismatched, policy-mismatched, and unauthorized checkpoints.
- Runtime and promotion tests verify new Step Executions, follow-up turns, forks, idempotency keys, artifact preservation, expected-head matching, gate requirements, invalidation records, and non-deletion of competitors.
- Capabilities whose open design questions are unresolved are disabled, approval-gated, or represented with explicit clarification evidence rather than silent fallback.

**Requirements**

- Add targeted schema, service, runtime, git, promotion, and provider tests as each implementation slice lands.
- Keep rollout phases as implementation sequencing, not durable product behavior in API contracts.
- Track open questions in implementation tickets or acceptance criteria until resolved.

**Source Design Coverage**

- `DESIGN-REQ-015`: Owns fail-closed safety test coverage.
- `DESIGN-REQ-018`: Owns rollout boundary coverage.
- `DESIGN-REQ-019`: Owns verification completeness.
- `DESIGN-REQ-020`: Owns explicit handling of open decisions.

**Assumptions**

- Each implementation slice can land with targeted tests for affected boundaries rather than requiring all later-phase tests immediately.

**Needs Clarification**

- Which workflows may permit automated branch exploration without user approval.

## Coverage Matrix

- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-001`
- `DESIGN-REQ-003` -> `STORY-003`
- `DESIGN-REQ-004` -> `STORY-002`
- `DESIGN-REQ-005` -> `STORY-004`, `STORY-005`
- `DESIGN-REQ-006` -> `STORY-006`
- `DESIGN-REQ-007` -> `STORY-001`, `STORY-004`
- `DESIGN-REQ-008` -> `STORY-001`
- `DESIGN-REQ-009` -> `STORY-004`
- `DESIGN-REQ-010` -> `STORY-003`
- `DESIGN-REQ-011` -> `STORY-002`, `STORY-006`
- `DESIGN-REQ-012` -> `STORY-002`
- `DESIGN-REQ-013` -> `STORY-006`
- `DESIGN-REQ-014` -> `STORY-005`, `STORY-007`
- `DESIGN-REQ-015` -> `STORY-003`, `STORY-004`, `STORY-005`, `STORY-006`, `STORY-007`, `STORY-008`
- `DESIGN-REQ-016` -> `STORY-007`
- `DESIGN-REQ-017` -> `STORY-002`, `STORY-004`, `STORY-005`
- `DESIGN-REQ-018` -> `STORY-008`
- `DESIGN-REQ-019` -> `STORY-003`, `STORY-006`, `STORY-008`
- `DESIGN-REQ-020` -> `STORY-008`
- `DESIGN-REQ-021` -> `STORY-001`, `STORY-005`, `STORY-007`
- `docs-workflows-checkpoint-branch-system#s1-goals` -> `STORY-001`
- `docs-workflows-checkpoint-branch-system#s1-purpose` -> `STORY-001`
- `docs-workflows-checkpoint-branch-system#s10-runtime` -> `STORY-002`, `STORY-006`
- `docs-workflows-checkpoint-branch-system#s11-context` -> `STORY-002`
- `docs-workflows-checkpoint-branch-system#s12-omnigent` -> `STORY-006`
- `docs-workflows-checkpoint-branch-system#s13-comparison` -> `STORY-005`, `STORY-007`
- `docs-workflows-checkpoint-branch-system#s14-safety` -> `STORY-003`, `STORY-004`, `STORY-005`, `STORY-006`, `STORY-007`
- `docs-workflows-checkpoint-branch-system#s15-ui` -> `STORY-007`
- `docs-workflows-checkpoint-branch-system#s16-artifacts` -> `STORY-002`, `STORY-004`, `STORY-005`
- `docs-workflows-checkpoint-branch-system#s17-rollout` -> `STORY-008`
- `docs-workflows-checkpoint-branch-system#s18-tests` -> `STORY-003`, `STORY-006`, `STORY-008`
- `docs-workflows-checkpoint-branch-system#s19-open` -> `STORY-008`
- `docs-workflows-checkpoint-branch-system#s2-1-graph` -> `STORY-001`
- `docs-workflows-checkpoint-branch-system#s2-2-git` -> `STORY-003`
- `docs-workflows-checkpoint-branch-system#s2-3-turns` -> `STORY-002`
- `docs-workflows-checkpoint-branch-system#s2-4-promotion` -> `STORY-004`, `STORY-005`
- `docs-workflows-checkpoint-branch-system#s2-5-provider` -> `STORY-006`
- `docs-workflows-checkpoint-branch-system#s20-end` -> `STORY-001`, `STORY-002`, `STORY-003`, `STORY-005`, `STORY-007`
- `docs-workflows-checkpoint-branch-system#s3-model` -> `STORY-001`
- `docs-workflows-checkpoint-branch-system#s4-terms` -> `STORY-001`
- `docs-workflows-checkpoint-branch-system#s5-invariants` -> `STORY-001`, `STORY-008`
- `docs-workflows-checkpoint-branch-system#s6-lifecycle` -> `STORY-001`, `STORY-002`, `STORY-004`, `STORY-005`
- `docs-workflows-checkpoint-branch-system#s7-data` -> `STORY-001`, `STORY-002`, `STORY-003`
- `docs-workflows-checkpoint-branch-system#s8-api` -> `STORY-004`
- `docs-workflows-checkpoint-branch-system#s9-4-publish-promote` -> `STORY-004`, `STORY-005`
- `docs-workflows-checkpoint-branch-system#s9-workspace-git` -> `STORY-003`

## Canonical Claims

- `docs-workflows-checkpoint-branch-system#s1-purpose` (1. Purpose) Checkpoint Branches create independent continuations from eligible workflow or step checkpoints with distinct instructions, workspace policy, runtime policy, model/provider settings, and publish strategy.
- `docs-workflows-checkpoint-branch-system#s1-goals` (1. Purpose) The system preserves branch evidence, isolates code changes, keeps instructions immutable, supports repeated turns, enables comparison and explicit promotion, and prevents exploration from silently mutating canonical workflow state.
- `docs-workflows-checkpoint-branch-system#s2-1-graph` (2.1 Add a product-level branch graph above Step Executions) A product-level branch graph is added above Logical Steps, Step Executions, manifests, and checkpoints while the execution plane remains the source of truth.
- `docs-workflows-checkpoint-branch-system#s2-2-git` (2.2 Separate product branches from git branches) Checkpoint Branch product identity is distinct from git branch identity; git branches are repository isolation bindings.
- `docs-workflows-checkpoint-branch-system#s2-3-turns` (2.3 Branching is not ordinary retry) Branch turns are new semantic executions that create new Step Execution identity, record lineage, pin checkpoint identity, preserve immutable instructions, declare policies, and write manifest evidence.
- `docs-workflows-checkpoint-branch-system#s2-4-promotion` (2.4 Branches are candidates until promoted) Branches become canonical only through explicit workflow-owned promotion gated by evidence, side-effect classification, workspace validation, and approval policy.
- `docs-workflows-checkpoint-branch-system#s2-5-provider` (2.5 Provider sessions are runtime bindings, not branch authority) Provider sessions are diagnostics/runtime bindings; MoonMind branch records, Step Execution manifests, checkpoints, artifact refs, git refs, and promotion records are authoritative.
- `docs-workflows-checkpoint-branch-system#s3-model` (3. Conceptual model) Branches fan out from checkpoints, accumulate branch turns, may be forked into child branches, and fan in only through explicit promotion or a new execution naming multiple branch inputs.
- `docs-workflows-checkpoint-branch-system#s4-terms` (4. Terminology) Retry, step re-execution, failed-step recovery, checkpoint branch, branch turn, promotion, and publication are distinct operations.
- `docs-workflows-checkpoint-branch-system#s5-invariants` (5. Core invariants) Branches are explicit, rooted in validated checkpoints, append-only, branch-scoped, isolated when repository-mutating, and fail closed when validation or policy is unsafe.
- `docs-workflows-checkpoint-branch-system#s6-lifecycle` (6. Branch lifecycle) Checkpoint Branches and Branch Turns have explicit states, canonical operations, and promotion records.
- `docs-workflows-checkpoint-branch-system#s7-data` (7. Data model) Branches, turns, git bindings, artifacts, and optional Step Execution manifest branch metadata are persisted for lineage and audit.
- `docs-workflows-checkpoint-branch-system#s8-api` (8. API surface) The API exposes checkpoint and branch discovery plus branch create, continue, fork, compare, promote, and archive operations.
- `docs-workflows-checkpoint-branch-system#s9-workspace-git` (9. Workspace and git policy) Branch workspace creation modes and policies are explicit, consistently recorded, and git work branch names are deterministic, sanitized, and collision-safe.
- `docs-workflows-checkpoint-branch-system#s9-4-publish-promote` (9.4 Publish vs promote) Publication and promotion are separate; a branch may be unpublished and unpromoted, published but unpromoted, promoted but unpublished, or both.
- `docs-workflows-checkpoint-branch-system#s10-runtime` (10. Runtime/session policy) Branch turns declare runtime context policy before launch with defaults for branch creation, continuation, fork, same-runtime conversations, and Omnigent modes.
- `docs-workflows-checkpoint-branch-system#s11-context` (11. Context bundle) Branch turns receive immutable digest-addressed context bundles with refs and bounded summaries only, never raw logs, diffs, provider payloads, or credentials.
- `docs-workflows-checkpoint-branch-system#s12-omnigent` (12. Omnigent integration) Omnigent v1 uses fresh sessions from checkpoints; same-session continuation requires typed lifecycle activities and capability gating.
- `docs-workflows-checkpoint-branch-system#s13-comparison` (13. Branch comparison) Branch comparison produces durable bounded artifacts with git, quality, diagnostics, and summary refs.
- `docs-workflows-checkpoint-branch-system#s14-safety` (14. Security and safety) Branch records, artifacts, workspace restore, git operations, provider continuation, side effects, promotion, archival, and comparison inherit MoonMind safety policy and fail closed in unsafe cases.
- `docs-workflows-checkpoint-branch-system#s15-ui` (15. UI requirements) The dashboard provides Branch Explorer, mainline-focused defaults, branch actions, evidence views, diagnostics views, and safety previews before creation and promotion.
- `docs-workflows-checkpoint-branch-system#s16-artifacts` (16. Artifact requirements) Branch, branch turn, promotion, and comparison operations produce minimum durable artifacts.
- `docs-workflows-checkpoint-branch-system#s17-rollout` (17. Migration and rollout) Delivery is sliced into branch graph and fresh runtime turns, compare and promote, provider continuation, and policy-driven branch exploration.
- `docs-workflows-checkpoint-branch-system#s18-tests` (18. Testing requirements) Tests cover schemas, checkpoint validation, git isolation, runtime behavior, promotion, and Omnigent integration.
- `docs-workflows-checkpoint-branch-system#s19-open` (19. Open questions) Open choices about execution containment, promotion mechanics, id shape, archived git branch retention, comparison execution, UI scale, auto exploration, and no-code promotion remain unresolved.
- `docs-workflows-checkpoint-branch-system#s20-end` (20. Desired end state) Operators can create, continue, fork, compare, promote, archive, and publish branches while MoonMind enforces checkpoint validation, artifact authority, Step Execution identity, workspace/git isolation, runtime boundaries, gates, side-effect classification, and promotion authority.

## Coverage Points

- `DESIGN-REQ-001` [requirement] Checkpoint branch primitive: Create named independent continuations from eligible checkpoints with branch-local policies and instructions.
- `DESIGN-REQ-002` [state-model] Product branch graph: Persist branch, turn, Step Execution, checkpoint, and child-branch lineage above the execution substrate.
- `DESIGN-REQ-003` [constraint] Product versus git identity: Keep product branch ids separate from optional git branch/worktree bindings.
- `DESIGN-REQ-004` [artifact] Branch turn execution evidence: Record every branch turn that executes work as new Step Execution evidence with immutable launch metadata.
- `DESIGN-REQ-005` [requirement] Explicit promotion: Require evidence-gated workflow-owned promotion before any branch becomes canonical progress.
- `DESIGN-REQ-006` [constraint] Provider bindings are not authority: Treat provider ids and sessions as runtime diagnostics, not authoritative branch identity.
- `DESIGN-REQ-007` [state-model] Lifecycle states and operations: Expose documented branch and turn states plus create, continue, fork, compare, promote, archive, and publish operations.
- `DESIGN-REQ-008` [requirement] Persistent branch data model: Persist branches, turns, git bindings, artifact links, and Step Execution manifest branch metadata.
- `DESIGN-REQ-009` [integration] Branch API surface: Provide HTTP endpoints for branch discovery and controls.
- `DESIGN-REQ-010` [requirement] Workspace and git policies: Record and enforce branch creation modes, workspace policies, git branch naming, and collision rules.
- `DESIGN-REQ-011` [requirement] Runtime context policy: Declare runtime/session policy before launch with gated exceptional modes.
- `DESIGN-REQ-012` [artifact] Immutable context bundles: Build digest-addressed context bundles from refs, bounded summaries, lineage, policy, baseline, evidence, and builder metadata.
- `DESIGN-REQ-013` [integration] Omnigent branch modes: Support fresh Omnigent sessions for v1 and capability-gated provider continuation for v2-style lifecycle activities.
- `DESIGN-REQ-014` [artifact] Artifact-backed comparison: Produce durable bounded branch comparison artifacts.
- `DESIGN-REQ-015` [security] Security and fail-closed safety: Enforce secrets, artifact, checkpoint, workspace, git, provider, side-effect, promotion, archival, and comparison safety rules.
- `DESIGN-REQ-016` [requirement] Dashboard branch experience: Expose Branch Explorer, simple mainline defaults, branch actions, evidence views, diagnostics, and safety previews.
- `DESIGN-REQ-017` [artifact] Minimum durable artifacts: Emit required branch, branch turn, promotion, and comparison artifacts.
- `DESIGN-REQ-018` [migration] Incremental delivery boundaries: Deliver the desired system in focused slices without making rollout phases the product model.
- `DESIGN-REQ-019` [requirement] Verification coverage: Test schema, validation, git isolation, runtime, promotion, and Omnigent behavior.
- `DESIGN-REQ-020` [constraint] Open decisions preserved: Carry unresolved choices as clarification items or disabled capabilities instead of hidden behavior.
- `DESIGN-REQ-021` [requirement] Operator end state: Enable safe branch create, continue, fork, compare, promote, archive, and publish flows with MoonMind authority and policy enforcement.

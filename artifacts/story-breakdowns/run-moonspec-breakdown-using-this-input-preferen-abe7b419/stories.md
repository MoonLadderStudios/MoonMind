# MoonSpec Story Breakdown: Step Executions and Checkpointing

- Source: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-996`
- Output mode: `jira`
- Coverage gate: PASS - every major design point is owned by at least one story.
- Recommended first story: `STORY-001`

## Design Summary

The source defines a desired-state execution model where repeated work is represented as new Step Executions with explicit identity, immutable context, durable manifest evidence, compact restorable checkpoints, structured gate decisions, side-effect policy, preserved-progress semantics, and bounded operator/API projections. The operational outcomes are reliable failed-step recovery, safe autonomous remediation loops, auditable attempt histories, and fail-closed behavior when checkpoint or side-effect evidence is missing or unsafe.

## Coverage Points

- `DESIGN-REQ-001` (state-model): Distinct authority layers - The implementation must keep plan, workflow state, manifests, checkpoints, git state, ledger projections, and DB projections in their documented authority roles.
- `DESIGN-REQ-002` (artifact): Manifest and checkpoint separation - Manifests provide attempt evidence, while checkpoints provide compact restore material; recovery cannot be based on a manifest alone.
- `DESIGN-REQ-003` (state-model): Explicit Step Execution identity and lineage - Every semantic re-execution must have validated run-scoped identity, optional separate lineage provenance, and stable side-effect idempotency keys.
- `DESIGN-REQ-004` (artifact): Single immutable manifest write path - Start and terminal attempt evidence must be written through one canonical workflow path with bounded refs, statuses, checks, outputs, side effects, and budget.
- `DESIGN-REQ-005` (artifact): Immutable attempt context bundle - Attempts must receive digest-addressed context bundles with compact refs, workspace policy, baseline, retrieval, memory, and runtime selection evidence.
- `DESIGN-REQ-006` (artifact): Durable checkpoint creation and validation - Checkpoint boundaries and workspace evidence kinds must be explicit, durable, policy-compatible, and fail closed with typed validation codes.
- `DESIGN-REQ-007` (constraint): Workspace and git policy enforcement - Repeated attempts must declare and apply workspace policy, classify git effects, and only advance on accepted committed/published/artifact/no-change outputs.
- `DESIGN-REQ-008` (security): Side-effect classification and guardrails - Workspace, artifact, external, publication, provider, memory, and retrieval effects require disposition tracking, idempotency, policy, and compensation where needed.
- `DESIGN-REQ-009` (integration): Activity and worker boundary enforcement - Workflow code owns orchestration but delegates file, git, artifact, provider, memory, checkpoint, and workspace side effects to idempotent Activities with compact typed results.
- `DESIGN-REQ-010` (requirement): Workflow-owned gated iteration - The parent workflow owns structured gate verdict branching, retry eligibility, budgets, stop rules, downstream invalidation, and publication eligibility.
- `DESIGN-REQ-011` (state-model): Dependency invalidation and preserved reuse - Downstream outputs must trace to producing Step Executions, and changed outputs or bad preserved refs must invalidate, revalidate, or block reuse.
- `DESIGN-REQ-012` (requirement): Checkpoint-backed failed-step recovery - Recovery creates a linked follow-up run, preserves completed prior steps, validates checkpoint evidence, restores workspace policy, and launches the failed step without full-rerun fallback.
- `DESIGN-REQ-013` (integration): Managed runtime attempt ownership - Managed runtimes execute work, while MoonMind owns attempt records, context, workspace policy, gate execution, side-effect classification, ledger projection, and manifests.
- `DESIGN-REQ-014` (observability): Operator and API attempt visibility - Dashboard and APIs must expose latest attempts, expanded step-execution history, lineage, policy, git disposition, gate verdicts, and artifact refs without large inline evidence.
- `DESIGN-REQ-015` (security): Failure, security, and compatibility guardrails - Failures must use typed dispositions, security must exclude unsafe evidence and materialization, non-goals must be preserved, and payload changes must be replay-safe or explicitly cut over.
- `DESIGN-REQ-016` (constraint): Conformance and workflow-boundary coverage - Changes to Step Execution and checkpoint behavior must update the conformance harness and include boundary coverage for compact refs, degraded inputs, and compatibility-sensitive payloads.

## Stories

### STORY-001: Define canonical Step Execution identity, manifests, and compact ledger projections

- Short name: `execution-ledger`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 1. Purpose, 2. Layering and Source of Truth, 5. Core Invariants, 6. Identity, Lineage, and Idempotency, 7. Step Execution Manifest Contract, 17. Operator and API Surfaces
- Canonical claim IDs: `docs/Steps/StepExecutionsAndCheckpointing.md#1-purpose-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#2-layering-and-source-of-truth-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#5-core-invariants-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#6-identity-lineage-and-idempotency-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#7-step-execution-manifest-contract-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#17-operator-and-api-surfaces-claim-001`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-003`, `DESIGN-REQ-004`, `DESIGN-REQ-014`
- Dependencies: None

As a workflow operator, I need each semantic re-execution to produce a distinct, traceable Step Execution record with immutable manifest evidence and bounded ledger/API projections so that repeated work is auditable without relying on transcripts or mutable read models.

Independent test:

Run workflow-boundary tests that create an initial execution and one semantic re-execution, then assert distinct ordinals, manifest refs, idempotency keys, ledger latest projection, expanded step-executions API response, and rejection of invalid identity payloads.

Acceptance criteria:

- A semantic re-execution creates a new Step Execution with monotonically increasing run-scoped executionOrdinal.
- Manifest writes for a Step Execution go through one canonical writer and produce immutable start and terminal refs under the same identity.
- Workflow state and API projections carry compact refs and bounded summaries, never raw logs, diffs, provider payloads, or credentials.
- Cross-run lineage fields remain separate from preservedFrom fields and never replace local run-scoped identity.
- The API uses the step-executions path terminology and exposes expanded attempt history without making it the default detail view.

Requirements:

- Implement validated identity/key construction for StepExecutionId, checkpointId, artifactLinkScope, and operation idempotency keys.
- Persist or project latest manifest refs and manifest ref history without treating DB projections as source of truth.
- Map malformed, blank, or mismatched identities to typed invalid model-boundary failures.
- Expose bounded operator/API attempt projections keyed by executionOrdinal.

### STORY-002: Capture and validate durable workspace checkpoints for Step Execution boundaries

- Short name: `workspace-checkpoints`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 2.1 Manifest vs checkpoint, 8. Context Bundle, Retrieval, and Memory Inputs, 9. Checkpoint Contract, 10. Workspace and Git Policy, 12. Activity Surface and Worker Boundaries, 19. Security and Side-Effect Guardrails
- Canonical claim IDs: `docs/Steps/StepExecutionsAndCheckpointing.md#21-manifest-vs-checkpoint-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#8-context-bundle-retrieval-and-memory-inputs-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#9-checkpoint-contract-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#10-workspace-and-git-policy-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#12-activity-surface-and-worker-boundaries-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#19-security-and-side-effect-guardrails-claim-001`
- Coverage IDs: `DESIGN-REQ-002`, `DESIGN-REQ-005`, `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-009`, `DESIGN-REQ-015`
- Dependencies: `STORY-001`

As a workflow operator, I need each resumable Step Execution boundary to capture durable, policy-compatible workspace checkpoint evidence and validate it fail-closed so that later attempts and recovery runs never depend on ephemeral or synthetic workspace state.

Independent test:

Run activity-boundary and workflow-boundary tests that modify a repo, capture before and after checkpoints, create a StepExecutionCheckpoint from the captured evidence, validate it against each supported policy, and assert missing/corrupt/unauthorized/incompatible/synthetic evidence blocks before launch.

Acceptance criteria:

- The canonical checkpoint writer calls workspace.capture_checkpoint before creating any checkpoint that can make resumeAllowed true.
- Checkpoint artifacts carry planRef or planDigest and ref-only workspace/step output evidence, rejecting inline raw evidence at the model boundary.
- Workspace policy selection and checkpoint kind requirements are explicit and visible in Step Execution metadata and diagnostics.
- Validation checks source workflow/run, task input, plan, logical step, execution provenance, artifact authorization, workspace consistency, checkpoint kind, and side-effect policy eligibility.
- An ephemeral_workspace_ref without durable TTL/reachability proof or a synthetic temporal locator remains diagnostic-only and ineligible for resume.

Requirements:

- Implement a shared policy resolver used by checkpoint capture, checkpoint creation, manifest workspace metadata, recovery manifests, and recovery preparation.
- Add or update workspace.capture_checkpoint and step_checkpoint.create integration points in the parent workflow boundary.
- Return StepCheckpointValidationFailureCode values rather than prose or exceptions for expected degraded checkpoint inputs.
- Ensure workspace.apply_policy only receives validated, policy-compatible evidence and only materializes inside approved workspaces.

### STORY-003: Resume failed workflows from artifact-backed recovery manifests and preserved progress

- Short name: `failed-step-resume`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 14. Dependency Invalidation and Preserved Output Reuse, 15. RecoverFromFailedStep Relationship, 16. Managed Runtime Relationship, 18. Failure and Stop Semantics, 20.2 Failed-step recovery, 20.2.1 Failed-run recovery manifest
- Canonical claim IDs: `docs/Steps/StepExecutionsAndCheckpointing.md#14-dependency-invalidation-and-preserved-output-reuse-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#15-recoverfromfailedstep-relationship-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#16-managed-runtime-relationship-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#18-failure-and-stop-semantics-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#20-examples-claim-001`
- Coverage IDs: `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-015`
- Dependencies: `STORY-001`, `STORY-002`

As an operator recovering a failed workflow, I need a follow-up run to load the failed-run recovery manifest, validate selected checkpoint evidence, preserve completed prior steps, restore the workspace, and launch the failed step as a new local Step Execution without silently rerunning earlier work.

Independent test:

Run an end-to-end workflow test where step 1 and 2 succeed, step 3 fails after durable checkpoints are captured, the source run emits a recovery manifest, and a follow-up run validates the manifest, preserves steps 1 and 2, applies workspace policy, then launches step 3 with correct lineage.

Acceptance criteria:

- Every failed run emits reports/recovery_manifest.json and a compact finish-summary ref before terminal failure is reported.
- resumeAllowed is true only when checkpoint validation is valid and a checkpoint ref is present; missing, corrupt, unauthorized, incompatible, invalid, or non-durable checkpoints block resume with a structured reason.
- The follow-up run loads recovery data from artifacts and verifies any selected checkpoint ref matches the manifest before workspace.apply_policy runs.
- Completed prior steps are imported as preserved progress with preservedFrom workflow/run/logical step/execution ordinal and validated reusable refs.
- The failed step is the first newly executed logical step in the resumed run and records reason recover_from_failed_step plus pinned source workflow/run lineage.
- Resume blocks before child workflow launch when side effects are blocked, uncompensated, or preserved outputs fail validation.

Requirements:

- Make resume launch artifact-driven and reject denormalized blob-only recovery source drift.
- Re-check non-idempotent side-effect dispositions immediately before launching the failed step.
- Use the same checkpoint validation and workspace.apply_policy path for failed-step recovery and bounded story-loop resume decisions.
- Emit structured terminal diagnostics for blocked recovery rather than silently falling back to full rerun.

### STORY-004: Run bounded gated iteration with structured verdicts, budgets, and dependency invalidation

- Short name: `gated-iteration`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 10.2 Accepted output rule, 11. Side-Effect Classes and External Guardrails, 13. Gated Iteration, 13.1 Gate verdict contract, 13.2 Budgets and stop rules, 14. Dependency Invalidation and Preserved Output Reuse, 18. Failure and Stop Semantics, 20.1 Jira Orchestrate gated implementation, 20.3 Autonomous story loop, 20.4 Dependency invalidation
- Canonical claim IDs: `docs/Steps/StepExecutionsAndCheckpointing.md#10-workspace-and-git-policy-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#11-side-effect-classes-and-external-guardrails-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#13-gated-iteration-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#14-dependency-invalidation-and-preserved-output-reuse-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#18-failure-and-stop-semantics-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#20-examples-claim-001`
- Coverage IDs: `DESIGN-REQ-007`, `DESIGN-REQ-008`, `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-015`
- Dependencies: `STORY-001`, `STORY-002`

As a workflow owner, I need remediation and verification loops to be governed by structured gate verdicts, explicit budgets, workspace policy, and dependency invalidation so autonomous repair can continue safely and stop deterministically before publication when evidence is insufficient.

Independent test:

Run workflow tests for a remediation loop where gates return ADDITIONAL_WORK_NEEDED until a later FULLY_IMPLEMENTED verdict, another where budget exhausts with failed_with_remaining_work, and another where an upstream accepted output change invalidates a downstream preserved step.

Acceptance criteria:

- The parent workflow branches on structured gate results and never parses prose summaries to decide advancement, retry, block, or publication.
- ADDITIONAL_WORK_NEEDED triggers another bounded remediation only while configured budget remains and a later remediation step exists.
- Budget exhaustion stops before publication/Jira movement and publishes latest evidence, remaining work, side-effect dispositions, and recommended next action.
- Failed or partial attempts retain diff/checkpoint evidence without story advancement; passing attempts create accepted git or artifact output before logical success.
- Downstream steps that consumed superseded outputs are marked pending, blocked, superseded, or requires_revalidation before reuse.

Requirements:

- Record gate verdicts, validated refs, invalidated refs, remaining work refs, recommended next action, target logical step, and workspace policy recommendation as typed data.
- Attach budget dimensions and exhausted state to autonomous-loop Step Execution manifests.
- Tie downstream input refs to producing Step Execution outputs instead of ambiguous latest values unless explicitly refreshed.
- Require structured validation evidence before preserving downstream outputs after dependency changes.

### STORY-005: Enforce Step Execution conformance, security, and versioned compatibility at workflow boundaries

- Short name: `conformance-guardrails`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 5.1 Conformance gate for MM-822+ changes, 12. Activity Surface and Worker Boundaries, 19. Security and Side-Effect Guardrails, 21. Non-Goals, 22. Versioning and Compatibility, 23. Constitution Alignment
- Canonical claim IDs: `docs/Steps/StepExecutionsAndCheckpointing.md#51-conformance-gate-for-mm-822+-changes-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#12-activity-surface-and-worker-boundaries-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#19-security-and-side-effect-guardrails-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#21-non-goals-claim-001`, `docs/Steps/StepExecutionsAndCheckpointing.md#22-versioning-and-compatibility-claim-001`
- Coverage IDs: `DESIGN-REQ-009`, `DESIGN-REQ-015`, `DESIGN-REQ-016`
- Dependencies: `STORY-001`, `STORY-002`, `STORY-003`, `STORY-004`

As a maintainer, I need Step Execution and checkpoint changes to be guarded by conformance fixtures, security checks, and replay-safe compatibility handling so degraded persisted payloads fail closed and future changes cannot reintroduce unsafe inline evidence or ambiguous aliases.

Independent test:

Run the documented conformance command and targeted unit/integration boundary tests with fixtures for old manifest rows, old checkpoint rows, legacy stateCheckpointRef-only rows, unknown enum values, inline evidence attempts, and unsafe restore paths.

Acceptance criteria:

- The PR validation path records the step_execution_conformance command and targeted test command or a justified fixture no-change note.
- Unknown, blank, degraded, or future persisted manifest/checkpoint/gate/policy values become typed invalid/degraded boundary decisions rather than crashes or silent coercions.
- New writers emit only canonical v1 content types unless an explicit cutover introduces a new version.
- Inline large evidence and secret-like fields are rejected from manifests, checkpoints, context bundles, retrieval manifests, memory proposals, workflow state, and ordinary telemetry.
- Security tests cover artifact authorization, restore scoping, path traversal prevention, and blocked non-idempotent side effects.

Requirements:

- Keep compatibility handling at workflow/activity/model boundaries, not through broad aliases or hidden semantic transforms.
- Update conformance fixtures whenever checkpoint capture artifacts, checkpoint-backed Resume, typed gate results, context bundles, provider-profile side effects, Docker boundaries, or skill-projection blocked environments change.
- Document impossible in-flight compatibility only when the cutover drains, cancels, resets, or otherwise safely handles affected runs.
- Ensure workflow code remains deterministic and side-effect free while activity contracts remain compact and typed.

## Coverage Matrix

- `docs/Steps/StepExecutionsAndCheckpointing.md#1-purpose-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#2-layering-and-source-of-truth-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#21-manifest-vs-checkpoint-claim-001` -> `STORY-002`
- `docs/Steps/StepExecutionsAndCheckpointing.md#5-core-invariants-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#51-conformance-gate-for-mm-822+-changes-claim-001` -> `STORY-005`
- `docs/Steps/StepExecutionsAndCheckpointing.md#6-identity-lineage-and-idempotency-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#7-step-execution-manifest-contract-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#8-context-bundle-retrieval-and-memory-inputs-claim-001` -> `STORY-002`
- `docs/Steps/StepExecutionsAndCheckpointing.md#9-checkpoint-contract-claim-001` -> `STORY-002`
- `docs/Steps/StepExecutionsAndCheckpointing.md#10-workspace-and-git-policy-claim-001` -> `STORY-002`, `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#11-side-effect-classes-and-external-guardrails-claim-001` -> `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#12-activity-surface-and-worker-boundaries-claim-001` -> `STORY-002`, `STORY-005`
- `docs/Steps/StepExecutionsAndCheckpointing.md#13-gated-iteration-claim-001` -> `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#14-dependency-invalidation-and-preserved-output-reuse-claim-001` -> `STORY-003`, `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#15-recoverfromfailedstep-relationship-claim-001` -> `STORY-003`
- `docs/Steps/StepExecutionsAndCheckpointing.md#16-managed-runtime-relationship-claim-001` -> `STORY-003`
- `docs/Steps/StepExecutionsAndCheckpointing.md#17-operator-and-api-surfaces-claim-001` -> `STORY-001`
- `docs/Steps/StepExecutionsAndCheckpointing.md#18-failure-and-stop-semantics-claim-001` -> `STORY-003`, `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#19-security-and-side-effect-guardrails-claim-001` -> `STORY-002`, `STORY-005`
- `docs/Steps/StepExecutionsAndCheckpointing.md#20-examples-claim-001` -> `STORY-003`, `STORY-004`
- `docs/Steps/StepExecutionsAndCheckpointing.md#21-non-goals-claim-001` -> `STORY-005`
- `docs/Steps/StepExecutionsAndCheckpointing.md#22-versioning-and-compatibility-claim-001` -> `STORY-005`
- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-002`
- `DESIGN-REQ-003` -> `STORY-001`
- `DESIGN-REQ-004` -> `STORY-001`
- `DESIGN-REQ-005` -> `STORY-002`
- `DESIGN-REQ-006` -> `STORY-002`
- `DESIGN-REQ-007` -> `STORY-002`, `STORY-004`
- `DESIGN-REQ-008` -> `STORY-004`
- `DESIGN-REQ-009` -> `STORY-002`, `STORY-005`
- `DESIGN-REQ-010` -> `STORY-004`
- `DESIGN-REQ-011` -> `STORY-003`, `STORY-004`
- `DESIGN-REQ-012` -> `STORY-003`
- `DESIGN-REQ-013` -> `STORY-003`
- `DESIGN-REQ-014` -> `STORY-001`
- `DESIGN-REQ-015` -> `STORY-002`, `STORY-003`, `STORY-004`, `STORY-005`
- `DESIGN-REQ-016` -> `STORY-005`

## Notes

- No `spec.md` files or `specs/` directories are created by this breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Downstream `/speckit.verify` should compare final behavior against the original source design preserved in `stories.json`.

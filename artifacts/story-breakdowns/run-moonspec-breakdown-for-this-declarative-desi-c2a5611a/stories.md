# Step Executions and Checkpointing Story Breakdown

Source design: `docs/Steps/StepExecutionsAndCheckpointing.md`
Source document class: `canonical-declarative`
Extracted at: 2026-06-11T21:34:52Z
Output mode: `jira`

## Design Summary

The canonical design defines Step Executions with Checkpointing as MoonMind's desired-state primitive for safely repeating work. Logical steps may have multiple semantic executions, each with explicit identity, lineage, immutable context, checkpoint evidence, side-effect classification, gate results, budgets, and terminal disposition. The workflow keeps compact refs while artifacts hold evidence; activities own side effects; recovery and autonomous story loops consume validated checkpoints and typed gate decisions rather than logs, prose, or UI projections.

## Coverage Points

- `DESIGN-REQ-001` **Step Execution primitive and scope** (requirement, 1. Purpose; 3. Desired-State Summary; 4. Terminology): MoonMind represents semantic re-execution as explicit Step Executions with separate identity, lineage, context, evidence, disposition, and stop rules.
- `DESIGN-REQ-002` **Layered source of truth and ref-only workflow state** (state-model, 2. Layering and Source of Truth; 2.1 Manifest vs checkpoint): Workflow state carries compact refs, artifacts hold large evidence, checkpoints are distinct recovery evidence, and app DB/UI are projections only.
- `DESIGN-REQ-003` **Canonical content types and terminology** (constraint, 2. Layering and Source of Truth; 4. Terminology; 17. Operator and API Surfaces): New writers use only the canonical manifest and checkpoint content types and API/contract language uses step-executions, executionOrdinal, retry/reexecute/recover distinctions.
- `DESIGN-REQ-004` **Identity, lineage, and idempotency keys** (state-model, 6. Identity, Lineage, and Idempotency): StepExecutionId, checkpointId, child workflow IDs, lineage, preservedFrom metadata, and operation idempotency keys are deterministic and validated.
- `DESIGN-REQ-005` **Single typed manifest writer path** (artifact, 7. Step Execution Manifest Contract): Every Step Execution writes typed start and terminal manifest artifacts through one canonical path with identity, input, context, workspace, execution, outputs, checks, sideEffects, dependencyEffects, and budget metadata.
- `DESIGN-REQ-006` **Manifest validation rejects inline large evidence** (security, 2. Layering and Source of Truth; 7. Step Execution Manifest Contract; 19. Security and Side-Effect Guardrails): Manifests and workflow projections carry refs and bounded summaries, not raw stdout, stderr, diffs, logs, provider payloads, credentials, or large evidence.
- `DESIGN-REQ-007` **Immutable context bundle per Step Execution** (artifact, 8. Context Bundle, Retrieval, and Memory Inputs; 16. Managed Runtime Relationship): Every launched Step Execution has an immutable digest-addressed context bundle containing refs, workspace policy, runtime selection, gate profile, retrieval, memory, and prior evidence.
- `DESIGN-REQ-008` **Attempt-scoped retrieval manifests** (artifact, 8.1 Retrieval context): Retrieval and RAG inputs are attempt inputs recorded through manifests with query/selector, index version, returned refs, filters, exclusions, scores where safe, and compact summaries.
- `DESIGN-REQ-009` **Memory proposal and promotion policy** (artifact, 8.2 Memory effects; 11. Side-Effect Classes and External Guardrails; 19. Security and Side-Effect Guardrails): Memory updates use explicit proposal and promotion states; failed attempts cannot silently write durable repo memory; repo-level memory requires normal publication gates.
- `DESIGN-REQ-010` **Checkpoint model and boundaries** (artifact, 9. Checkpoint Contract; 9.1 Checkpoint boundaries): Checkpoint artifacts carry planRef or planDigest, task input refs, prepared refs, workspace evidence, outputs, and are written idempotently at canonical boundaries.
- `DESIGN-REQ-011` **Checkpoint kind and workspace policy compatibility** (state-model, 9.2 Workspace checkpoint kinds; 9.3 Policy-to-checkpoint requirements; 10. Workspace and Git Policy): Workspace policies require compatible checkpoint evidence such as git_commit, git_patch, worktree_archive, ephemeral workspace refs, accepted commits, or source refs.
- `DESIGN-REQ-012` **Typed checkpoint validation failures** (constraint, 9.4 Checkpoint validation; 18. Failure and Stop Semantics): Checkpoint validation checks source, task input, plan, step, artifact, workspace, checkpoint kind, and policy, and failures stop before launch or mutation with canonical failure codes.
- `DESIGN-REQ-013` **Workspace and git effect classification** (state-model, 10. Workspace and Git Policy; 10.1 Git effect states; 10.2 Accepted output rule): Repeated coding work declares workspace policy before launch and classifies git effects as accepted, candidate, discarded, superseded, or none; dirty workspaces are not accepted state.
- `DESIGN-REQ-014` **External side-effect guardrails** (security, 11. Side-Effect Classes and External Guardrails; 19. Security and Side-Effect Guardrails): External side effects are classified, idempotent or explicitly policy-permitted, compensated observably, and publication/Jira/merge/deploy/provider actions require gate-approved workflow state.
- `DESIGN-REQ-015` **Activity and workflow boundary ownership** (integration, 12. Activity Surface and Worker Boundaries; 16. Managed Runtime Relationship): MoonMind.UserWorkflow orchestrates compact typed refs while activities inspect files, git, artifacts, providers, workspace policy, retrieval, and memory; adapters execute attempts but do not own attempt semantics.
- `DESIGN-REQ-016` **Structured gate result contract** (integration, 13. Gated Iteration; 13.1 Gate verdict contract): Gate-producing steps return typed verdict payloads and the parent workflow branches on structured verdict/recommended action instead of prose or ad-hoc strings.
- `DESIGN-REQ-017` **Autonomous loop budgets and stop rules** (requirement, 13.2 Budgets and stop rules; 18. Failure and Stop Semantics): Autonomous loops have attempt and non-attempt budgets; exhaustion stops with deterministic terminal disposition, latest evidence, remaining work, and recommended next action.
- `DESIGN-REQ-018` **Dependency invalidation and preserved output reuse** (state-model, 14. Dependency Invalidation and Preserved Output Reuse): Downstream outputs bind to producing Step Executions; changed accepted outputs invalidate/revalidate dependents; preserved outputs require checkpoint-backed ref and signature validation.
- `DESIGN-REQ-019` **Checkpoint-backed RecoverFromFailedStep and selected-step recovery** (requirement, 15. RecoverFromFailedStep Relationship; 20.2 Failed-step recovery): Recovery creates a linked execution, imports prior steps as preserved refs, validates source checkpoint evidence, restores workspace, and starts new work at the failed or selected step without silent full rerun.
- `DESIGN-REQ-020` **Managed runtime context policy** (integration, 16. Managed Runtime Relationship; 16.1 Runtime context policy): Attempts distinguish workspace policy from runtime/session context policy and pass compact refs to owned or coordinated runtimes while recording weaker restoration capabilities when applicable.
- `DESIGN-REQ-021` **Operator and API attempt surfaces** (integration, 17. Operator and API Surfaces): Default UI/API surfaces show latest Step Execution compactly, expanded surfaces expose bounded history and refs, and no large attempt payloads are inlined.
- `DESIGN-REQ-022` **Security boundaries for artifacts, restore, skills, and secrets** (security, 19. Security and Side-Effect Guardrails): Context, manifests, checkpoints, retrieval, memory, and restoration forbid raw secrets, unauthorized artifacts, path traversal, writes outside approved workspaces, and unsafe trusted-skill assumptions.
- `DESIGN-REQ-023` **Autonomous story loops use Step Execution primitives** (requirement, 20.1 Jira Orchestrate gated implementation; 20.3 Autonomous story loop; 20.4 Dependency invalidation): Explicit operator-submitted story loops use the same checkpoint, gate, accepted-output, side-effect, budget, and invalidation primitives instead of separate retry systems.
- `DESIGN-REQ-024` **Non-goals and compatibility policy** (migration, 21. Non-Goals; 22. Versioning and Compatibility; 23. Constitution Alignment): The design excludes infinite loops, hidden attempts, inline large state, Activity retries as semantic attempts, and separate recovery systems; payload changes use versioned cutovers and degraded persisted values fail closed or become typed invalid decisions.

## Ordered Story Candidates

### STORY-001 - Establish Step Execution conformance harness

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 2. Layering and Source of Truth, 4. Terminology, 7. Step Execution Manifest Contract, 9. Checkpoint Contract, 17. Operator and API Surfaces, 22. Versioning and Compatibility

As a MoonMind maintainer, I need a focused contract suite for Step Execution manifests, checkpoints, terminology, compatibility, and degraded inputs so payload-affecting changes are guarded before production workflow paths change.

Independent test: Run a Step Execution conformance test target that exercises model validators, API terminology checks, old compact payload fixtures, degraded checkpoint payloads, degraded gate verdicts, and no-inline-evidence assertions without requiring a full workflow run.

Acceptance criteria:
- A single conformance command covers manifest, checkpoint, gate, terminology, and API contract fixtures.
- Tests fail when manifests or checkpoints include raw stdout, stderr, diffs, logs, provider payloads, credentials, or oversized inline evidence.
- At least one replay or degraded-input fixture exists for old manifest rows, old checkpoint rows, old gate verdict strings, and old ledger rows with only legacy checkpoint refs.
- Canonical terms step-executions, executionOrdinal, and recover_from_failed_step are enforced.

Requirements:
- Add golden fixtures for successful execution, failed reattempt, gate failure, recovery with preserved steps, degraded checkpoint payload, and degraded gate verdict.
- Assert canonical content types and terminology for all new evidence writers.
- Provide compatibility helpers that return typed invalid/degraded decisions rather than uncaught exceptions.

Dependencies: None
Coverage: DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-021, DESIGN-REQ-024

### STORY-002 - Consolidate typed Step Execution manifest writing

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 6. Identity, Lineage, and Idempotency, 7. Step Execution Manifest Contract, 10. Workspace and Git Policy, 11. Side-Effect Classes and External Guardrails

As a workflow operator, I need every Step Execution manifest to be emitted through one typed writer so start, blocked, and terminal evidence is consistent, auditable, and free of legacy duplicate payload shapes.

Independent test: Run unit and workflow-boundary tests proving start and terminal manifests share the canonical typed builder, include compensation, side-effect, execution metadata, budget fields, and emit only canonical content types.

Acceptance criteria:
- There is exactly one production Step Execution manifest builder/writer path.
- Typed start manifests include reattempt compensation, side-effect records, execution metadata overrides, budget metadata, input refs, workspace policy, and launch-blocked payloads where applicable.
- Typed terminal manifests include status, terminal disposition, output refs, checks, dependency effects, and side-effect summaries.
- No production references remain to legacy untyped manifest helpers or artifact naming assumptions tied only to report-style JSON paths.

Requirements:
- Fold all unique legacy start-writer behavior into the typed writer.
- Delete the untyped helper and direct tests of its raw output.
- Keep replay-safe command behavior while routing patched and new paths through the canonical writer.

Dependencies: STORY-001
Coverage: DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-013, DESIGN-REQ-014

### STORY-003 - Add checkpoint model and activity substrate

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 2.1 Manifest vs checkpoint, 9. Checkpoint Contract, 9.1 Checkpoint boundaries, 9.2 Workspace checkpoint kinds, 9.4 Checkpoint validation, 12. Activity Surface and Worker Boundaries

As MoonMind orchestration, I need typed checkpoint capture, create, validate, and workspace evidence contracts so recovery and reattempt policies can rely on durable state instead of logs or UI projections.

Independent test: Run model and activity-boundary tests that create, persist, read, and validate checkpoint artifacts independently from a full workflow, including invalid payload and unauthorized/path-traversal cases.

Acceptance criteria:
- Checkpoint models require planRef or planDigest and reject inline raw evidence.
- git_patch checkpoints require baseCommit and patchRef; other checkpoint kinds validate their required refs.
- Checkpoint artifacts write with application/vnd.moonmind.step-execution-checkpoint+json;version=1.
- Activity outputs are compact typed refs and are idempotent under {stepExecutionId}:checkpoint:{boundary}.
- Invalid, missing, unauthorized, corrupted, or incompatible checkpoint inputs produce typed failure codes.

Requirements:
- Add capture, create, validate, and optional git-effect classification activity contracts at activity/service boundaries.
- Support initial checkpoint kinds git_commit, git_patch, ephemeral_workspace_ref, and durable extension points for archive/external refs.
- Ensure workflow code does not shell out to git or read workspace files directly.

Dependencies: STORY-001
Coverage: DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-024

### STORY-004 - Capture checkpoints at canonical workflow boundaries

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 7. Step Execution Manifest Contract, 9.1 Checkpoint boundaries, 12. Activity Surface and Worker Boundaries, 16. Managed Runtime Relationship, 17. Operator and API Surfaces

As a workflow operator, I need Step Executions to write checkpoint artifacts at canonical boundaries and link them from manifests and ledger rows so recovery evidence exists before it is needed.

Independent test: Run workflow-boundary tests for a successful path and a failed gated path and assert before_execution, after_execution, after_gate or before_publication checkpoint refs are written without raw payloads in workflow state.

Acceptance criteria:
- New workflow runs produce real checkpoint refs for canonical boundaries that apply to the path.
- Manifest workspace.checkpointBeforeRef and workspace.checkpointAfterRef are populated where applicable.
- Ledger rows retain latestStepExecutionCheckpointRef, stepExecutionCheckpointRefs, and checkpointRefsByBoundary while read-side compatibility handles legacy refs.
- API step detail exposes checkpoint refs only, not checkpoint payload bodies.

Requirements:
- Add a canonical workflow helper that derives Step Execution identity, checkpoint ID, idempotency key, task input snapshot, plan refs, prepared refs, workspace policy, and expected workspace.
- Call capture/create activities at after_prepare, before_execution, after_execution, after_gate, before_publication, and before_recovery_restoration where applicable.
- Keep workflow history compact.

Dependencies: STORY-002, STORY-003
Coverage: DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-020, DESIGN-REQ-021

### STORY-005 - Validate and apply checkpoint-backed workspace policies

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 9.3 Policy-to-checkpoint requirements, 9.4 Checkpoint validation, 10. Workspace and Git Policy, 12. Activity Surface and Worker Boundaries, 18. Failure and Stop Semantics, 19. Security and Side-Effect Guardrails

As MoonMind orchestration, I need reattempts and recovery launches to validate compatible checkpoint evidence and apply the selected workspace policy before any agent launch or workspace mutation.

Independent test: Run workflow-boundary and activity-boundary tests proving missing, incompatible, corrupted, unauthorized, or path-unsafe checkpoint evidence blocks launch before mutation, while valid policies apply idempotently and launch exactly once.

Acceptance criteria:
- Policies restore_pre_execution, continue_from_previous_execution, apply_previous_execution_diff_to_clean_baseline, start_from_last_passed_commit, and fresh_branch_from_source validate required evidence before launch.
- Validation failure writes a blocked Step Execution manifest and structured diagnostics with canonical failure code, logical step, source workflow/run, checkpoint ref, policy, and recommended next action.
- No agent/tool launch happens after checkpoint validation failure.
- workspace.apply_policy is idempotent and scoped to approved workspaces.

Requirements:
- Add or wire checkpoint validation helper into workspace-policy launch paths, reattempt loops, and recovery launch paths.
- Add workspace.apply_policy activity behavior for each canonical policy.
- Fail closed instead of silently falling back to full rerun.

Dependencies: STORY-003, STORY-004
Coverage: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-024

### STORY-006 - Make Resume checkpoint-backed by default

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 6.2 Cross-run lineage, 14. Dependency Invalidation and Preserved Output Reuse, 15. RecoverFromFailedStep Relationship, 17. Operator and API Surfaces, 20.2 Failed-step recovery

As an operator resuming a failed or selected step, I need recovery to restore from validated checkpoint evidence, preserve completed steps from verified refs, and fail explicitly when evidence is missing or mismatched.

Independent test: Run recovery integration tests where a source run has completed preserved steps and a failed step checkpoint; assert recovery validates source refs, restores workspace, starts the failed/selected step as local executionOrdinal 1, and blocks on missing or mismatched evidence before marking the step running.

Acceptance criteria:
- recover-from-failed-step and recover-from-selected-step payloads include source workflow/run, logical step, source execution ordinal, checkpoint ref, checkpoint boundary, task input snapshot, plan ref/digest, preserved-step refs, dependency signatures, and workspace policy.
- Preserved steps materialize only after checkpoint and output-ref validation passes.
- Missing checkpoint, plan mismatch, missing preserved output, unauthorized/corrupt artifact, or changed dependency signature fails before workspace mutation or _mark_step_running.
- UI/API exposes checkpoint eligibility and typed disabled/failure reasons; full retry remains a separate explicit action.

Requirements:
- Pin source workflowId and runId in recovery lineage.
- Capture before_recovery_restoration checkpoint for recovery execution.
- Apply workspace policy from validated checkpoint before new work.

Dependencies: STORY-005
Coverage: DESIGN-REQ-004, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-024

### STORY-007 - Gate external handoffs on accepted terminal disposition

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 7.3 Terminal dispositions, 10.1 Git effect states, 10.2 Accepted output rule, 11. Side-Effect Classes and External Guardrails, 18. Failure and Stop Semantics, 19. Security and Side-Effect Guardrails

As an operator, I need terminal manifests to classify side effects and all publication, Jira, merge, deploy, or provider-account handoffs to require accepted terminal disposition plus gate approval.

Independent test: Run workflow tests where failed candidate attempts, blocked gates, and accepted attempts reach handoff boundaries; assert only accepted disposition plus passing gate permits external handoff and terminal manifests include side-effect records.

Acceptance criteria:
- Terminal accepted manifests include sideEffects.git.disposition=accepted and accepted output evidence.
- Failed or partial attempts include candidate/discarded/superseded side-effect records but do not open handoff gates.
- PR creation, Jira transition/comment, merge automation, deployment/publish, and provider-account actions are blocked unless the producing Step Execution is accepted and gate-approved.
- Non-idempotent external actions without explicit policy are denied at the activity boundary and recorded as blocked side effects.

Requirements:
- Aggregate git, external, artifact, publication, compensation, memory, retrieval, and record side effects into terminal manifests.
- Add a producing-step accepted assertion before external handoff activities.
- Keep existing MoonSpec gate verdict blocking and add terminal-disposition gating.

Dependencies: STORY-002, STORY-004
Coverage: DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-022

### STORY-008 - Introduce typed gate result branching

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 13. Gated Iteration, 13.1 Gate verdict contract, 13.2 Budgets and stop rules, 18. Failure and Stop Semantics, 22. Versioning and Compatibility

As MoonMind workflow orchestration, I need gate-producing steps to emit structured typed results so parent workflow decisions are deterministic, compatible, and not based on prose or ad-hoc verdict strings.

Independent test: Run unit and workflow-boundary tests for FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, NO_DETERMINATION, BLOCKED, FAILED_UNRECOVERABLE, legacy string FULLY_IMPLEMENTED, future string PARTIALLY_IMPLEMENTED, and prose-only malformed gate output.

Acceptance criteria:
- New gate writers emit StepGateResult-style artifacts with verdict, confidence, validated refs, invalidated refs, remaining work, blocking evidence, recommended action, target step, workspace policy recommendation, and recoverability.
- Parent workflow branches only on typed verdict and recommendedNextAction.
- Legacy string verdicts normalize without crashing; unknown/future/prose-only values fail closed as typed invalid/degraded decisions.
- Manifest checks and check rows include gate result ref, verdict, confidence, validated refs, remaining work ref, target step, and policy recommendation.

Requirements:
- Add typed schema and read-boundary normalization.
- Update review/MoonSpec activities to return typed gate result artifacts.
- Preserve budget metadata in manifests for autonomous loops.

Dependencies: STORY-001
Coverage: DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-024

### STORY-009 - Build immutable context and retrieval manifests

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 8. Context Bundle, Retrieval, and Memory Inputs, 8.1 Retrieval context, 16. Managed Runtime Relationship, 16.1 Runtime context policy, 19. Security and Side-Effect Guardrails

As a workflow operator, I need every Step Execution launch to have an immutable context bundle and attempt-scoped retrieval manifest so the agent-visible inputs are auditable, digest-addressed, and not hidden ambient state.

Independent test: Run workflow-boundary tests where initial and reattempt executions build context bundles with refs/digests, prior evidence refs, retrieval skipped/unavailable/captured states, and digest changes when retrieval inputs change.

Acceptance criteria:
- New manifest starts no longer write context={} for launched Step Executions.
- Context bundles include task input snapshot, plan ref/digest, prepared inputs, workspace policy/baseline, checkpoint refs, prior evidence, retrieval manifest ref, memory manifest ref, runtime selection, quality gate profile, policy refs, builder version, and stable digest.
- Retrieval manifests record skipped, unavailable, or captured status explicitly.
- Context and retrieval artifacts reject raw credentials, large logs, raw diffs, and unsafe provider payloads.

Requirements:
- Add step_context.build_context_bundle helper/activity with stable JSON digesting.
- Include prior failed attempt evidence in reattempt context bundles.
- Expose context refs through API/UI as refs only.

Dependencies: STORY-002, STORY-004, STORY-008
Coverage: DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-015, DESIGN-REQ-020, DESIGN-REQ-022

### STORY-010 - Add memory proposal and promotion manifests

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 8.2 Memory effects, 11. Side-Effect Classes and External Guardrails, 19. Security and Side-Effect Guardrails

As MoonMind orchestration, I need memory changes to be explicit attempt-scoped artifacts with policy-controlled promotion so failed attempts cannot silently alter durable repo instructions.

Independent test: Run unit, activity-boundary, and workflow tests where failed attempts propose memory only, accepted attempts promote run-local memory when policy allows, repo-level memory writes require accepted disposition and publication gates, and superseded proposals are recorded.

Acceptance criteria:
- MemoryProposal, MemoryManifest, and MemoryPolicyDecision models include source Step Execution identity and promotion states proposed, accepted_for_run_context, applied_to_repo, rejected, and superseded.
- Failed or abandoned attempts cannot write durable repo memory.
- Terminal manifests classify memory side effects.
- Repo-level memory writes are blocked unless gate and terminal disposition are accepted.

Requirements:
- Add memory.evaluate_proposals and memory.apply_policy activity surfaces or equivalents.
- Record run-local memory visibility in context bundles for later attempts.
- Sanitize memory proposal content and store large text as refs.

Dependencies: STORY-007, STORY-009
Coverage: DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-022

### STORY-011 - Implement bounded autonomous story loops

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 13. Gated Iteration, 13.2 Budgets and stop rules, 14. Dependency Invalidation and Preserved Output Reuse, 20.1 Jira Orchestrate gated implementation, 20.3 Autonomous story loop, 20.4 Dependency invalidation, 21. Non-Goals

As an operator submitting PRD or story work, I need MoonMind to compile story items into bounded logical Step Executions that use checkpointing, typed gates, accepted-output rules, invalidation, and explicit stop budgets.

Independent test: Run integration tests for a story loop where attempt 1 fails gate and retains candidate diff, attempt 2 applies the previous diff to a clean baseline and passes, budget exhaustion stops before handoff, and downstream story steps invalidate when upstream accepted output changes.

Acceptance criteria:
- Explicit operator-submitted PRD/story items compile into implement, verify, remediation, and verification-gate logical steps using fresh Step Execution identity per semantic attempt.
- Failed attempts retain candidate diffs as artifacts only and do not advance logical story state or publish externally.
- Accepted attempts commit or publish accepted typed outputs, advance state, and invalidate dependent steps when semantic outputs change.
- Loops record attempt budget plus at least one non-attempt stop dimension and stop with needs_human, blocked, or failed_with_remaining_work when exhausted.
- This story does not implement a fully autonomous unattended supervisor beyond explicit submitted workflows.

Requirements:
- Use typed gate result recommendations and checkpoint-backed workspace policies for remediation attempts.
- Commit only accepted attempts.
- Show latest attempt by default and expanded history by refs only.

Dependencies: STORY-005, STORY-007, STORY-008, STORY-009, STORY-010
Coverage: DESIGN-REQ-001, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-023, DESIGN-REQ-024

### STORY-012 - Expose Step Execution evidence in API and Mission Control

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 14. Dependency Invalidation and Preserved Output Reuse, 15. RecoverFromFailedStep Relationship, 17. Operator and API Surfaces, 18. Failure and Stop Semantics

As an operator, I need compact UI and API surfaces for checkpoint eligibility, gate result fields, context/retrieval/memory refs, side effects, and expanded Step Execution history without inlining large payloads.

Independent test: Run API contract and UI tests for step list/detail, step-executions list/detail, recovery eligibility, disabled reasons, gate fields, checkpoint refs, context refs, and expanded history while asserting no large evidence is inlined.

Acceptance criteria:
- Default workflow detail shows latest/current Step Execution, count, gate/block summaries, latest evidence refs, and preserved/resumed provenance.
- Expanded history shows execution ordinal, lineage, reason, source attempt, runtime child refs, context bundle ref, workspace policy, git disposition, gate verdict, output/diagnostic/diff refs, side effects, and terminal disposition.
- Recovery actions show Resume from checkpoint as default only when eligible; disabled states include typed reasons; full retry remains separate.
- API route vocabulary remains step-executions keyed by execution_ordinal and does not expose attempts route language.

Requirements:
- Update Mission Control and API projections after core evidence models exist.
- Expose refs and typed diagnostics, not raw artifact bodies.
- Include downstream invalidation and preserved-step status in compact rows where relevant.

Dependencies: STORY-006, STORY-007, STORY-008, STORY-009, STORY-010, STORY-011
Coverage: DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-022

### STORY-013 - Finalize docs, terminology, and compatibility cleanup

Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`; sections: 2. Layering and Source of Truth, 21. Non-Goals, 22. Versioning and Compatibility, 23. Constitution Alignment

As a MoonMind maintainer, I need the canonical docs, disposable gap plan, terminology checks, compatibility tests, and conformance suite to agree once Step Executions and Checkpointing are fully implemented.

Independent test: Run the full Step Execution conformance suite plus terminology checks proving no stale content types, no external attempts routes, no duplicate manifest writer, no new context={}, no mutating terminal sideEffects={}, and no policy-required launch without validated checkpoint evidence.

Acceptance criteria:
- Canonical docs and code agree on completed behavior.
- Disposable docs/tmp gap plan is deleted only after all implementation gaps are closed.
- No old checkpoint content-type spellings or stale attempt route terms remain.
- Compatibility tests demonstrate degraded persisted values fail closed or normalize to typed invalid decisions, never workflow task crashes.
- Full downstream TDD/verify handoff remains anchored to the original canonical document.

Requirements:
- Update docs/Steps/StepExecutionsAndCheckpointing.md and related Temporal/API docs only after implementation evidence warrants it.
- Keep canonical docs declarative and place any rollout notes under docs/tmp or artifact handoffs.
- Delete disposable tracking material after closure.

Dependencies: STORY-012
Coverage: DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-024

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-011
- `DESIGN-REQ-002` -> STORY-001, STORY-003, STORY-004
- `DESIGN-REQ-003` -> STORY-001, STORY-013
- `DESIGN-REQ-004` -> STORY-002, STORY-006
- `DESIGN-REQ-005` -> STORY-002, STORY-004, STORY-007
- `DESIGN-REQ-006` -> STORY-001, STORY-002, STORY-013
- `DESIGN-REQ-007` -> STORY-009
- `DESIGN-REQ-008` -> STORY-009
- `DESIGN-REQ-009` -> STORY-010
- `DESIGN-REQ-010` -> STORY-001, STORY-003, STORY-004
- `DESIGN-REQ-011` -> STORY-003, STORY-005
- `DESIGN-REQ-012` -> STORY-001, STORY-003, STORY-005
- `DESIGN-REQ-013` -> STORY-002, STORY-005, STORY-007
- `DESIGN-REQ-014` -> STORY-002, STORY-007, STORY-010
- `DESIGN-REQ-015` -> STORY-003, STORY-004, STORY-005, STORY-009
- `DESIGN-REQ-016` -> STORY-008
- `DESIGN-REQ-017` -> STORY-008, STORY-011
- `DESIGN-REQ-018` -> STORY-006, STORY-011, STORY-012
- `DESIGN-REQ-019` -> STORY-006, STORY-012
- `DESIGN-REQ-020` -> STORY-004, STORY-009
- `DESIGN-REQ-021` -> STORY-001, STORY-004, STORY-006, STORY-012, STORY-013
- `DESIGN-REQ-022` -> STORY-003, STORY-005, STORY-007, STORY-009, STORY-010, STORY-012
- `DESIGN-REQ-023` -> STORY-011
- `DESIGN-REQ-024` -> STORY-001, STORY-003, STORY-005, STORY-006, STORY-008, STORY-011, STORY-013

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001
- `STORY-004` depends on: STORY-002, STORY-003
- `STORY-005` depends on: STORY-003, STORY-004
- `STORY-006` depends on: STORY-005
- `STORY-007` depends on: STORY-002, STORY-004
- `STORY-008` depends on: STORY-001
- `STORY-009` depends on: STORY-002, STORY-004, STORY-008
- `STORY-010` depends on: STORY-007, STORY-009
- `STORY-011` depends on: STORY-005, STORY-007, STORY-008, STORY-009, STORY-010
- `STORY-012` depends on: STORY-006, STORY-007, STORY-008, STORY-009, STORY-010, STORY-011
- `STORY-013` depends on: STORY-012

## Out of Scope

- Implementing code, specs, tasks, Jira issues, PRs, or runtime changes during breakdown: The active breakdown skill only creates derived story candidates.
- Fully autonomous unattended remediation supervisor: The canonical document and provided plan limit this phase to explicit operator-submitted story/PRD loops.
- Backfilling checkpoints from old prose logs or UI projections: The canonical data/backfill policy requires legacy runs to remain legacy evidence only unless a valid checkpoint artifact exists.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Downstream Notes

Recommended first story for `/speckit.specify`: `STORY-001 - Establish Step Execution conformance harness`.
No stories contain `[NEEDS CLARIFICATION]` markers.
TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.

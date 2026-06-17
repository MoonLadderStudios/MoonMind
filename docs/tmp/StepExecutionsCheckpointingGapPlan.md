# Step Executions and Checkpointing — Implementation Gap Plan

Status: Execution plan (disposable; not canonical)
Created: 2026-06-10
Last Updated: 2026-06-13
Canonical design: `docs/Steps/StepExecutionsAndCheckpointing.md`
Roadmap alignment: `docs/MoonMindRoadmap.md` Milestones 1, 3, 5, 12, 13, and 14

This document tracks the remaining wiring between the canonical desired-state
Step Executions and Checkpointing design and production `MoonMind.UserWorkflow`
orchestration. It exists under `docs/tmp/` per the Constitution's **Canonical Documentation Separates Desired State from Migration Backlog** principle.
Delete it when the gaps close.

This refresh accounts for the June 2026 roadmap reframing around managed runtime
parity, safety/governance, operator-driven recovery, and deep observability. The
remaining work is therefore not just a local Step Execution cleanup: it is the
execution substrate for the current P0/P1 roadmap items.

---

## 1. Verified current state (2026-06-13)

The 2026-06-10 gap assessment is still directionally correct, but more related
work has landed or been queued since then. The status below separates shipped
foundations from remaining gaps.

### 1.1 Shipped or substantially wired foundations

| Capability | Status | Notes |
| --- | --- | --- |
| Step Execution identity / ordinal increment | Wired | Step rows carry `attempt` / `executionOrdinal`; `_mark_step_running(increment_attempt=True)` increments semantic executions. |
| Canonical content types | Wired | `STEP_EXECUTION_MANIFEST_CONTENT_TYPE` and `STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE` are canonical. Do not reintroduce `step-checkpoint`, `step-resume-checkpoint`, or API `attempts` vocabulary. |
| Typed manifest start and terminal models | Wired | `_record_step_execution_manifest` writes `StepExecutionManifestModel` payloads with canonical content type for start, launch-blocked, and terminal evidence. |
| Legacy duplicate start manifest path | Removed | Manifest creation is folded into `_record_step_execution_manifest`; read-side validation remains separate for persisted/degraded evidence. |
| Ledger manifest refs | Wired | Latest and historical Step Execution manifest refs are attached to step rows. |
| Gated reattempt loop | Wired for MoonSpec/remediation presets | `ADDITIONAL_WORK_NEEDED` / `FULLY_IMPLEMENTED` drive bounded reattempt behavior; the broader typed gate model is still missing. |
| Non-attempt stop dimension | Wired | Consecutive no-progress budget is recorded in Step Execution budget manifests and can stop downstream handoff. |
| Selected-step recovery | Wired at API/payload/UI foundation level | `recover-from-selected-step` exists alongside last-failed-step recovery, but checkpoint-backed restoration is not yet the default path. |
| Latest evidence/provenance UI | Wired | Default workflow detail rows surface latest attempt evidence refs and preserved provenance without full attempt history by default. |
| Side-effect compensation primitives | Partially wired | Non-idempotent effect detection and compensation planning exist; terminal manifests still do not classify all side effects. |
| Managed clean-context reattempt evidence | Partially wired | Managed reattempt session reset evidence exists for current managed runtime paths; Claude managed-session parity and generic checkpoint/fork semantics remain roadmap work. |
| Compatibility boundary helpers | Wired | Raw manifest/checkpoint payloads can degrade to typed invalid results instead of crashing read/replay boundaries. |
| Security hardening around attempts | Partially wired | Failed-attempt summary redaction and selected repo/local skill provenance enforcement exist; policy envelopes/governance telemetry remain roadmap work. |

### 1.2 Confirmed unfinished implementation gaps

| Gap | Current impact | Owning work package |
| --- | --- | --- |
| Checkpoint artifact creation at canonical boundaries | `step_checkpoints.py` helpers exist, but workflow capture is still mostly ref-ingestion from runtime outputs; canonical boundary checkpoint artifacts are not written by `MoonMind.UserWorkflow`. | WP2, WP3 |
| Checkpoint-backed workspace policy application | Launch validation can block missing evidence, but policy application/restoration is not backed by validated checkpoint artifacts. | WP4 |
| Checkpoint-backed Resume as default recovery path | Recovery payloads carry checkpoint evidence, but recovery does not yet validate/apply a checkpoint before new work as the primary operator path. | WP5 |
| Terminal side-effect classification | Typed terminal manifests still emit empty or incomplete `sideEffects`; external handoff is not universally gated by producing-step terminal disposition. | WP6 |
| Typed gate result contract | Gate verdicts are still ad-hoc strings / local model shapes rather than the full section 13.1 structured result. | WP7 |
| Immutable Step Execution context bundles | New manifests still write `context={}` in typed paths; there is no digest-addressed context bundle artifact per attempt. | WP8 |
| Retrieval context pack integration | RAG primitives exist, but retrieval-backed context packs are not injected into Temporal step execution. | WP8 |
| Memory manifests and promotion states | Memory systems exist, but Step Execution-scoped memory proposal/promotion side effects are not represented in manifests. | WP9 |
| Claude managed-session checkpoint/resume/fork parity | Roadmap Milestone 1.9d is still open; Step Execution checkpoint contracts must not become Codex-only. | WP10 |
| Observability correlation | Step Execution evidence does not yet consistently carry trace/log/cost correlation refs required by Milestone 14. | WP11 |
| Operator remediation panels / queryable remediation audit | Recovery foundations exist, but Mission Control panels and queryable remediation audit remain open. | WP12 |
| Autonomous story/PRD loops | MoonSpec remediation presets exist, but the general autonomous story/PRD loop is not implemented. Autonomous 3 a.m. supervisor remains gated by safety/governance/observability milestones. | WP13 |

### 1.3 Active story handoff

`STORY-006` / Jira `MM-825` tracks the concrete implementation story for making
Resume checkpoint-backed by default. That story maps to this plan's **WP5** and
must not be treated as closing the entire Step Execution checkpointing gap. It is
one downstream Jira Orchestrate execution focused on the recovery-default path;
WP1-WP4 remain prerequisites or supporting substrate, and WP6-WP13 remain
post-recovery hardening/alignment work.

### 1.4 Phase 12 cleanup decision

Phase 12 evidence review retains this temp plan because the final definition of
done is still open: checkpoint-backed recovery is not yet the default operator
flow, retrieval-backed context packs are not injected into Temporal step
execution, and remediation panels remain partial. The plan is non-canonical
temporary material and must not be cited as implementation completion evidence.

Conditional documentation review:

- `docs/ManagedAgents/DockerSidecarRuntime.md`: no Phase 12 update required;
  current evidence did not change sidecar checkpoint/restore behavior.
- `docs/Security/SecretsSystem.md`: no Phase 12 update required; current
  evidence did not add secret refs to checkpoint or context payloads.
- `docs/Steps/PentestTool.md`: no Phase 12 update required; current evidence
  did not change provider leases, role constraints, or side-effect gates for
  pentest workflows.

---

## 2. Cross-project alignment rules

1. **Workflow terminology first.** New code, docs, and APIs must use Workflow
   Execution / Step Execution terminology. `task` naming may remain only at
   legacy wire boundaries explicitly marked as compatibility surfaces.
2. **Do not create a second recovery system.** Failed-step recovery,
   selected-step recovery, remediation actions, and future autonomous repair must
   all consume the same checkpoint, Step Execution, and evidence primitives.
3. **Do not outrun safety milestones.** Autonomous repair and risky action
   execution must remain gated on policy envelopes, governance telemetry, secret
   lifecycle audit, and outbound scan coverage. Step Execution work may add hooks
   and evidence fields now, but must not invent a parallel governance substrate.
4. **Keep managed runtimes provider-neutral.** Checkpoint, context-bundle,
   runtime-reset, session-epoch, and fork/resume contracts must support Codex,
   Claude Code, Gemini, external-provider continuation, and future Codex Cloud
   adapters. Any Codex-specific activity behavior must be behind adapter
   boundaries.
5. **Use retrieval/memory primitives instead of ambient state.** RAG context
   packs and memory proposals must be captured as Step Execution inputs/effects
   with refs and digests. They must not be hidden in prompts or runtime session
   state only.
6. **Observability refs are part of evidence.** Step Execution manifests,
   checkpoints, gate results, context bundles, and side-effect records should
   carry trace/log/cost correlation refs as optional compact metadata so Milestone
   14 can add UI/deep links without rewriting the execution plane again.
7. **Activity names in the design are not mandatory one-for-one.** Generic
   `artifact.create` / `artifact.write_complete` may remain the manifest write
   path. New dedicated activities should be added only where they own side
   effects, such as workspace capture/apply, checkpoint validation, outbound
   scan, or memory policy.
8. **No silent fallback to full rerun.** Any path labeled checkpoint resume or
   selected-step recovery must validate and apply checkpoint evidence before new
   work. If validation fails, fail closed with typed diagnostics and offer an
   explicit full retry as a separate operator choice.

---

## 3. Execution sequence

Each work package should land as a focused PR. Every PR that changes workflow
state, activity input/output, artifact payloads, or API projection must include:

- workflow or activity boundary tests, not only pure unit tests;
- one in-flight or legacy payload compatibility case;
- one degraded-input/fail-closed case;
- terminology verification where new public surface text is introduced;
- replay-safety notes in the PR description.

### WP0 — Rebaseline conformance harness (prerequisite, small)

**Goal:** Create a small contract suite that fails whenever new work regresses
Step Execution terminology, compact evidence, replay compatibility, or checkpoint
failure behavior.

**Work:**

1. Add or extend a Step Execution checkpointing conformance suite.
2. Add golden fixtures for first success, failed execution followed by
   reexecution, gate-result retry, recovery with preserved steps, degraded old
   checkpoint payload, and old string-only gate verdict.
3. Assert no raw `stdout`, `stderr`, `diff`, `logs`, provider payload, raw
   credentials, or long free-form evidence appears in workflow state or compact
   manifest/checkpoint sections.
4. Assert canonical route vocabulary remains `step-executions` keyed by
   `execution_ordinal` / `executionOrdinal`.

**Acceptance:** Contract suite runs locally and in CI before behavioral changes
land.

---

### WP1 — Consolidate manifest writers (completed)

**Goal:** Make exactly one Step Execution manifest write path per identity.

**Work:**

1. Introduce one typed internal builder for start/terminal manifests that returns
   `StepExecutionManifestModel`.
2. Fold the legacy start path's unique behavior into the typed path:
   - reattempt compensation plan;
   - side-effect records;
   - execution metadata override (`kind`, `toolName`, idempotency key);
   - budget metadata;
   - launch-blocked summary/disposition.
3. Route all manifest call sites through the canonical typed method.
4. Delete duplicate manifest-start helpers entirely.
5. Keep replay patch markers only as routing guards, not as alternate payload
   builders.

**Tests:** typed reattempt start manifest includes compensation/budget data;
launch-blocked start manifest uses canonical content type; old raw manifest
payload validates/degrades at the boundary; grep/contract test proves the legacy
helper is gone.

**Acceptance:** Production manifest writes use `_record_step_execution_manifest`
and `STEP_EXECUTION_MANIFEST_CONTENT_TYPE`; persisted manifest reads continue to
validate or fail closed through the boundary validator.

---

### WP2 — Checkpoint activity substrate

**Goal:** Add side-effect-owning activity boundaries for capturing, writing, and
validating checkpoint artifacts.

**Work:**

1. Add typed activity models for workspace checkpoint capture, checkpoint create,
   and checkpoint validation.
2. Implement `workspace.capture_checkpoint` or equivalent on the sandbox queue.
   It must be able to produce at least:
   - `git_commit` for accepted committed state;
   - `git_patch` for candidate diffs against a base commit;
   - `ephemeral_workspace_ref` for live managed-session workspaces when valid.
3. Implement checkpoint artifact persistence with
   `STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE`.
4. Keep `worktree_archive` and `external_state_ref` behind adapter/kind-specific
   support. They may initially be evidence-only if restoration is unavailable.
5. Add an activity wrapper for validation if workflow needs artifact auth,
   existence, corruption, or workspace reachability checks that pure code cannot
   perform.

**Tests:** checkpoint payload requires `planRef` or `planDigest`; `git_patch`
requires `baseCommit` and `patchRef`; checkpoint write is idempotent under
`{stepExecutionId}:checkpoint:{boundary}`; unauthorized/missing/corrupt artifacts
produce canonical failure codes; activity output is compact and ref-only.

**Acceptance:** Checkpoint artifacts can be created and validated without a full
workflow run, and no workflow code reads workspace files or shells out to git.

---

### WP3 — Capture checkpoints at canonical Step Execution boundaries

**Goal:** Wire checkpoint artifact creation into `MoonMind.UserWorkflow`.

**Work:**

Add a single workflow helper such as:

```python
async def _capture_step_execution_checkpoint(
    self,
    logical_step_id: str,
    *,
    boundary: StepExecutionCheckpointBoundary,
    workspace_policy: WorkspacePolicy | None = None,
    step_outputs: Mapping[str, Any] | None = None,
) -> StepCheckpointCaptureProjection | None:
    ...
```

Call it at these boundaries where applicable:

1. `after_prepare` after prepared input refs are known;
2. `before_execution` immediately before mutating work starts;
3. `after_execution` after runtime output refs are recorded;
4. `after_gate` after quality/review gate artifacts are recorded;
5. `before_publication` before PR/Jira/merge/deploy/external handoff;
6. `before_recovery_restoration` before a recovery run restores or mutates a
   workspace.

Update ledger rows and manifests with checkpoint refs:

- `latestStepExecutionCheckpointRef`;
- `stepExecutionCheckpointRefs`;
- `checkpointRefsByBoundary`;
- manifest `workspace.checkpointBeforeRef` and `workspace.checkpointAfterRef`.

Continue reading legacy `stateCheckpointRef` / `workspaceCheckpointRef`, but new
writers should prefer Step Execution checkpoint refs.

**Tests:** successful mutating step writes `before_execution`, `after_execution`,
and terminal boundary checkpoints; failed gated step retains candidate checkpoint
evidence; API returns checkpoint refs, not inline checkpoint payloads; old rows
with only `stateCheckpointRef` still project correctly.

**Acceptance:** New workflow runs produce real Step Execution checkpoint artifacts,
not only runtime-provided checkpoint refs.

---

### WP4 — Validate/apply workspace policies before reexecution

**Goal:** Make workspace policy enforcement checkpoint-backed and fail-closed.

**Work:**

1. Add workflow helper to validate a selected policy against a checkpoint ref
   before launching a reexecution.
2. Add `workspace.apply_policy` or equivalent sandbox activity for
   `restore_pre_execution`, `continue_from_previous_execution`,
   `apply_previous_execution_diff_to_clean_baseline`,
   `start_from_last_passed_commit`, and `fresh_branch_from_source`.
3. Reject policy launch with typed diagnostics when required evidence is missing,
   unauthorized, corrupted, wrong kind, plan-mismatched, or workspace-mismatched.
4. Write a blocked manifest and step check before any agent/tool launch when
   validation fails.
5. Carry trace/log correlation IDs through validation/apply activities for future
   Milestone 14 deep links.

**Tests:** missing checkpoint blocks before launch; patch policy requires valid
`git_patch`; mismatched plan digest returns `plan_mismatch`; missing patch artifact
returns `artifact_missing`; `workspace.apply_policy` is idempotent across activity
retries.

**Acceptance:** Workspace policies are no longer metadata-only for reexecution.
An agent never launches after checkpoint validation failure.

---

### WP5 — Make checkpoint-backed recovery the default operator path

**Goal:** Complete roadmap Milestone 13.1 and Jira `MM-825` by making failed-step
and selected-step recovery restore from validated checkpoint evidence by default.

**Work:**

1. Update recovery payload construction to include source workflow/run, source
   execution ordinal, checkpoint ref, checkpoint boundary, plan ref/digest, task
   input snapshot ref, preserved steps, dependency input signatures, and chosen
   workspace policy.
2. In recovery execution initialization:
   - validate source checkpoint artifact before materializing preserved steps;
   - validate preserved-step output refs and dependency signatures;
   - capture `before_recovery_restoration` in the recovery execution;
   - apply workspace policy from the validated checkpoint;
   - only then create the new local Step Execution with
     `reason = recover_from_failed_step` or selected-step equivalent.
3. Keep full retry as a separate explicit operator action, not fallback behavior.
4. Update Mission Control eligibility diagnostics so disabled recovery buttons
   show typed reasons.

**Tests:** failed-step recovery imports prior steps as preserved, restores
workspace, then starts the failed step as local execution ordinal 1 with lineage
to the source execution; selected-step recovery validates chosen step evidence
before launch; missing checkpoint fails before workspace mutation; missing
preserved output ref fails with `artifact_missing`; changed dependency signature
marks preserved downstream row `requires_revalidation` and blocks reuse.

**Acceptance:** Checkpoint resume is the default recovery path when eligible;
full retry is explicit; no recovery path reconstructs progress from logs.

---

### WP6 — Populate terminal side effects and gate external handoff

**Goal:** Make terminal manifests auditable and ensure external handoff requires
accepted producing-step disposition.

**Work:**

1. Add canonical terminal side-effect aggregation for git effects, artifact
   writes, external effects, publication/PR/Jira/merge/deploy operations,
   provider-account effects, compensation/cleanup records, and retrieval/memory
   effects once WP8/WP9 land.
2. Populate `sideEffects` in terminal manifests, not only in start manifests.
3. Gate PR creation, Jira movement/commenting, merge automation, deployment, and
   provider-account actions on `terminalDisposition == "accepted"` for the
   producing step, in addition to existing MoonSpec gate blocking.
4. Integrate existing outbound scan boundaries rather than inventing a new scan
   mechanism. Add TODO hooks for remaining Milestone 12.4 publish boundaries.
5. Emit governance-ready side-effect records with actor/action/target/decision
   fields so Milestone 12.2 can consume them.

**Tests:** accepted terminal manifest includes git side effect and accepted output
evidence; failed/candidate attempt cannot publish; Jira/PR/merge/deploy handoff
blocks when terminal disposition is not accepted; external idempotent effects
require stable idempotency key; non-idempotent effects require explicit policy or
fail closed; compensation records appear in terminal side effects.

**Acceptance:** Terminal manifests answer "what did this execution change or try
to change?" and all external handoffs are state-gated.

---

### WP7 — Typed gate result contract and risk-review hook

**Goal:** Replace ad-hoc gate verdict strings with the desired structured gate
result and align the gate surface with Milestone 12 risk review.

**Work:**

1. Add `StepGateResultModel` with verdict, confidence, validated refs,
   invalidated refs, remaining work ref, blocking evidence refs, recommended next
   action, target logical step, workspace policy recommendation,
   recoverable-in-current-runtime, and optional policy/risk review refs.
2. Add read-side normalization for old string verdicts and future unknown values.
3. Update MoonSpec/review activity outputs to write typed gate artifacts.
4. Make parent workflow branch only on normalized typed gate results.
5. Add a hook for Milestone 12.5 risk-gated action review: risky actions should
   attach a policy/risk decision ref rather than bypassing the Step Execution
   gate model.

**Tests:** `FULLY_IMPLEMENTED` advances only with accepted output evidence;
`ADDITIONAL_WORK_NEEDED` reexecutes only while budget remains; non-passing
verdicts stop before handoff; legacy string verdict normalizes safely;
unknown/future verdict fails closed with typed degraded decision; prose-only gate
output cannot open publication.

**Acceptance:** Workflow branching is typed and deterministic; no branch depends
on parsing prose summaries or raw strings.

---

### WP8 — Immutable context bundles and retrieval manifests

**Goal:** Stop writing `context={}` and make each Step Execution's visible input
context explicit, immutable, and digest-addressed.

**Work:**

1. Add `StepExecutionContextBundleModel` with workflow/run/step identity,
   execution ordinal, reason, task input snapshot ref, plan ref/digest, prepared
   input refs, workspace policy, checkpoint refs, prior evidence refs, runtime
   selection, quality gate profile, policy refs, retrieval manifest ref, memory
   manifest ref, builder version, and digest.
2. Add builder activity/helper that writes the context bundle as an artifact and
   returns `contextBundleRef`, `contextBundleDigest`, and `builderVersion`.
3. Update manifest start and terminal writers to populate the `context` section.
4. Add retrieval manifest capture for step-scoped context packs, satisfying
   roadmap Milestone 3.2 / 5.3.
5. Ensure context bundles and retrieval manifests carry trace/log/cost correlation
   refs when available.

**Tests:** new manifests never write empty `context={}` for launched executions;
reexecution context includes prior failed attempt evidence refs; changing retrieval
inputs changes context digest; retrieval unavailable is explicit evidence, not
hidden fallback; raw credentials and large logs/diffs are rejected.

**Acceptance:** Step Execution context is auditable and RAG inputs are no longer
ambient runtime state.

---

### WP9 — Memory proposal and promotion manifests

**Goal:** Represent Step Execution memory effects explicitly without making failed
attempts silently modify durable instructions.

**Work:**

1. Add memory promotion states: `proposed`, `accepted_for_run_context`,
   `applied_to_repo`, `rejected`, and `superseded`.
2. Add memory proposal/manifest models with source Step Execution identity.
3. Add `memory.evaluate_proposals` and `memory.apply_policy` activity surfaces or
   adapt existing memory services behind typed activity boundaries.
4. Allow run-local memory promotion only when policy allows.
5. Gate repo-level memory writes behind accepted Step Execution disposition and
   publication/policy gates.
6. Populate terminal `sideEffects.memory`.

**Tests:** failed attempt can create only `proposed` memory; accepted attempt can
promote to run context; repo-level memory write is blocked without accepted
disposition; superseded proposals remain evidence but are not applied; proposal
artifact carries source workflow/run/step/execution ordinal.

**Acceptance:** Memory updates are reviewable side effects, not hidden runtime
learning.

---

### WP10 — Managed runtime checkpoint/resume/fork parity hooks

**Goal:** Keep Step Execution checkpointing compatible with Claude Code managed
session parity and external-provider continuation.

**Work:**

1. Define provider-neutral runtime session evidence fields: runtime ID, managed
   session ID, session epoch, reset/clear action ref, fork source ref, checkpoint
   support level, and external continuation limitations.
2. Ensure Codex managed-session reattempt reset evidence uses the same fields as
   future Claude Code session support.
3. Add adapter capability metadata so checkpoint-backed recovery can distinguish
   strong restore, best-effort evidence, and unsupported restoration.
4. Do not make Claude parity depend on Codex-specific field names.
5. Ensure session epoch/reset markers can be consumed by future live-log timeline
   work.

**Tests:** managed reattempt starts a new session epoch/reset and records
evidence; external-provider continuation records known side effects and available
checkpoint evidence but does not claim strong restoration if unsupported;
unsupported runtime checkpoint restore fails with typed capability diagnostic;
same-attempt activity retry does not create a new semantic session epoch.

**Acceptance:** Step Execution checkpoint contracts are provider-neutral and ready
for roadmap Milestone 1.9d.

---

### WP11 — Observability refs for traces, logs, and per-step cost

**Goal:** Make Step Execution evidence ready for Milestone 14 without coupling
this plan to the full OpenTelemetry rollout.

**Work:**

1. Add optional compact observability blocks to manifests, checkpoints, context
   bundles, gate results, and side-effect records.
2. Include trace/span IDs when present, log slice refs, session epoch/log stream
   refs, estimated/actual token and cost refs, and bounded provider/model/runtime
   labels.
3. Propagate current structured logging correlation context into Step Execution
   evidence where available.
4. Do not block runs when live-log or tracing sinks are unavailable.
5. Ensure APIs expose refs only and leave full trace/log/cost rendering to Mission
   Control / observability surfaces.

**Tests:** evidence includes observability refs when provided; missing tracing/log
transport does not fail execution; per-step cost refs are bounded and do not inline
large billing details; API detail exposes refs, not logs.

**Acceptance:** Every future step row can deep-link to trace/log/cost slices once
Milestone 14 UI/instrumentation lands.

---

### WP12 — Operator remediation panels and queryable audit integration

**Goal:** Connect checkpoint-backed recovery evidence to operator-visible
remediation workflows without implementing autonomous repair yet.

**Work:**

1. Surface checkpoint eligibility, validation failures, preserved-step provenance,
   and selected-step recovery choices in Mission Control remediation panels.
2. Publish remediation lifecycle audit events through the existing control-event
   mechanism when recovery is requested, validated, blocked, restored, or
   completed.
3. Link audit events to Step Execution manifest/checkpoint refs.
4. Keep default workflow detail compact; expose full attempt/checkpoint history in
   expanded surfaces.

**Tests:** operator can distinguish full retry from checkpoint resume; disabled
recovery action shows typed checkpoint reason; remediation audit events are
queryable by target run; UI does not inline full logs/diffs/provider payloads.

**Acceptance:** Milestone 13.2 and 13.3 have the Step Execution evidence they
need.

---

### WP13 — Autonomous story/PRD loop and supervisor gate

**Goal:** Use completed Step Execution primitives for bounded autonomous story
loops, while keeping the broader 3 a.m. autonomous remediation supervisor gated.

**Work:**

1. Compile PRD/story items into logical steps using the existing workflow preset
   and step sequencing system.
2. For each story item, create bounded plan structure: implementation Step
   Execution, typed verification gate, remediation Step Execution(s), verification
   gate after each remediation, and publication only after accepted terminal
   disposition.
3. Use checkpoint policies from WP4 so failed/candidate attempts retain
   diff/checkpoint evidence and later attempts can apply previous diff to clean
   baseline when a gate recommends it.
4. Record at least one non-attempt stop dimension in manifests.
5. Invalidate downstream steps when accepted outputs change.
6. Keep the autonomous remediation supervisor out of scope until Milestone 12.1,
   12.2, 12.3, 14.1, 14.3, and 14.4 are sufficiently shipped.

**Tests:** story attempt 1 fails gate, retains candidate diff, and does not
publish; story attempt 2 applies prior diff to clean baseline, passes, commits,
and advances; budget exhaustion stops before PR/Jira and records remaining work;
downstream step invalidates when upstream accepted output changes; autonomous
supervisor remains disabled or feature-gated when safety/observability
prerequisites are missing.

**Acceptance:** General autonomous story/PRD loops use the same Step Execution and
checkpointing substrate, not a separate retry/remediation system.

---

## 4. Suggested PR order

```text
WP0  Rebaseline conformance harness
  └─► WP1  Consolidate manifest writers
        ├─► WP2  Checkpoint activity substrate
        │     └─► WP3  Boundary checkpoint capture
        │           ├─► WP4  Workspace policy validation/application
        │           │     └─► WP5  Checkpoint-backed recovery default / MM-825
        │           └─► WP6  Terminal side effects + disposition gates
        ├─► WP7  Typed gate result contract
        └─► WP8  Context bundles + retrieval manifests
                ├─► WP9   Memory manifests
                ├─► WP10  Managed runtime parity hooks
                └─► WP11  Observability refs
WP5 + WP6 + WP7 ──► WP12 Operator remediation panels/audit
WP4 + WP7 + WP8 + WP9 + WP10 + WP11 ──► WP13 Story/PRD loop
```

Recommended batching:

1. **PR A:** WP0 + WP1, because everything else depends on the manifest writer
   being single-path.
2. **PR B:** WP2 only, because sandbox/workspace activities are high-risk.
3. **PR C:** WP3 + minimal ledger/API projection for checkpoint refs.
4. **PR D:** WP4 reexecution policy enforcement.
5. **PR E:** WP5 / MM-825 recovery default path.
6. **PR F:** WP6 terminal side effects and handoff gating.
7. **PR G:** WP7 typed gate results.
8. **PR H:** WP8 retrieval/context bundle work.
9. **PR I:** WP9 memory effects.
10. **PR J:** WP10 + WP11 provider-neutral runtime/observability refs.
11. **PR K:** WP12 operator surfaces/audit.
12. **PR L:** WP13 bounded story/PRD loop.

---

## 5. Migration and replay policy

1. New writers are strict. Readers and replay boundaries are forgiving and
   degrade old/future/blank values into typed invalid decisions.
2. Additive `v1` fields remain optional/default-initialized.
3. Renaming/removing fields or changing identity/key shapes requires a new
   content-type version and explicit cutover, not aliasing.
4. Old runs without real checkpoint artifacts must be shown as legacy evidence;
   they may offer explicit full retry, but not checkpoint resume.
5. Do not synthesize checkpoints from logs, UI projections, or prose summaries.
6. Do not backfill terminal side-effect classifications for old runs unless the
   source evidence is already structured and authoritative.

---

## 6. Final definition of done

This gap plan can be deleted when all of the following are true:

1. There is exactly one Step Execution manifest write path.
2. Every launched Step Execution has a context bundle ref and digest.
3. Checkpoint artifacts are written at canonical boundaries.
4. Workspace policies validate and apply checkpoint evidence before reexecution.
5. Failed-step and selected-step recovery restore from validated checkpoints by
   default.
6. Preserved steps are imported only from validated refs and dependency
   signatures.
7. Terminal manifests include side-effect classification for mutating steps.
8. External handoffs require accepted terminal disposition plus typed gate
   approval and applicable policy/outbound-scan decisions.
9. Gate results are structured typed payloads, not ad-hoc strings or prose.
10. Retrieval and memory effects are Step Execution-scoped and artifact-backed.
11. Managed runtime checkpoint/resume/fork evidence is provider-neutral and ready
    for Claude Code managed-session parity.
12. Step Execution evidence carries compact observability refs suitable for
    trace/log/cost deep links.
13. Mission Control exposes checkpoint eligibility, recovery diagnostics,
    preserved provenance, and remediation audit without inlining large evidence.
14. General story/PRD loops use the same Step Execution/checkpoint primitive.
15. In-flight/degraded payload tests pass and fail closed.
16. `docs/tmp/StepExecutionsCheckpointingGapPlan.md` is deleted in the closing PR.

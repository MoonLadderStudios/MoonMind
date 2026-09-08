# Remediation Verification Cadence

**Document class:** Canonical declarative system / feature design.
**Status:** Accepted
**Owners:** MoonMind Platform and dashboard.

Related: [Workflow Remediation](WorkflowRemediation.md), [Step Ledger and Progress](../Temporal/StepLedgerAndProgressModel.md), [Step Executions and Checkpointing](../Steps/StepExecutionsAndCheckpointing.md), [Recovery semantics](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7a-failed-step-recovery-semantics), [Artifact Presentation](../Artifacts/ArtifactPresentationContract.md), and [Workflow Detail](../UI/WorkflowDetailsPage.md).

## 1. Purpose

A remediation attempt addresses the complete latest verification report. One authoritative full verification then checks the resulting candidate. Do not create a full verifier after every small fix by default.

This contract owns bounded repair/verification iteration within a workflow. Cross-workflow administrative actions retain their independent authorization, approval, action ledger, and targeted verification in Workflow Remediation. Both paths share Step Execution, candidate, artifact, runtime, and finalization owners rather than maintaining competing repair engines.

## 2. Problem

An ambiguous sequence such as `Remediate 1 of 6 -> Verify 1 of 6` can be mistaken for one remediation per individual gap. It also obscures whether subsequent attempts retain the previous candidate and whether a failed verifier causes already successful compute to run again.

Attempts, gaps, runtime retries, restoration retries, and verification retries are different units. The UI and the durable state must represent those distinctions without relying on labels alone.

## 3. Desired cadence

```text
Initial verification -> current report and candidate identity
Remediation attempt 1 -> address all safe in-scope gaps -> committed candidate C1
Verification attempt 1 -> verify C1 against the current objective
Additional work, if admitted -> remediation attempt 2 from C1 -> candidate C2
Verification attempt 2 -> verify C2
Terminal success, bounded stop, or explicit escalation
```

One attempt may address many gaps, one gap, or none if mutation is unsafe. Targeted local checks are part of that attempt. A failed or unavailable verifier does not automatically create another repair attempt or repeat successful agent execution.

## 4. Invariants

1. **Completion is evidence-scoped.** A canonical verifier verdict governs its declared scope. `FULLY_IMPLEMENTED` is not a universal certificate for unexecuted live/runtime/security/operator requirements.
2. **The input is the latest authoritative report.** Bind exact `gateResultRef`, `remainingWorkRef`, producing run/step/attempt, objective/Skill snapshot, and candidate identity. Do not infer new requirements from logs or silently use a stale report.
3. **One coherent attempt handles known gaps.** Address all safe in-scope gaps within the admitted budget, and record addressed, deferred, unsafe, and still-failing items explicitly.
4. **Full verification is attempt-scoped.** Local checks do not become sibling full-verifier steps. Independently risky administrative effects still require targeted verification.
5. **Work remains cumulative.** Attempt N+1 starts from N's authoritative committed candidate, not the original clean baseline. Source branch, verified base, candidate head, output branch, and publication state remain separate identities.
6. **Verification does not mutate the candidate it verifies.** Required test scratch/output is isolated under policy. Unexpected source changes invalidate the comparison or require an explicit new candidate. A stale verification result cannot advance a newer head.
7. **Budget and authority persist.** Attempt, action, elapsed-time, token/cost, cooldown, and escalation state survive worker retry, recovery, and Continue-As-New. Only explicitly admitted new work may receive a new budget.
8. **Scope recovery is bounded.** When trusted verification input is marked truncated, try one bounded authorized recovery of complete content. Residual truncation is disclosed. Missing/unreadable authoritative requirements or unrecoverably truncated acceptance criteria prevent a complete determination. Hidden unseen text never becomes an invented requirement.
9. **Evidence tiers remain distinct.** A repo-verifiable Skill can return `FULLY_IMPLEMENTED` for all inspectable in-scope repository requirements while listing manual/deployed/provider checks as exclusions. Those exclusions do not satisfy a parent issue or roadmap gate that requires those checks. Required product acceptance remains open until its own evidence exists.
10. **Optional enrichment is optional.** Unavailable optional RAG or other enrichment is a `NOT RUN` environment note, not automatically `NO_DETERMINATION`. Missing evidence required to establish the actual objective is not optional and cannot be waived by calling it enrichment.
11. **There is one authoritative candidate source.** The capture/persistence owner supplies a workflow-owned cumulative head with checkpoint/content refs, digest, version, and attempt identity. Every attempt and verifier must agree with it. Advance the head only after the required candidate evidence is committed and verified; late or conflicting advances fail rather than overwrite newer progress.

### Checkpointless candidate admission

Where the existing runtime/policy explicitly permits checkpointless continuation, admit it only from the **same repository branch and exact head SHA** that the admitting verifier proved publication-authorized, uncontaminated, and remotely verified after a push or a verified no-change result. Pin the remediation request to that SHA. The next verifier follows the resulting head of that same candidate branch and retains the verified base separately.

Missing, advanced, denied, contaminated, or mismatched evidence stops the attempt before mutation. A branch name, local checkout path, still-running host, or earlier success flag is not an alternative authority. A managed runtime cannot use another run's checkout. An admitted checkpointless attempt still transitions to verification/evaluation instead of leaving the loop stuck in remediation.

This narrowly bounded branch-based path is **not** byte-identical checkpoint Resume or proof of cold restoration. It must be named and observed truthfully. Repository-independent saved-work recovery must satisfy the reviewed checkpoint/durability policy through its existing owners, not silently broaden this exception or synthesize a GitHub credential requirement for unrelated diagnosis.

### Progress evidence ownership

For structured `remainingWork`, the artifact publisher computes `validatedRefs.authoritativeEvidenceDigest` from published entries independent of entry order and stamps `progressEvidenceSchemaVersion=remediation-progress-evidence/v1`. Supplied values for these fields, including plausible stale digests, are replaced by the publisher. The verifier still owns gap semantics and verdict.

Progress uses the validated remaining-work/candidate evidence identity. Repeated verdict text alone is not no-progress. Different timestamps, random IDs, changed wording, or reordered entries are not substantive progress. Full evidence remains artifact-backed; history carries compact digests and refs.

## 5. When immediate verification is still required

Administrative effects on sessions, hosts, leases, locks, helpers, cleanup, target liveness, or repository publication require the relevant action verification. Destructive/high-risk actions and dependent repairs may require an intermediate health check or explicit full gate.

Use the exact action/resource/postcondition contract. A successful pause or lease reconciliation does not prove the original coding objective is completed. Do not replace deterministic authorization, side-effect reconciliation, or secret controls with an LLM verdict.

## 6. Recommended step labels

```text
Verify completion
Remediate verification gaps: attempt 1 of 6
Verify remediation attempt 1 of 6
Remediate remaining gaps: attempt 2 of 6
Verify remediation attempt 2 of 6
```

The numbers describe the admitted attempt budget, not individual gaps. Six is an example, not a universal default. Show gap progress separately, such as four known gaps, three addressed, one deferred. Inactive future attempts are inactive/skipped, never displayed as executing work.

## 7. Artifact model

### 7.1 Remediation attempt artifact

`reports/remediation_attempt-<n>.json`, type `remediation.attempt`, records attempt and budget, input verifier/report refs, objective/Skill scope, source and resulting candidate identities, per-gap status and evidence, changed paths, targeted checks, and the next required verification.

Record an explicit no-change/unsafe/blocked result when no candidate mutation was accepted. Failed compute may still leave saved work, but saving does not turn it into a successful repair attempt.

### 7.2 Verification artifact

`reports/remediation_verification-<n>.json`, type `remediation.verification`, identifies `verifiesAttempt`, input remediation artifact, exact candidate/head version/digest, target objective and verified scope, canonical verdict, remaining work, excluded/not-run requirements, and authoritative evidence refs.

Action verification uses its existing normalized action-outcome contract rather than overloading the full objective verdict. An adapter-returned success mapping is not a verification artifact.

## 8. Runtime state model

Keep durable attempt state under the existing workflow/step owner. The lifecycle remains collecting evidence, diagnosing, acting, verifying, and a terminal resolution/escalation/failure, with explicit supported wait reasons.

Before runtime launch, persist exact source report, candidate, attempt, Skill/configuration, destination and idempotency identities. Before advancing to full verification, commit accepted compute/candidate evidence. Before another remediation attempt, commit the current verifier outcome and the budget/no-progress decision.

An Activity retry retries the same effect. A restore retry continues the same destination reservation. A semantic repair attempt gets a new Step Execution and increments its admitted attempt count. A verification-only retry reuses the candidate without running the repair again.

## 9. Dashboard expectations

Show full verification as an attempt-level gate and targeted checks inside the attempt. Display current candidate, latest authoritative verification, addressed/deferred/unsafe/still-failing gap counts, attempt budget, waiting/escalation reason, and source/result links.

Pending verification is not repaired, failed, or no-change merely because a short polling window ended. Historical failure and verified linked repair can be shown together. A stale result from an earlier attempt cannot replace the selected current candidate or enable the wrong action.

## 10. Planner and orchestration contract

Only activate the next remediation pair when the canonical verdict is `ADDITIONAL_WORK_NEEDED`, budget remains, no-progress/cooldown policy allows it, and candidate/repository/runtime/action authority is valid. Normalize the verdict once through the existing providing contract.

The planner schedules portable named remediation and verification Skills. It must not reimplement their gap classification or repair policy in native Python. Native code owns admission, materialization, evidence validation, bounded scheduling, lifecycle, and safe finalization.

Generic Omnigent execution applies equally to each claimed harness. Preserve admitted Runtime + Profile and subordinate configuration/model/policy across an attempt. A new intended choice is explicit re-admission, not fallback because a provider is busy or a default changed. Session reuse is capability-dependent and independent of preserving workspace content.

## 11. Attempt-scoped artifact requirements

Publish immutable per-attempt evidence through the existing artifact owner. A convenience latest pointer refers to the latest accepted authoritative artifact under the correct candidate/attempt identity, not whichever artifact has the newest timestamp.

A repeated artifact write reconciles committed bytes/digest. A changed payload under the same immutable identity is a conflict. Failure to generate a preview, timeline update, or optional report does not erase a committed valid candidate or cause compute to run again. Missing required output remains incomplete and needs explicit reconciliation.

## 12. Preset and skill instruction requirements

Use an explicitly named canonical remediation Skill, not `auto` selection that may resolve to an empty bundle. Supply the exact latest `gateResultRef` and `remainingWorkRef` as compact direct inputs. The trusted Activity materializes complete permitted bytes outside history and supplies readable `gateResultPath` and `remainingWorkPath` to the runtime.

Instruct remediation to consume all safe current gaps, exercise the actual production boundary requested by the verifier, record per-gap decisions and targeted checks, and stop without mutation when the input verdict is terminal. A test-only dictionary, fabricated success field, or helper mock does not satisfy a requirement about real workflow/Activity/adapter/persistence wiring.

The full verifier checks the resulting whole candidate against the authoritative scope and publishes an attempt-bound result. Presets and schedules use the same contract, not independent loops with subtly different stopping or preservation rules.

## 13. Continuation and terminal handling

- `FULLY_IMPLEMENTED`: accept the verified scope, skip unused repair attempts, and permit only downstream handoffs whose own evidence/policy is satisfied.
- `ADDITIONAL_WORK_NEEDED`: admit the next bounded attempt from the committed current candidate when safe.
- `BLOCKED`: stop or escalate with explicit blocker evidence.
- `NO_DETERMINATION`: stop/escalate, except a separately bounded safe evidence-recovery action permitted by policy. Do not blindly repeat paid implementation.
- `FAILED_UNRECOVERABLE` or environment contamination: stop, preserve evidence, and prevent unsafe publication/issue completion.

Attempt exhaustion preserves the latest candidate, remaining-work artifact, and exact stopped phase. A failed or interrupted loop resumes through the existing phase-aware recovery contract, not by resetting attempts or starting from the initial checkout. If only verification is unfinished, rerun verification only. If publication is unfinished, use its publication/reconciliation owner without another model run.

Partial candidate capture, missing manifest, corrupted/denied content, save failure, lost publication acknowledgment, and optional report failure are distinct outcomes. Capture while the actual workspace owner still has the content, verify the required handoff before deleting its sole copy, and persist bounded retention/cleanup obligations. Do not convert successful compute into a generic failed result merely because later capture/presentation failed.

Continue-As-New carries the exact report, candidate head/version, attempt identities, budgets, active child/operation, approval/lock where applicable, pending verification, and finalization obligations. Recorded historical decisions remain replay-safe through explicit versioning.

## 14. Immediate verification exceptions

Targeted action verification remains mandatory where the action contract requires it. [Workflow Remediation §11.6.1](WorkflowRemediation.md#1161-trusted-post-action-verification-phase) owns `verified_resolved`, `verified_no_change`, `still_failed`, `regressed`, `evidence_unavailable`, `approval_required`, `verification_failed`, and `canceled`.

Keep delivery, pending verification, operational postcondition, and objective repair separate. Verify a linked branch/recovery candidate and exact result identity without rewriting the source failure or requiring that historical row to become completed. A branch completing does not alone prove the objective or any separately requested publication/promotion.

Long-running action verification persists a pending obligation under the existing orchestration/action owner and resumes on authoritative result evidence or bounded durable reconciliation. A short in-process poll, browser presence, or redispatch of the same mutation must not be the only way to finish verification.

## 15. Dashboard and API projection

Project attempt/max-attempts, current phase/wait, candidate identity and head version, latest authoritative verification ref, per-attempt repair/verifier Step Executions, gap/check counts, canonical verdict, scope/exclusions, next-action eligibility, budgets, and remaining operator work from the existing owner.

Do not parse labels to infer phase or use UI selection as runtime authority. Authorization and cache keys include principal, source/result run, and attempt/candidate identity. Late projection updates cannot advance the canonical loop. Unavailable evidence stays unknown rather than fabricated zero gaps or a successful no-op.

## 16. Test expectations

Required production-boundary tests must prove:

- One report with multiple gaps produces one coherent repair attempt and one full verifier, with local checks nested inside that attempt.
- Two or more attempts preserve a real cumulative candidate marker/content from C0 to C1 to C2. The verifier receives the exact latest report and reads the intended candidate, not the clean baseline or another run's checkout.
- Candidate-head updates and progress digests reject stale/concurrent attempts and ignore mere entry ordering or timestamp changes. Substantive progress with repeated verdict labels is not stopped incorrectly.
- Interrupted repair, completed repair with unfinished verification, attempt exhaustion, and interrupted publication resume at the right phase without resetting budgets or repeating accepted upstream steps/effects.
- Valid bounded checkpointless continuation uses the exact authorized remote SHA. Missing/moved/contaminated/unauthorized evidence blocks before mutation and cannot be relabeled as checkpoint restore.
- Every verdict and no-progress/budget/cooldown branch has a deterministic terminal or next-attempt outcome. Missing required evidence cannot produce a synthetic pass; optional enrichment failures remain optional.
- Mutation/verification artifact retry, partial save/manifest commit, report failure, cancellation, worker restart, late result, and cleanup/janitor races preserve source/candidate/result identity and required content.
- A linked repair can be verified while its immutable source remains failed. An unrelated newer source success, stale branch turn, or pending result cannot satisfy the repair.
- Runtime, Profile, model, policy, repository and publication choices remain fixed through retries unless explicitly re-admitted. Qualified-but-busy execution waits without substitution.
- Actual preset/API/workflow/Activity/realizer/artifact/UI wiring is tested with controlled external dependencies and selected by required CI. Existing recorded-history replay and escaped-regression suites remain enforced.

Report repository tests, real-infrastructure qualification, exact-artifact/protected-live evidence, and release promotion separately. A repository-scoped `FULLY_IMPLEMENTED` result with disclosed external exclusions does not close a product gate requiring those missing observations.

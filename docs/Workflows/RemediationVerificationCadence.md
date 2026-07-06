# Remediation Verification Cadence

**Status:** Desired-state design refinement
**Document Class:** System / Feature Design View
**Owners:** MoonMind Platform + dashboard
**Last Updated:** 2026-07-03
**Related:** `docs/Workflows/WorkflowRemediation.md`, `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Steps/StepExecutionsAndCheckpointing.md`, `docs/Artifacts/ArtifactPresentationContract.md`, `docs/UI/WorkflowDetailsPage.md`

---

## 1. Purpose

This document defines the desired verification cadence for MoonMind remediation loops.

The central rule is:

> A remediation attempt should address the complete current verification report, then one authoritative verification step should check the resulting workflow state. MoonMind should not create a full verifier step after every individual remediated gap by default.

This keeps remediation bounded and auditable without exploding the step graph into one remediation/verification pair per small fix.

---

## 2. Problem

The existing remediation UX can imply a sequence like:

```text
Verify completion
Remediate verification gaps 1 of 6
Verify remediation 1 of 6
Remediate verification gaps 2 of 6
Verify remediation 2 of 6
...
```

That shape is ambiguous. It can look like `1 of 6` means "gap 1 of 6", and it can encourage a full verification run after every atomic remediation item.

That is usually the wrong default because each full verification may launch a managed agent, consume slots, write artifacts, block on child workflows, and create another manifest. The operational cost is high, while the value is low when the remediator can safely batch all known gaps from the latest report.

---

## 3. Desired cadence

MoonMind should model remediation as bounded attempts:

```text
Initial verification
  -> verification report with all known gaps

Remediation attempt 1
  -> consumes the latest verification report
  -> fixes every safe known gap within scope and budget
  -> records targeted checks and per-gap actions

Verification attempt 1
  -> runs one authoritative full verification over the resulting state
  -> returns FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, BLOCKED, NO_DETERMINATION, or FAILED_UNRECOVERABLE

If additional work is needed:
Remediation attempt 2
  -> consumes the new verification report
Verification attempt 2
  -> verifies the whole resulting state again
```

The attempt number is the retry-cycle number, not the individual gap number. A single remediation attempt may address one gap, many gaps, or no gaps if all candidate repairs are unsafe.

---

## 4. Invariants

1. **Verification remains the authority for completion.** A target is complete only after the authoritative verifier reports a successful terminal verdict such as `FULLY_IMPLEMENTED` or the remediation-specific equivalent.
2. **Remediation consumes verifier artifacts.** A remediation attempt starts from the latest concrete verification or remediation context artifact and must not invent gaps from logs alone.
3. **Remediation batches known gaps.** The remediator should address all currently known safe gaps in one coherent attempt rather than enqueueing one full verifier step per gap.
4. **Local checks are allowed inside remediation.** A remediation attempt may run targeted tests, lint, static checks, schema validation, or other cheap checks before yielding.
5. **Full verification is attempt-scoped.** The expensive authoritative verifier runs after a remediation attempt, not after every atomic fix.
6. **Budgets are explicit.** Maximum attempts, action budgets, and escalation behavior must be visible and enforced.
7. **Artifacts stay durable.** Each remediation attempt and verification attempt must leave enough evidence to audit what changed and why another attempt did or did not run.
8. **Verification scope is the visible, trusted input.** The verifier's scope is the visible content of the trusted verification inputs (issue brief artifact, spec, assessment backlog). When a trusted input is marked truncated, the verifier or remediator first attempts one bounded recovery of the full content through the trusted MoonMind tool surface; residual truncation is a disclosed scope limitation in the report, not a `NO_DETERMINATION` trigger, unless the acceptance criteria themselves are truncated and unrecoverable. Hidden truncated content never becomes a requirement.
9. **Non-repo-verifiable requirements are disclosed exclusions.** Requirements provable only by manual testing, external deployment, or provider tooling unavailable in the runtime are reported as scope exclusions with reasons. They do not block `FULLY_IMPLEMENTED` and do not force `NO_DETERMINATION` when every repo-verifiable in-scope requirement is verified.
10. **Optional tooling never gates the verdict.** Unavailable or misconfigured optional enrichment tooling (for example MoonMind RAG retrieval failing with `embedding_provider_not_configured`) is recorded as a `NOT RUN` environment note. `NO_DETERMINATION` is reserved for verification targets that cannot be established at all: a missing or unreadable authoritative input, unrecoverably truncated acceptance criteria, or repository evidence for visible in-scope requirements that cannot be inspected.

---

## 5. When immediate verification is still required

MoonMind may insert a verification or health-check boundary inside a remediation attempt when the next repair decision depends on it or the action has independent safety risk.

Examples:

- a side-effecting administrative action changes target liveness, locks, sessions, containers, or provider-profile slots;
- a destructive or high-risk action requires confirmation before any later action can safely proceed;
- a migration, branch promotion, publish action, or external side effect has to be confirmed before continuing;
- the next remediation step needs proof that an environmental repair succeeded;
- policy requires an approval gate or verification gate for a specific action kind.

These checks should usually be targeted health checks or action verification artifacts, not a full verifier pass unless the policy explicitly calls for a full verifier.

---

## 6. Recommended step labels

Avoid labels that make attempts look like individual gaps. Prefer:

```text
Verify completion
Remediate verification gaps — attempt 1 of 6
Verify remediation attempt 1 of 6
Remediate remaining gaps — attempt 2 of 6
Verify remediation attempt 2 of 6
```

The UI should display both:

- attempt progress, for example `attempt 2 of 6`;
- gap progress inside the attempt, for example `4 known gaps, 3 addressed, 1 deferred`.

Do not label a step `Remediate verification gaps 1 of 6` unless the UI makes clear that `1 of 6` means the remediation attempt budget rather than the first gap.

---

## 7. Artifact model

A remediation loop should produce attempt-scoped artifacts.

### 7.1 Remediation attempt artifact

Recommended path:

```text
reports/remediation_attempt-<n>.json
```

Recommended artifact type:

```text
remediation.attempt
```

Representative shape:

```json
{
  "schemaVersion": "v1",
  "attempt": 1,
  "maxAttempts": 6,
  "inputVerificationRef": { "artifact_id": "art_verification_initial" },
  "knownGaps": [
    {
      "gapId": "gap-1",
      "source": "verification_report",
      "status": "addressed",
      "evidenceRefs": [{ "artifact_id": "art_targeted_test" }]
    },
    {
      "gapId": "gap-2",
      "source": "verification_report",
      "status": "deferred",
      "reason": "requires policy approval"
    }
  ],
  "changedFiles": ["path/to/file.py"],
  "targetedChecks": [
    {
      "command": "pytest tests/unit/example_test.py",
      "status": "passed"
    }
  ],
  "nextVerificationRequired": true
}
```

### 7.2 Verification artifact

Recommended path:

```text
reports/remediation_verification-<n>.json
```

Recommended artifact type:

```text
remediation.verification
```

The verification artifact should identify the remediation attempt it verifies and should contain the authoritative verdict for the whole target state, not just the last atomic fix.

---

## 8. Runtime state model

The existing remediation lifecycle can continue to use these phases:

```text
collecting_evidence -> diagnosing -> acting -> verifying -> resolved/escalated/failed
```

The `acting -> verifying -> diagnosing` loop is attempt-scoped:

```text
acting(attempt=1) -> verifying(attempt=1) -> diagnosing(attempt=2)
```

It is not per-gap by default:

```text
acting(gap=1) -> verifying(gap=1) -> acting(gap=2) -> verifying(gap=2)
```

When the verifier returns `ADDITIONAL_WORK_NEEDED`, MoonMind should create the next remediation attempt only if the attempt budget remains and the failure is safe to remediate. `BLOCKED`, `NO_DETERMINATION`, and unrecoverable failures should stop or escalate with explicit evidence.

---

## 9. Dashboard expectations

The Workflow detail timeline should make remediation loops understandable at a glance:

- show the full verifier as an attempt-level gate;
- show targeted checks inside the remediation attempt details rather than as peer full-verifier steps;
- summarize known gaps as addressed, deferred, unsafe, or still failing;
- show the latest authoritative verification artifact as the completion authority;
- distinguish action verification from full completion verification;
- preserve the attempt budget and remaining attempts in the step details.

For example:

```text
Remediate verification gaps — attempt 1 of 6
  6 known gaps
  5 addressed
  1 deferred: requires approval
  targeted checks: 4 passed, 0 failed

Verify remediation attempt 1 of 6
  verdict: ADDITIONAL_WORK_NEEDED
  remaining gaps: 1
```

---

## 10. Acceptance criteria for implementation

This design is implemented when:

1. The workflow planner creates one full verification step after each remediation attempt, not after each individual gap by default.
2. Remediation attempt numbering is distinct from gap numbering in backend state, artifacts, and dashboard labels.
3. The remediator receives the latest verification report and is instructed to address all safe known gaps in one attempt.
4. Targeted local checks can be recorded inside `remediation.attempt` artifacts.
5. Full verifier outputs are recorded as `remediation.verification` artifacts and identify the attempt they verify.
6. Policy can still require targeted action verification or full verification for specific high-risk action kinds.
7. The dashboard renders attempt-level progress and per-gap details without implying a full verifier per gap.
8. Tests cover multi-gap remediation, remaining-gap retries, blocked/no-determination escalation, and high-risk action verification exceptions.

---

## 11. Design rule summary

Keep these rules stable as MoonMind evolves:

1. **Batch known safe gaps into a remediation attempt.**
2. **Run one authoritative full verifier after the attempt.**
3. **Use targeted checks inside the attempt for cheap or safety-critical confirmation.**
4. **Create another attempt only from a new verifier report.**
5. **Make attempt numbering, gap counts, budgets, and verifier authority explicit in the UI and artifacts.**

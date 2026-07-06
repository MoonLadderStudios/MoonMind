# Remediation Verification Cadence

**Status:** Desired-state design refinement and implementation plan
**Document Class:** System / Feature Design View
**Owners:** MoonMind Platform + dashboard
**Last Updated:** 2026-07-06
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

## 10. Implementation review as of 2026-07-06

The current repository has a useful partial implementation, but it does not yet satisfy this design as a system contract.

What is already present:

- The orchestrate presets already contain remediation and post-remediation verification pairs for multiple attempts.
- Those steps already carry attempt-oriented annotations such as `moonSpecRemediationAttempt` and `moonSpecRemediationMaxAttempts`.
- The remediation instructions already tell the worker to consume the latest verifier report and treat `ADDITIONAL_WORK_NEEDED` as the bounded remediation input.
- The verifier instructions already preserve `ADDITIONAL_WORK_NEEDED` for the next attempt and stop for hard terminal states.

What remains incomplete:

- The visible preset labels still look like gap ordinals, for example `Remediate verification gaps 1 of 6` and `Verify remediation 1 of 6`.
- The preset step graph still materializes every remediation pair up front. Later attempts rely on instruction-level skipping rather than a planner/orchestrator decision that creates or activates the next attempt only after `ADDITIONAL_WORK_NEEDED`, budget availability, and safety checks.
- Attempt artifacts are not first-class. The repository still needs a durable `remediation.attempt` artifact schema, writer, and reader that records known gaps, statuses, changed files, targeted checks, and the input verification artifact.
- Post-remediation verification artifacts are not attempt-scoped. The verifier should write `remediation.verification` artifacts that identify the remediation attempt they verify instead of only updating a generic verifier output path.
- The backend state model needs an explicit remediation cadence view so attempt numbering, max-attempt budget, gap IDs, targeted checks, and authoritative verification refs are persisted separately from the plain workflow step title.
- The dashboard needs a rendering path for attempt progress, nested gap status, targeted checks, action verification, and latest authoritative completion verification.
- Existing tests still assert the legacy labels and static step expectations, so they protect the incomplete implementation instead of this desired cadence.

A fix that only renames the current preset titles is not enough. Label cleanup is necessary, but the acceptance criteria require durable attempt state, attempt-scoped artifacts, authoritative verification refs, conditional continuation, and dashboard rendering.

---

## 11. Remaining-work implementation plan

### 11.1 Planner and orchestration contract

Introduce a small remediation cadence model at the workflow planning/orchestration boundary.

Recommended fields:

```json
{
  "cadence": "attempt_scoped_remediation_verification",
  "attempt": 1,
  "maxAttempts": 6,
  "sourceVerificationArtifactId": "...",
  "latestVerificationArtifactId": "...",
  "knownGapCount": 6,
  "terminalPolicy": {
    "fullyImplemented": "advance",
    "additionalWorkNeeded": "next_attempt_when_budget_and_policy_allow",
    "blocked": "stop_or_escalate",
    "noDetermination": "stop_or_escalate",
    "failedUnrecoverable": "stop_or_escalate"
  }
}
```

The planner should derive the next remediation attempt from the latest verifier artifact, not from an individual gap. It should create or activate the next remediation pair only when the authoritative verifier returns `ADDITIONAL_WORK_NEEDED`, the attempt budget remains, and remediation policy says the remaining work is safe. If the first implementation uses static preset expansion for compatibility, the runtime still needs an explicit skip/activation state so the UI does not present unneeded future attempts as active work.

### 11.2 Attempt-scoped artifacts

Add schemas and writers for two durable artifact types:

1. `remediation.attempt`
   - path: `reports/remediation_attempt-<n>.json`
   - written by the remediation step
   - includes the input verifier artifact ref, all known gaps consumed from that report, per-gap status (`addressed`, `deferred`, `unsafe`, `still_failing`), changed files, targeted checks, and `nextVerificationRequired`
2. `remediation.verification`
   - path: `reports/remediation_verification-<n>.json`
   - written by the full verifier step after an attempt
   - includes `verifiesAttempt`, the input remediation attempt artifact ref, the whole-target verdict, remaining gaps, and the verifier evidence refs

The existing generic verifier artifact can remain as a latest-pointer or compatibility artifact, but it must not be the only durable record for post-remediation verification.

### 11.3 Preset and skill instruction updates

Update the affected issue orchestration presets so their step titles and instructions use attempt-oriented wording:

```text
Remediate verification gaps — attempt N of M
Verify remediation attempt N of M
```

For attempts after the first, prefer:

```text
Remediate remaining gaps — attempt N of M
Verify remediation attempt N of M
```

Each remediation step should explicitly instruct the worker to:

- read the latest verifier artifact before changing anything;
- address all safe known gaps from that report in one bounded pass;
- defer or mark unsafe gaps with evidence instead of silently skipping them;
- record targeted local checks inside the remediation attempt artifact;
- avoid creating sibling full-verifier steps for local checks;
- stop without code changes when the latest verifier verdict is terminal.

Each post-remediation verifier step should explicitly verify the whole target state and write a `remediation.verification` artifact that references the remediation attempt artifact.

### 11.4 Continuation and terminal handling

Normalize verifier outcomes once at the cadence boundary:

- `FULLY_IMPLEMENTED`: mark the latest verification artifact as the completion authority, skip remaining remediation attempts, and allow the downstream handoff.
- `ADDITIONAL_WORK_NEEDED`: create or activate the next remediation attempt only when budget remains and policy says the work is safe.
- `BLOCKED`: stop or escalate with the blocking evidence; do not loop.
- `NO_DETERMINATION`: stop or escalate with the missing-evidence reason unless the policy explicitly marks the evidence recovery as safe targeted remediation.
- `FAILED_UNRECOVERABLE` and environment-contamination verdicts: stop, classify the failure, and prevent PR or issue handoff.

This logic belongs in the planner/orchestrator layer, not only in natural-language step instructions.

### 11.5 Immediate verification exceptions

Keep support for immediate verification inside a remediation attempt, but model it as targeted action verification unless policy requires a full verifier. The artifact record should distinguish:

- targeted health checks;
- action verification for side-effecting or high-risk operations;
- policy-required full completion verification.

Examples include provider-profile slot changes, container/session/liveness operations, destructive actions, migrations, branch promotion, publish actions, and environmental repairs whose result determines the next safe remediation decision.

### 11.6 Dashboard and API projection

Extend the workflow detail API projection so the dashboard can render remediation cadence explicitly instead of parsing meaning from labels alone.

Recommended UI projection:

```json
{
  "remediationCadence": {
    "attempt": 2,
    "maxAttempts": 6,
    "status": "verifying",
    "latestAuthoritativeVerificationArtifactId": "...",
    "attempts": [
      {
        "attempt": 1,
        "remediationStepId": "...",
        "verificationStepId": "...",
        "knownGaps": { "total": 6, "addressed": 5, "deferred": 1, "unsafe": 0, "stillFailing": 1 },
        "targetedChecks": { "passed": 4, "failed": 0, "notRun": 0 },
        "verificationVerdict": "ADDITIONAL_WORK_NEEDED"
      }
    ]
  }
}
```

The Workflow detail timeline should render the remediation and full verification steps as peers at the attempt level, while targeted checks and per-gap details appear inside the remediation attempt details.

### 11.7 Test plan

Add or update tests at the same layer where each behavior is enforced.

Required coverage:

- one verifier report with multiple gaps produces one remediation attempt and one full verification step;
- a single remediation attempt can mark multiple gaps addressed, deferred, unsafe, or still failing;
- targeted local checks are captured in the remediation attempt artifact and do not appear as sibling full-verifier steps;
- post-remediation verification writes a `remediation.verification` artifact that identifies the attempt it verifies;
- `ADDITIONAL_WORK_NEEDED` creates or activates a next attempt only when budget remains and policy allows it;
- attempt exhaustion stops with remaining-work evidence;
- `BLOCKED`, `NO_DETERMINATION`, and unrecoverable failures stop or escalate rather than looping;
- high-risk action policies can require targeted action verification or policy-required full verification;
- preset expansion and dashboard rendering use attempt-oriented labels and keep gap progress separate from attempt progress;
- the latest authoritative verification artifact is visually and API-distinct from local targeted checks.

Affected tests should include the preset expansion assertions that currently expect legacy labels, backend cadence/continuation unit tests, artifact schema tests, and frontend workflow-detail rendering tests.

---

## 12. Implementation priority

1. **Backend contract first.** Add the cadence state, artifact schemas, and continuation decision tests before UI polish. This prevents the issue from being solved only as a label change.
2. **Preset compatibility second.** Update Jira, GitHub issue, and provider-neutral issue implementation presets to use attempt labels and attempt artifact paths while preserving existing inputs.
3. **Verifier/remediator artifact writing third.** Ensure the remediator writes `remediation.attempt` and the full verifier writes `remediation.verification` with an explicit `verifiesAttempt` link.
4. **Dashboard projection fourth.** Add API/UI rendering for attempt progress, per-gap status, targeted checks, and latest authoritative verification.
5. **End-to-end acceptance last.** Exercise a multi-gap report through successful batched remediation, remaining-gap retry, blocked/no-determination stops, and a high-risk action verification exception.

This order is deliberate: the durable state and artifact contract are the source of truth; labels and dashboard rendering should consume that contract rather than becoming the contract themselves.

---

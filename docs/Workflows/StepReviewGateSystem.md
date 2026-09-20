# Step Approval Policy System

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-20  
**Related:** [Workflow Publishing](WorkflowPublishing.md), [Workflow Step System](WorkflowStepSystem.md), [Workflow Architecture](WorkflowArchitecture.md), [PR Resolver](../Steps/SkillGithubPrResolver.md), [Skill System](../Steps/SkillSystem.md)

## Purpose and ownership

An enabled step approval policy evaluates a completed plan step against its
original inputs using the configured reviewer. The review does not redefine the
requested outcome, provide new execution authority, or replace the selected
Skill's acceptance policy. MoonSpec owns implementation verification semantics.
MoonMind owns execution, artifacts, budgets, publication, and continuation.

The existing `step.review` Activity owns reviewer invocation and response repair.
The workflow owns the step ledger, semantic remediation, committed decisions,
and terminal policy. Existing PR-resolution and issue-finalization owners keep
those responsibilities. Do not add another approval service, retry controller,
status registry, or certification engine.

This is a desired-state contract. Its continuation requirements do not establish
that every runtime, publication path, or deployed Skill already implements them.
Execution evidence and implementation gaps belong in PRs and run artifacts.

## Configuration

An omitted plan `policy.approval_policy` remains distinct from an explicit policy,
including `enabled: false`. Plan policy takes precedence when present. Otherwise,
the workflow's `initialParameters.approvalPolicy` applies, followed by
`MOONMIND_APPROVAL_POLICY_DEFAULT_ENABLED`, defaulting to disabled. Do not turn an
explicit disabled policy into an enabled default.

The existing policy carries `enabled`, `max_review_attempts`, `reviewer_model`,
`review_timeout_seconds`, and `skip_tool_types`. `max_review_attempts` counts
retries after the initial review, not total executions. Workflow/API projections
retain their existing camelCase field names. No extra report-repair toggle is
needed.

`repo.publish` and `codex.execute` remain default exclusions from blind step
re-execution. Exempting a step from auxiliary review does not waive its own
publication evidence, approval, or side-effect requirements.

## Reviewer authority and evidence

`ConfiguredStepReviewer` uses the existing deployment chat provider, enablement,
credential, and model settings. Omitted and `default` models resolve through that
route. An explicit model is not silently replaced. A disabled, unsupported,
uncredentialed, or changed route does not authorize fallback to another provider.
Credentials never come from workflow payloads.

The Activity sends bounded supplied evidence, not unrestricted artifact references
that it pretends to have fetched. A successful process, a completed step, a model
claim, or a report URL alone does not prove acceptance. The owning reader must
supply the actual required evidence before a reviewer can evaluate it.

Original issue scope and constraints control implementation acceptance. Related
epics, old assessments, and previous reports are context. They do not add unrelated
acceptance obligations. Separate production operations and physical-event release
qualification from implementation unless the selected scope explicitly owns them.
Required runtime, rendered, or other tests cannot be waived merely because the
current agent lacks their toolchain. An AI visual judgment must use the actual
images and rubric through the repository's declared proof owner.

Input sections remain bounded to 64,000 bytes, the complete prompt to 256,000
bytes, and responses to 64,000 bytes. Feedback and finding limits stay enforced
before acceptance. Secret-shaped input fields are redacted before provider send.
The supported review timeout is 1 through 120 seconds; out-of-range requests are
rejected, not silently clamped. Provider output-token ceilings remain unchanged.

## Structured report repair

A malformed reviewer response is a report-production defect, not proof that the
business step failed or that a person must review it.

After a bounded malformed response, the Activity may make one report-only repair
request to the same configured reviewer. Both calls share one total timeout and
one review-attempt/evidence identity. The second request does not replenish the
semantic implementation budget or enable another business-step execution. A valid
non-passing report is not retried here to shop for approval.

Use the existing canonical gate parser. Reject ambiguous duplicate JSON fields,
non-finite values, malformed booleans, invalid verdict/action combinations, and
oversized evidence without silently inferring a passing verdict. Preserve any
recognized non-passing verdict and explicit stop during repair. The repair prompt
carries the original evidence and bounded prior response as untrusted data, not
instructions. It cannot authorize changing code, dropping requirements, inventing
evidence, weakening assertions, or changing the reviewer route.

Oversized responses, provider/permission failures, and requests that cannot fit the
repair prompt budget remain non-passing. Do not truncate required evidence or
reset the timeout to force another attempt. Cancellation propagates normally.
An unsuccessful repair retains a distinct report diagnostic and the candidate.

`reviewProvenance.reportRepair` records the count, original response digest, reason,
and outcome when repair is attempted. It does not store raw provider responses or
credentials. The workflow's existing gate-result artifact remains the durable
owner; this is additional evidence, not a parallel receipt store.

## Verdicts and continuation

Use the existing canonical verdict and action vocabulary:

| Evidence result | Continuation |
| --- | --- |
| `FULLY_IMPLEMENTED` | `advance` for the verified scope only |
| `ADDITIONAL_WORK_NEEDED` with actionable implementation work | Existing bounded remediation owner |
| `NO_DETERMINATION` with obtainable new evidence | Evidence collection/review against the preserved candidate |
| Required capability currently unavailable | `blocked`, with the prerequisite and resumption check |
| Actual human-only information or authorization | Explicit `needs_human`, naming that decision |
| `FAILED_UNRECOVERABLE` | Preserve evidence and stop under existing policy |

New unavailable-review Activity results use `NO_DETERMINATION`, zero confidence,
and explicit `blocked`, rather than manufacturing `needs_human`. A fresh valid
inconclusive report without an action supplies `reattempt_current_step` only when
its current-runtime recovery flag is true; otherwise it supplies `blocked`.
Explicit valid human and blocked decisions remain intact.

Missing evidence is not accepted because confidence is low or no assertion ran.
Optional diagnostics remain optional according to the selected acceptance policy,
not by relabeling a mandatory missing check. Neither `failure_mode: CONTINUE` nor
a draft PR turns incomplete verification into successful acceptance.

Blocked means the current attempt must not repeat an unchanged prerequisite. It
does not mean that the candidate is abandoned to a human. An authorized later
continuation may resume when the prerequisite or controlling evidence changes.
A timeout or unavailable reviewer must not rerun a completed mutation merely to
obtain another review. User cancellation, denied authority, and explicit holds
remain stops and are not converted into automatic continuations.

## Automation-owned candidate and PR continuation

Every incomplete verification handoff identifies the original issue/scope, exact
candidate, last truthful verdict, completed and missing checks, evidence references,
consumed budgets, current owner, and smallest next authorized action. Preserve the
complete report in the existing artifact store and include a bounded actionable
summary in GitHub. An opaque artifact ID or “operator review required” is not a
sufficient next step.

Before declaring execution unavailable, discover the repository's supported
CI, container, or qualified workstation path. Inspect terminal evidence from an
already-submitted job before launching a duplicate. A container in another service
can satisfy an authorized execution requirement even when the agent has no local
Docker executable. Do not grant new credentials or broaden runtime capabilities.

When publication is authorized and required to start CI, the publication owner may
preserve a coherent candidate on the existing PR under the admitted draft policy.
This is a checkpoint/evidence handoff, not completion, permission to merge, or a
request for mandatory manual review. Explicit publication `none` stays `none`.
Continue the same PR from its validated current head; never create another PR to
escape a failed gate or exhausted budget. Keep its actual issue/change title.

The existing continuation owner consumes new post-publication check and artifact
results, distinguishes code failures from reporting or infrastructure failures,
and re-enters only the necessary implementation or verification phase. CI success
alone cannot satisfy unexecuted rendered, packaged, or other source requirements.
The selected verifier decides coverage from actual evidence.

Bind evidence to the candidate content, original scope, required tests and target,
plus relevant locked content, package, topology, or render surface when the source
requires them. A moved PR head invalidates affected evidence. A duplicate event for
the same job/attempt is idempotent, not another implementation attempt. Resume from
the retained source/PR/candidate, not a fresh issue search or a default branch.

Use the existing workflow and issue-attempt budgets, cooldowns, and stopped-writer
checks. Report repair, evidence retrieval, semantic remediation, and transport
retry are distinct work. None may reset another's exhaustion counter. A new
continuation is authorized through the existing owner, not implied by prose or
merely renaming an exhausted attempt. When no safe continuation exists, preserve
an actionable blocked handoff without claiming it has been scheduled.

Successful re-verification updates the same PR's verification summary and may
remove its automation-owned draft state under the existing publication policy.
It does not remove an explicit user hold, satisfy branch-protection approvals,
authorize deployment, or merge without separate merge authority. Issue completion
continues to require the actual intended target evidence, not candidate approval.

## Durable evidence and replay

Review provenance binds provider/model, `evidenceDigest`, `reviewAttemptIdentity`,
review attempt, and timeout policy. Record large evidence in the existing artifact
store and expose a bounded step-ledger check and artifact link in the dashboard.
Diagnostics must remain readable without another LLM invocation. Distinguish
implementation failures, report defects, missing evidence, infrastructure failures,
and real human decisions rather than displaying all as manual review.

The Activity's bounded in-process committed-review cache is only a duplicate-delivery
hint. The persisted workflow gate and step ledger own durable decisions. Worker
loss before the first result is committed may repeat the review; it must not repeat
the business mutation or claim an exactly-once provider call. Reuse only a matching
committed decision. Unavailable results are not accepted cache entries.

Keep nondeterministic calls, clocks, filesystem operations, and network access in
Activities or existing external services. Historical results retain their recorded
meaning. New producer results can carry explicit recovery decisions without
changing historical parser defaults. Changes to workflow command scheduling require
replay coverage or the existing versioned migration mechanism.

## Consumer adoption and validation

MoonSpec assets are owned upstream. Adopt them through the existing pinned bundle
and projection process, not independent edits of generated Skills. Refresh the
active Skill snapshot and verify the normal runtime resolver uses the intended
instructions and helper. Loaded path/digest evidence is diagnostic provenance,
not another exact-version compatibility gate. A merged upstream PR alone does not
update a running deployment.

Test the production review Activity and configured provider adapter with isolated
wire fixtures: malformed-to-valid repair, repeated malformed responses, preserved
non-pass/stop decisions, changed routes, size limits, shared timeout, cancellation,
redaction, duplicate delivery, and unavailable reviewer outcomes. Preserve tests
that reject empty or stale evidence and unauthorized advancement.

At the workflow boundary, exercise candidate publication followed by later CI
completion, evidence-only resumption on the same PR, moved-head rejection, duplicate
completion events, bounded exhaustion, and cancellation. The same-PR journey must
execute the actual publisher, evidence reader, and continuation owner; a helper
unit test or descriptive policy does not prove that end-to-end behavior. Broader
validation belongs in existing GitHub Actions lanes. No human-review checkpoint is
added to implementation acceptance.

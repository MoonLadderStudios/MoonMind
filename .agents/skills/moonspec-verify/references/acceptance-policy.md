# Acceptance policy

This is the portable semantic policy for assessment, implementation verification,
evidence reuse, and completion. Presets supply the original scope, constraints,
candidate, completion target, and evidence; they do not redefine this policy.

## Scope and obligations

Resolve the baseline from original implementation instructions, the selected
canonical source, or the issue brief. Derived plans, assessment backlogs, previous
verifier reports, and process instructions are context, not new requirements.
Preserve source content and stable requirement IDs. Recheck prior gaps against
current code; previously met requirements remain regression constraints.

Honor explicit exclusions and separately owned release work. Referencing a parent
epic or architecture does not import its entire acceptance checklist into a child
story. Record applicable prerequisites and excluded release obligations before
running checks. A physical-event rehearsal, production authorization, or broader
epic closure gates this verification only when the selected source explicitly
requires it here. Do not silently rewrite a mixed or conflicting source to obtain
a pass; return the precise scope conflict through the existing reconciliation path.

Classify obligations by their source, before checking tool availability:

- Optional enrichment improves context. Missing retrieval is a disclosed limitation.
- Optional diagnostics provide additional confidence. Their absence does not gate
  acceptance, but an observed in-scope defect still requires remediation.
- Mandatory implementation acceptance requires objective evidence, including the
  checks required by the source and repository guidance. Missing tools do not
  turn these obligations into exclusions. Prepare authorized isolated test
  dependencies and complete independent safe work before reporting a blocker.
- Separately authorized production execution is a different operation. Verify
  implementation through isolated migration, browser, or workflow tests without
  deploying or mutating production. Preserve a production prerequisite when the
  selected operation explicitly requires it (for example draining live handlers
  before their removal). Do not infer deployment authority from implementation.

Recover truncated mandatory source content through its authorized owning reader.
When recovery cannot supply it, assess the visible scope and identify the missing
source fields. Never fabricate a requirement from a truncation marker. Set scope
`complete: false`; do not certify the unseen whole issue or complete it. An absent
optional MoonSpec packet is not a gap when a usable original baseline exists.

## Source-bound verification map and feasibility

Before expensive implementation, builds, exports, or specialized execution, extend
the existing assessment `verificationMap`; do not create a second acceptance
framework. Each row records `id`, `sourceRef`, `sourceDigest`, `sourceLocator`,
`classification` (`mandatory`, `optional`, or `separate_release`), and
`requiredBehavior`. Source reference/digest match the independently captured
original scope, and the locator identifies the actual requirement within it.
Mandatory IDs must match `scope.requirementIds` exactly. Preserve any existing
`mandatory` boolean consistently; a prior assessment cannot expand or waive scope.

When correcting invented historical IDs, preserve `scopeReconciliation` in the
current snapshot and acceptance binding: `previousScope` is the unchanged prior
scope, `currentScope` is the independently captured scope, and
`removedRequirements` contains each removed `id`, concrete `reason`, and current
`sourceLocator`. Account for every removed ID exactly once. The host compares
provenance to the actual old assessment and independently read source; the
verifier owns semantic classification and completeness. No human approval is
needed merely to retire an invented or inherited gate. Removing an explicit
source requirement is still invalid. Optional `sourceQuote` is an exact substring
of the original source and participates in the obligation digest.

A mandatory row also records `executableChecks`, source-allowed `proofMethods`,
and the existing execution `owner`. Include `target`, `profile`, `topology`, and
`samplingPolicy` only when the source requires them. These are nonblank strings;
methods and commands are nonempty string lists. Record a real required render,
live-provider, protected-request, or target profile precisely. A fake backend,
generic CI pass, or static test cannot replace it. Retain all required samples:
a failing first sample cannot be discarded just because a later one passed.
Do not invent a physical computer, human operator, browser, production credential,
or full-content export gate. Reconcile an explicitly physical-only requirement
that conflicts with the authorized automated implementation lane; preserve the
unproven product/release claim instead of silently calling it passed.

Free-form quality suggestions and `suggestedEvidence` are strictly advisory.
They cannot add a mandatory row, create remaining work, veto publication, or
trigger a retry. A concrete observed defect still blocks its actual source row.
Documentation-only work uses its source-required prose/link/repository checks;
it does not inherit gameplay or GPU execution or close separate implementation.

Record each mandatory row's current `capability` probe as `status` (`ready`,
`busy`, `unavailable`, or `unauthorized`), `evidenceRefs`, and a useful
`resumeCheck`. When relevant, `prerequisites` is a list of similarly probed records
with stable `id` and existing `owner`. The inventory below must contain actual
probe bytes. Discover existing authorized test/CI/content-authoring owners;
missing local tooling alone does not mean no owner is available. Probe cheaply
before spending execution budgets. Separate an isolated test namespace from a
production signer or live execution authority. Do not repeat root content sync
when a specific content-authoring dependency is missing.

The helper's `admission --current current.json [--previous admission.json]`
returns `readyRequirementIds`, `waitingRequirementIds`, `blockedRequirementIds`,
`fingerprint`, `madeProgress`, and diagnostics. `--previous` is the prior admission
result, not a prior assessment. Run independent ready rows; busy work stays with
its existing owner, and unavailable/unauthorized rows await their actual resume
condition. Missing or unresolved probes are never presumed ready. The fingerprint
uses source, content, obligations, and readiness; changing a report ID, timestamp,
or probe artifact while readiness stays unchanged is not implementation progress.
A changed evidence inventory can still resume evidence-only verification. This
helper does not dispatch work, consume/reset attempts, or authorize any action.

## Assessment and verification

Initial assessment is a bounded inspection. It may report `FULLY_IMPLEMENTED` to
mean implementation appears present, but this does not prove tests passed or work
landed. Preserve the initial assessment artifact and verdict unchanged. Later
verification has its own artifact and cannot rewrite that historical meaning.

Objective verification may return `FULLY_IMPLEMENTED` only when the complete
selected scope and every mandatory requirement have valid evidence. Use the
existing verdicts and continuation actions. Record implementation defects,
missing verification, unavailable environment, budget exhaustion, and a human
decision separately. Bounded authorized fixes go to the existing remediation
owner; new obtainable evidence goes to evidence retry. Exhausted attempts retain
the last truthful verdict. `needs_human` is reserved for information or authority
only a human can supply, not elaborate tests or an unchanged unavailable service.

Bind objective evidence in the existing `validatedRefs.acceptance` field using
`acceptance/v1` (see the supplying contract in
`moonmind/workflows/skills/acceptance_contract.py` when hosted by MoonMind):

- `subject`: repository identity, actual revision, and content digest. Capture a
  dirty candidate's content/checkpoint identity; HEAD alone is insufficient.
- `scope`: source reference, digest of original source and constraints, complete
  scope flag, and stable mandatory requirement IDs. For issue-driven work,
  `sourceRef` is the original canonical issue reference (for example
  `owner/repo#123` or `ENG-123`); the source digest includes the complete selected
  source content and constraints, including any controlling canonical document.
- `completionTarget`: intended ref, its observed revision and content digest.
  Resolve an explicitly configured target first, otherwise the remote default
  branch. A feature branch's upstream is not the default/completion target.
- `evidence`: records with mandatory `requirementId` and `evidenceRefs`. A success
  accounts for every required row exactly once; a non-pass may preserve passed
  rows while source-linked remaining work accounts for unresolved rows.
- `freshness`: the required policy identity and optional expiry for time-sensitive
  evidence. Content-bound evidence need not expire arbitrarily.

For source-bound structured work, carry the map in
`validatedRefs.acceptance.verificationMap`. Extend the same `acceptance/v1`
binding with `evidenceSet` (reference to SHA-256 digest of actual inspected bytes)
and `evidenceSetDigest` (SHA-256 of its sorted, compact UTF-8 JSON, with Unicode
preserved). The authorized artifact owner supplies this inventory. Never invent
it from artifact metadata, URLs, a green aggregate, or a prior verifier assertion.

Each objective row also has `status` (`VERIFIED`, `PARTIAL`, `MISSING`, `CONFLICT`,
`UNVERIFIED`, `NO_DETERMINATION`, or `FAILED`) and `obligationDigest`. A verified
row has a `proofMethod` from the source row's allowed methods and matching values
for each source-declared target/profile/topology/sampling policy. Its
`testedSubject` records the actual tested repository/revision/content; omitted
means the enclosing binding's subject. Never relabel an old tested SHA as new.
The helper returns reused rows with the original tested identity explicitly.

`obligationDigest` hashes sorted compact UTF-8 JSON of only these present map
fields: `id`, `sourceRef`, `sourceDigest`, `sourceLocator`, `classification`,
`sourceQuote`, `requiredBehavior`, `executableChecks`, `proofMethods`, `owner`, `target`,
`profile`, `topology`, `samplingPolicy`. Advice and transient probes are excluded.
Every proof reference must resolve to unchanged bytes in the current inventory.
This is a binding check, not a parser asserting that those bytes prove success;
the verifier must still inspect actual counts, assertions, artifacts and rubric.
Skipped/zero-test summaries, wrong SHA/tree/profile/checkpoint, expired policy,
or a failed required sample cannot pass their source row.

Use `scripts/acceptance.py` beside this Skill to capture Git identities and check
reuse. `capture --assessment assessment.json` validates/copies the existing map
against the separately captured source; repeat `--evidence REF PATH` for actual
owner-retrieved report/probe files. This hashes bytes without fetching or running
anything. `--evidence` and `--source-artifact-ref` require `--assessment`, so
capture cannot emit an inventory/selector without its required map. With a map
and no files, the inventory is explicitly empty.

When original source is a trusted artifact, preserve `sourceBinding` with its
`artifactRef` and RFC6901 `pointer`. An empty pointer hashes the whole original
artifact's raw bytes; an explicit pointer must select an original source string,
whose exact UTF-8 bytes are hashed. The owning host independently reads that
artifact and verifies canonical source identity. `capture --source-artifact-ref
REF [--source-pointer /description]` records this selector and applies it to the
local `--source` file. The original caller, never the verifier, selects the
pointer. Issue-driven hosts bind the complete trusted original brief's raw bytes,
including acceptance criteria and constraints. Do not guess a normalization or
omit source constraints by choosing a narrower field than the original authorized
scope. Current reports must preserve the supplied selector and reconciliation
provenance.

The helper uses an isolated Git index for dirty content and never switches the
user's checkout. A checkpoint ref/digest supplied by an existing workspace owner
may be used instead. Keep the original base/source revision, candidate revision,
and publication branch distinct in the report.

Reuse objective evidence only for the same subject content, repository, original
scope, requirement IDs, completion policy, and required freshness. A changed
candidate, source, or expired proof requires the affected verification again.
Identical valid evidence needs no arbitrary full-suite repeat. An unchanged
checkout and a previous verdict alone are not objective evidence.

`reuse` returns qualified `reusableRows` from source-bound objective bindings even
when the aggregate is blocked or nonpassing. It checks each row's obligation,
subject content, referenced bytes and freshness independently; newly added
inventory need not rerun eleven genuinely passed rows to collect a twelfth proof.
Reevaluate actual observations under a changed policy rather than rerunning an
unchanged workload when those observations still prove the new obligation.
Rows named by either remaining-work ID field are not reusable, even if also
marked `VERIFIED`; a nonpass with absent/empty inline gaps or an opaque-only gap
reference cannot certify partial passes. Judgment and reuse share the same
normalized handoff validation: unknown/optional IDs, contradictory ID fields,
missing actionable fields, and wrong-candidate gaps are report errors, never an
empty blocker set. Historical gaps use their own verified source scope only after
exact current reconciliation lineage is established, so a correctly retired
inherited obligation does not discard unrelated executed proof.
Current handoffs use canonical primary `requirement` IDs; if `requirementIds` is
also present it must include that primary ID. Narrative belongs in `remainingWork`.
A validated exact previous/current scope reconciliation can retain unchanged
rows after inherited IDs are retired, but never reuses the historical aggregate.
Legacy coverage may yield `candidateRows` solely as artifacts to inspect, never
accepted proof. Neither partial result means aggregate success or issue closure.

The helper conservatively rejects changed content. Any owner-provided scoped
reuse across different content requires verified unaffected production, test,
build/toolchain, Content, workload and topology dependencies. An unchanged
filename list or a report-only label is insufficient; unknown impact reruns
only the affected proof once its dependencies are established. Same-content
commits and squash merges retain the original tested identity.

## Automated evidence continuation

Treat missing local tooling as a routing question. Discover and use the existing
authorized CI, container, or qualified-workstation entrypoint before declaring
required execution unavailable. If a job already ran for the candidate, retrieve
its terminal reports and artifacts before requesting another run. Check actual
selected-test counts, results, source/content identities, build/profile, and any
required client/render surface. A green workflow summary or a job ID alone is not
acceptance evidence. Preserve valid evidence and rerun only affected verification.

Use repository-approved AI artifact review for rendered or semantic criteria when
permitted. The reviewer must inspect the actual artifacts against the rubric, not
infer visible behavior from source, logs, or artifact existence. Ambiguity requires
better observations or an explicit non-pass, not automatic human escalation. Never
substitute structural tests for required pixels or override failed machine checks.

Every non-passing result needs concrete `remainingWork` or a durable
`remainingWorkRef`. Include the source requirement, gap type, candidate identity,
existing execution owner/entrypoint or missing capability, evidence references,
and the condition/check that resumes verification. Keep an actionable summary
with the report rather than only an opaque artifact ID. A false
`recoverableInCurrentRuntime` does not mean another authorized owner cannot do it.

When different evidence is obtainable, use the existing `reattempt_current_step`
action for verification or its separate remediation owner. If no authorized path
can currently proceed, retain `blocked` and name what must change before resuming;
do not repeatedly submit the same rejected job. Preserve explicit stop decisions
and attempt budgets. Exhaustion does not itself justify `needs_human`, and this
policy does not create a scheduler, retry loop, publication rule, or new verdict.
A genuine human decision must identify the exact unavailable information or
authority. Do not finish with a generic request to review and complete the PR.

## Report production preflight

Before returning requested structured JSON, explicitly emit `verdict`,
`recommendedNextAction`, and boolean `recoverableInCurrentRuntime`. Use the
existing canonical verdict/action pairs, not inferred defaults. Run the helper
from the resolved Skill against the produced JSON, using a caller-provided
artifact path or ignored disposable files:

```bash
python3 <resolved-skill>/scripts/acceptance.py validate-report --report <report.json> --current <current.json>
```

`current.json` is the freshly captured subject, original scope, completion target,
and freshness policy used for the checks, plus the source-bound map and actual
artifact inventory for structured source-bound work. It is required for success
preflight. Every current verdict, including `BLOCKED` and `NO_DETERMINATION`,
checks exact candidate revision, source obligations, target snapshot, freshness,
and the current evidence-set digest. A historical blocker is not a current
judgment after later evidence arrives; re-read newly available authorized bytes,
including after a prior 403 or expiry, before repeating it. Actual assertion
failures, explicit proof requirements and permission holds remain controlling.

A current non-pass includes inline, mandatory-source-linked `remainingWork` even
when a durable `remainingWorkRef` also exists. Passed rows plus gap IDs cover the
complete mandatory scope without contradiction; optional/unknown criteria cannot
block it. Retain the original historical assessment unchanged. Historical no-map
reports and a truthful non-pass without identity capture remain readable for
compatibility; they do not acquire current source-bound or completion authority.
The helper returns only `valid` and `errors`, with exit 1 on invalid output. It
checks steering fields and reuses the existing acceptance binding check. It does
not fetch artifacts, judge requirements, replace the host's envelope validation,
or turn syntactically valid output into product approval.

If preflight fails, preserve the original response and diagnostics and make one
bounded report-only repair from the existing findings, then revalidate. Do not
change source, tests, scope, evidence, or a truthful verdict to satisfy formatting.
Missing substantive evidence returns to its existing owner instead. Unresolved
output errors remain a report-production failure, not a fabricated product
failure, unavailable environment, or human-review requirement. The caller retains
the last trustworthy verdict and owns any further authorized continuation.

For Markdown-only callers, provide the same explicit decision and actionable
handoff without inventing a requirement for a JSON artifact.

## Resolved Skill provenance

Invoke `python3 <resolved-skill>/scripts/acceptance.py identity` beside the actual
loaded `SKILL.md`. Record its resolved path and SHA-256 digests for the Skill,
this policy, and the helper in the report's diagnostics or existing provenance
metadata. Do not hash a different checkout or assume the latest upstream bundle
was loaded. Do not change the active Skill set during verification. An unavailable
identity probe is a disclosed diagnostic limitation, not a new product acceptance
gate. These digests explain which instructions ran; they are not a blanket
cross-deployment exact-version compatibility requirement.

## Completion target and publication

Candidate verification approves implementation for the existing review/publication
path. It does not prove landing. Clean or already-pushed candidate commits still
require that path. Skip publication and close/transition an already-completed issue
only with objective evidence on the intended completion target, freshly resolved
through the owning repository reader. Never infer landing from an empty diff,
branch name, process exit, prose, assessment verdict, or a PR link alone.

Explicit main/trunk verification inspects the resolved target ref, even when a
feature branch or detached HEAD is checked out. Use `git show`/`git grep` at the
pinned ref for reads and an isolated detached worktree at that ref for tests.
Preserve unrelated local edits. A non-main default, empty diff, or squash merge is
normal. Current target behavior suffices; historical merge links are supplemental.
An explicitly selected alternative completion target is equally valid.

Completion evidence may reuse candidate checks when the target's complete content
is identical (including squash merges); otherwise verify the actual target.
Re-resolve the target before a completion side effect. If it moved, verify/reuse
against that new identity before closing. Unavailable target/source evidence
withholds completion and returns exact evidence work to the existing owner.

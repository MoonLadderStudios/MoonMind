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
- `evidence`: one record per mandatory requirement with its ID and objective
  evidence references. All records certify the enclosing subject and scope.
- `freshness`: the required policy identity and optional expiry for time-sensitive
  evidence. Content-bound evidence need not expire arbitrarily.

Use `scripts/acceptance.py` beside this Skill to capture Git identities and check
reuse. It uses an isolated Git index for dirty content and never switches the
user's checkout. A checkpoint ref/digest supplied by an existing workspace owner
may be used instead. Keep the original base/source revision, candidate revision,
and publication branch distinct in the report.

Reuse objective evidence only for the same subject content, repository, original
scope, requirement IDs, completion policy, and required freshness. A changed
candidate, source, or expired proof requires the affected verification again.
Identical valid evidence needs no arbitrary full-suite repeat. An unchanged
checkout and a previous verdict alone are not objective evidence.

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
and freshness policy used for the checks. It is required for success preflight;
a truthful non-pass can omit it when identity capture itself is unavailable.
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

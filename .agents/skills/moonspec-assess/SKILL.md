---
name: moonspec-assess
description: Assess existing implementation against an original issue brief or source acceptance matrix before implementation or MoonSpec planning.
metadata:
  required-skills: "moonspec-verify"
  required-capabilities:
    - git
---

Read the portable acceptance policy from the resolved `moonspec-verify` bundle
before assessing, verifying, or completing work. Resolve it at
`$MOONMIND_ACTIVE_SKILLS_DIR/moonspec-verify/references/acceptance-policy.md`;
outside MoonMind use `.agents/skills/moonspec-verify/references/acceptance-policy.md`.
It owns scope, mandatory versus optional evidence, reuse, and completion rules.
Preserve the original scope and previously met requirements as regression constraints;
prior reports are context, not current proof. Candidate success alone cannot close
or transition an issue as already landed. Completion follows the shared policy,
including valid Unverified disclosures, on the intended completion target.


# MoonSpec Assess

## Issue-brief assessment

When supplied `issue_provider`, `issue_ref`, `brief_artifact_path`, and
`assessment_artifact_path`, use the original trusted issue brief and constraints.
No MoonSpec packet is required. This is a bounded initial inspection; collect
available objective evidence but do not start a long suite or mutate code/issues.

Preserve the trusted loaded brief at `brief_artifact_path` with keys
`issue_provider`, `issue_ref`, `issue_url`, `title`, `description`,
`acceptance_criteria`, `labels`, `preset_brief`, `constraints`, `source_resolution`,
`trusted_source`, `truncated`, and `truncated_fields`. Copy source text exactly;
map provider summary/title to `title` and body/description to `description`.
Recover truncated fields through the authorized owning reader (MoonMind's trusted
Jira reader in managed runs, authenticated issue tooling for GitHub). Record any
unrecovered fields without inventing requirements; incomplete scope cannot certify
whole-issue completion. Missing optional enrichment is only a limitation.

Write the initial assessment to `assessment_artifact_path` and the step result:
`issue_provider`, `issue_ref`, `issue_url`, `verdict`, `branch`, `base_ref`, `mode`,
`summary`, and `requirements` (stable `id`, original `description`, `status` of
`met`, `partially_met`, `not_met`, `unverifiable`, or `needs_human`, and evidence/reason).
`unverifiable` rows must include `unverified: {reason, verifiableBy}` with nonblank
strings and a stable nonblank `id`; this is the issue-brief spelling of `UNVERIFIED`.
Record candidate `subject`, original `scope`, and `completionTarget` identities
from the shared policy. Keep comparison base and publication branch separate.
For an unavailable mandatory check, preserve its disclosure; complete independent
safe inspection first.

Use `FULLY_IMPLEMENTED` for apparently present implementation, `PARTIALLY_IMPLEMENTED`
for some implementation with gaps, `NOT_IMPLEMENTED` for missing implementation,
or `BLOCKED` when unavailable mandatory source prevents trustworthy assessment.
Prerequisites outside the verifier's reach never make an assessment `BLOCKED`.
Preserve mixed executable work as a bounded backlog. These are
initial inspection verdicts, not objective acceptance or landing evidence. The
verifier must evaluate evidence or valid Unverified disclosures before publication or completion.
Do not overwrite this artifact with a later verifier verdict.

## Verification map and feasibility

Before expensive work, emit the existing `verificationMap` with stable IDs,
original source reference/digest/locator, mandatory/optional/separate-release
classification, required behavior, executable checks, allowed proof methods,
and existing owner. Preserve source-declared target/profile/topology/sampling
constraints; free-form suggestions stay advisory. Follow the shared policy's
exact field contract. Match mandatory IDs to the independently captured source
scope, not a prior report or inherited epic checklist.

Use cheap code and owner probes for capability/prerequisite readiness. Apply the
shared policy's prerequisite classification before marking repository work
unavailable: a related issue's open state is not an active-writer or capability
probe. Keep authorized missing implementation in `boundedBacklog`, with its
concrete next change; record an explicit scope exclusion or confirmed conflicting
writer with evidence and a resume check. Preserve independent ready rows while
routing busy work to its existing owner. Run `acceptance.py admission` before a
repeated setup/implementation attempt. An unchanged unavailable capability is not new implementation work;
missing local tools, a different report ID, or a refreshed timestamp do not
justify repeating the same setup. Never turn missing optional browser proof or
a separately owned physical event into mandatory child acceptance.

## Source-matrix assessment

After `moonspec-specify`, resolve the active feature directory and identifier from
`.specify/feature.json` or the selected `specs/<feature>/spec.md`. Consume
`artifacts/moonspec/source-acceptance.json` only for that feature. If absent or
unrelated, report `not_applicable`; do not require an assessment packet.

## Output

Write `artifacts/moonspec/acceptance-assessment.json` with schema version `v1` and the same `featureId` as the active feature and source acceptance matrix.

External systems can provide a matrix, but MoonSpec stays provider neutral.

Allowed overall verdicts:

- `FULLY_IMPLEMENTED`
- `PARTIALLY_IMPLEMENTED`
- `NOT_IMPLEMENTED`
- `BLOCKED`
- `NO_DETERMINATION`

Allowed row statuses:

- `VERIFIED`
- `PARTIAL`
- `MISSING`
- `CONFLICT`
- `UNVERIFIED`
- `OUT_OF_SCOPE`
- `needs_human`: actual authority handoff, with the exact decision, owner, and action; excluded from executable backlog until authorized.

`UNVERIFIED` rows use the same disclosure object and stable source ID as issue-brief
`unverifiable` rows. Neither status asserts a pass.

## Bounded Backlog

Produce `boundedBacklog` for missing, partial, or conflicting implementation rows. Unverified criteria are disclosure-only: keep their reason and verifiableBy in the assessment, outside `boundedBacklog`. The backlog prioritizes remaining implementation for downstream planning, tasks, and implementation. Verification retains the complete original scope, including previously met requirements as regression constraints.

Separate executable repository changes from required evidence outside the current
runtime. Name the criterion, missing evidence, reason and verifiableBy for each
unavailable check. A mixed backlog can proceed with independent repository work.
Checks outside the verifier's reach become Unverified criteria under the shared
acceptance policy. Optional deployment diagnostics are limitations, not blockers.

Unverified does not grant permission. An actual missing authorization or operator
decision needed to act on the selected operation remains a `needs_human` handoff;
do not perform that action or report its authorized completion until supplied.
Carry `needs_human` as the row status into planning and tasks, with the exact
decision, owner, and action; do not convert it into missing implementation. Do not invent an
authority gate from unavailable deployment evidence, physical testing, or test
signoff: those remain Unverified disclosures when outside the verifier's reach.
Separately scoped release approvals do not gate implementation completion.

Do not choose `FULLY_IMPLEMENTED` for objective acceptance unless every mandatory source row is verified or Unverified under the shared acceptance policy. A bounded initial assessment may identify apparently present implementation; it cannot replace objective verification or prove landing.

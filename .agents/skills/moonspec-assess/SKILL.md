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
or transition an issue as already landed. Completion requires objective evidence
on the intended completion target under that policy.


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
`met`, `partially_met`, `not_met`, or `unverifiable`, and evidence/reason).
Record candidate `subject`, original `scope`, and `completionTarget` identities
from the shared policy. Keep comparison base and publication branch separate.
For an unavailable mandatory check, name its owner, missing evidence, and resume
check; complete independent safe inspection first.

Use `FULLY_IMPLEMENTED` for apparently present implementation, `PARTIALLY_IMPLEMENTED`
for some implementation with gaps, `NOT_IMPLEMENTED` for missing implementation,
or `BLOCKED` when unavailable mandatory source prevents trustworthy assessment
or only unavailable prerequisites remain. Preserve mixed executable work as a
bounded backlog with its explicit prerequisite handoff. These are
initial inspection verdicts, not objective acceptance or landing evidence. The
verifier must check/reuse objective evidence before publication or completion.
Do not overwrite this artifact with a later verifier verdict.

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

## Bounded Backlog

Produce `boundedBacklog` for every missing, partial, conflict, or required-unverified row. The backlog prioritizes remaining implementation for downstream planning, tasks, and implementation. Verification retains the complete original scope, including previously met requirements as regression constraints.

Separate executable repository changes from required evidence or decisions owned
outside the current runtime. Name the owner, missing evidence, and resume check
for each unavailable prerequisite. A mixed backlog can proceed with independent
repository work, but if only unavailable mandatory prerequisites remain, report
`BLOCKED` with that handoff instead of starting another implementation pass.
Optional deployment diagnostics are limitations, not blockers; explicitly required
deployment acceptance cannot be silently excluded because access is unavailable.

Do not choose `FULLY_IMPLEMENTED` for objective acceptance unless every mandatory source row is verified under the shared acceptance policy. A bounded initial assessment may identify apparently present implementation; it cannot replace objective verification or prove landing.

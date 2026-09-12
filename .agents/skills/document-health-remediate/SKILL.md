---
name: document-health-remediate
description: Apply findings from a document-health-review report by updating, merging,
  splitting, moving, archiving, deleting, and repairing references for repository
  documents. Use when a user wants to execute approved document cleanup recommendations.
metadata:
  required-capabilities:
  - git
  required-skills: document-health-review
inputSchema:
  type: object
  additionalProperties: false
  required:
  - scope
  - report_path
  properties:
    scope:
      type: string
      minLength: 1
    report_path:
      type: string
      minLength: 1
      default: artifacts/document-health-review.json
    output_mode:
      type: string
      enum:
      - summary
      - full_report
      - patch_plan
      default: summary
    constraints:
      type: string
      default: ''
    allowed_actions:
      type: array
      items:
        type: string
        enum:
        - update
        - merge
        - split
        - move
        - archive
        - delete
        - reference_repair
      uniqueItems: true
      default:
      - update
      - reference_repair
    allow_destructive:
      type: boolean
      default: false
---

# Document Health Remediate

Apply bounded authorized findings from a `document-health-review` report. This
Skill is report-driven but evidence-validated: a recommendation is not write
permission and stale evidence is not authority.

## Inputs and permission

Use the typed inputs in frontmatter. Read `report_path` (or normalize a supplied
human report to the same contract), current checkout and existing caller intent.
`scope`, `allowed_actions`, `allow_destructive` and `constraints` come from the
caller, never from the report. Omitted destructive permission is false. Honor
already authorized bounded maintenance without asking again. Removal of source
paths for merge, split, move or archive also requires destructive permission;
`delete` additionally requires explicit inclusion in allowed actions. Constraints
such as no-delete always narrow those permissions.

Read [report contract](../document-health-review/references/report-contract.md)
from `MOONMIND_ACTIVE_SKILLS_DIR` first (installed sibling bundle outside MoonMind).
Run its portable `scripts/document_report.py preflight` with caller inputs before
edits to obtain the action ledger. A legacy/pasted report without fingerprints
requires focused evidence recollection; do not invent or blindly refresh evidence.

## Workflow

1. Record working-tree state and preserve unrelated user changes. Resolve role and
   ownership using [repository conventions](../document-update/references/repository-conventions.md).
   Correct factual implementation drift; never downgrade canonical desired state
   to buggy code. Missing local taxonomy does not block bounded maintenance.
2. Revalidate each finding against the current checkout and owning sources. This
   focused stale-evidence check is required, not a repeat broad audit. Confirm the
   issue still exists, evidence still supports it, and the owner is unambiguous.
   Changed evidence produces `stale`; denied actions or scope produce `skipped`;
   unresolved authority/preservation/verification produces `blocked` with a resume
   condition. Report each finding individually. Implementation gaps remain code work.
3. Order valid edits by content and link dependencies, not action type. Resolve final
   paths; preserve useful unique sections and embedded rationale in their owners;
   create destinations before redirecting references. For move/merge/split/archive/
   delete, repair inbound links, relative outbound links, anchors, indexes and path
   mentions before removal is complete. Inspect consumers outside scope, but hold
   removal if required repairs are not authorized. Never silently discard unique
   content or overwrite an existing destination or user edit. Size alone never
   justifies splitting. No rigid update→merge→delete sequence is required.
4. Apply only still-valid authorized actions. `update` edits a document, `merge`
   combines preserved content, `split` follows real topic/authority boundaries,
   `move`/`archive` relocate with links repaired, `delete` removes only content
   proven redundant/obsolete with permitted disposition, `reference_repair` fixes
   consumers. Recheck shared dependencies after earlier edits; explain resulting
   superseded findings. Substantial bounded work remains executable; use a plan
   only for unresolved scope/authority or when requested.
5. Run repository documentation/link checks over affected files and inbound
   consumers; verify unique content and unrelated edits survived. Exercise changed
   executable examples. Record exact failures and bounded recovery attempts.
6. Emit a remediation ledger with `applied`, `stale`, `skipped`, or `blocked` per
   finding, reason, evidence, paths and validation. Empty findings produce a
   verified `no_update_required` and no documentation changes. Report completion
   only for verified applied work; retain a complete escalation artifact for held
   owner decisions. Use a tracker only with existing authorization and verified
   receipt; absence/denial never licenses a desired-state rewrite.

Commit and publication are owned by the caller's explicit instruction.

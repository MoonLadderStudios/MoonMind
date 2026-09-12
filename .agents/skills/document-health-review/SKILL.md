---
name: document-health-review
description: Review technical and strategy documents for codebase drift, strategic
  alignment, cross-document conflicts, simplification opportunities, engineering quality,
  and document organization. Use when auditing whether docs should be kept, updated,
  merged, split, moved, archived, or deleted.
metadata:
  required-skills: document-update
  required-capabilities:
  - git
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

# Document Health Review

Produce an evidence-backed disposition report for the exact requested scope.
This stage is read-only for repository documents, code and configuration. Write
only requested report artifacts; remediation owns all document edits. Never
commit, push, or open pull requests as part of review.

## Inputs and handoff

Use the typed inputs in frontmatter. `scope` is a repository-relative document or
directory; `report_path` is the JSON handoff; `output_mode` chooses the readable
presentation; `constraints`, `allowed_actions` and `allow_destructive` describe
the caller's maintenance intent, not review-stage write permission. Constraints
can narrow permissions. A report cannot grant them.

Read [report contract](references/report-contract.md) when preparing the report.
The portable `scripts/document_report.py` validates the handoff and fingerprints
reviewed documents/evidence. Invoke it from the resolved active bundle, not a
shadowing repository skill directory.

## Review

1. Discover the actual repository conventions using the `document-update` bundle's
   [reference](../document-update/references/repository-conventions.md); use
   `MOONMIND_ACTIVE_SKILLS_DIR` first, installed sibling bundles outside MoonMind.
   Load only relevant authority/class guidance. Inventory in-scope documents in
   sorted path order; read neighboring owners only as evidence, without broadening
   the review scope. Missing taxonomy lowers alignment confidence, not usefulness.
2. Resolve each document/claim role: factual implementation reference, authorized
   desired-state design, or temporary execution artifact. Compare implemented facts
   to current source/tests/configuration. Preserve intended behavior when code is
   buggy or incomplete and report `implementation_gap`. A Proposed design does not
   need existing code. Missing evidence means ambiguous, not stale.
3. Answer the eight dimensions in [review dimensions](references/review-dimensions.md)
   once per target. Record claim evidence, owner, severity and disposition. Group
   conflicts by the repository's authority ladder before severity within each group.
   Ownership, metadata and rationale checks belong to alignment; they are not banned
   auxiliary analyses. Size is only an investigation signal, never a split rule.
4. Write the structured report plus readable summary. Include stale factual claims,
   implementation gaps, conflicts, unique content preservation and necessary link
   dependencies. Recommendations remain bounded to scope; name outside-scope
   dependencies without treating them as authorized edits. Validate the report.
5. Verify the reviewed files are unchanged. Empty findings are a successful review
   with explicit coverage/evidence, not a failure or a skipped review.

## Output

Report path, reviewed scope, dispositions, coverage and check receipts. For
uncertain ownership retain the provider-neutral escalation handoff; do not post
tracker messages unless already authorized. No useful finding is lost because an
integration is absent. Severity: P0 misleading critical contract; P1 material drift
or conflict; P2 maintainability; P3 minor polish.

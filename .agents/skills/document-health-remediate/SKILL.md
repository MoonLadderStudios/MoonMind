---
name: document-health-remediate
description: Apply findings from a document-health-review report by updating, merging, splitting, moving, archiving, deleting, and repairing references for repository documents. Use when a user wants to execute approved document cleanup recommendations.
metadata:
  required-capabilities:
    - git
---

# Document Health Remediate

Consume a `document-health-review` report and apply the approved document maintenance actions to the current repository checkout.

This skill is execution-focused, not review-focused.

- The review skill answers: **What should happen?**
- This remediation skill answers: **How do we safely apply it?**

It is responsible for executing the following action types and validating the resulting docs tree:

- update
- merge
- split
- move
- archive
- delete
- repair references

## Core Design Principle

The skill is **report-driven but evidence-validated**. It does not blindly apply a review report, because documents may have changed between review and remediation. It always:

1. Parses the report.
2. Builds an action ledger.
3. Re-checks that the report still matches the current checkout.
4. Applies changes in a safe order.
5. Preserves unique content before destructive actions.
6. Updates references.
7. Runs validation.
8. Summarizes exactly what changed.

If the report no longer matches the current checkout for a given finding, that finding is held back and reported as `stale` rather than applied.

## Inputs

Required:

- A `document-health-review` report, either as a file path or pasted content.
- A current repository checkout.

Optional:

- Scope limit, such as a single document, directory, action type, or severity (for example `P0`/`P1`).
- Allowed actions, such as `update`, `merge`, `split`, `move`, `archive`, `delete`.
- Disallowed actions, such as `no-delete` or `no-archive`.
- Whether destructive actions are allowed.
- Preferred archive directory.
- Preferred target directory for moved or split documents.
- Validation commands requested by the user.

Example invocations:

- `Use document-health-remediate on reports/docs-health.md.`
- `Use document-health-remediate on the pasted report, but only apply P0/P1 update and move findings.`
- `Use document-health-remediate on Docs/Engineering findings, but do not delete anything.`
- `Use document-health-remediate to apply merge and split findings only.`

## Remediation Boundaries

- Treat the review report as a recommendation, not an instruction. Confirm each finding against the current checkout before acting.
- Keep canonical docs under `docs/` focused on desired state: architecture, contracts, operator-visible behavior, and target semantics.
- Put migration notes, rollout checklists, and temporary investigation details under `docs/tmp/` or in gitignored handoff paths, not as the main framing of canonical docs.
- Follow the document classes and precedence rules in `docs/Workflows/MoonSpecDocumentModel.md`.
- When a superseded document is no longer needed, prefer removing or replacing it over leaving compatibility-era ambiguity, but only when its unique content has been preserved or intentionally discarded.
- Never apply a disallowed action, and never apply a destructive action when destructive actions are not permitted.
- Redact secret-like content if it appears in copied report text, logs, or examples before writing or reporting.

## Supported Actions

### update

Modify an existing document in place.

Used when:

- The document is still needed.
- The document is in the right place.
- The main problem is stale, incomplete, or conflicting content.

Typical operations:

- remove stale sections
- rewrite inaccurate sections
- add missing current behavior
- replace obsolete terminology
- simplify strategy text
- fix cross-links

### merge

Move useful content from one document into another, then either delete or archive the source document.

Used when:

- Two documents substantially overlap.
- One document is the stronger canonical target.
- The source document has some useful unique sections.
- Keeping both creates source-of-truth confusion.

Required preservation check:

- Before removing the source document, confirm that useful unique content has been moved or intentionally discarded.

### split

Break one large or multi-topic document into multiple focused documents.

Used when:

- The document is over 2,000 lines.
- The document contains multiple separable systems.
- Architecture, implementation, operations, and future plans are mixed together.
- A single file is too large to maintain safely.

Rule:

- If `line_count > 2000`, default to splitting unless the report gives a strong reason not to.

### move

Relocate a document to the correct subdirectory.

Used when:

- The document topic does not match its current directory.
- A new directory is needed for a recurring topic.
- Temporary docs live in canonical docs areas.
- Canonical docs live in temporary areas.

Required follow-up:

- Update inbound references, relative links, indexes, and path mentions.

### archive

Move a document out of the active docs tree while preserving it.

Used when:

- The document is no longer active.
- It may contain useful historical context.
- It is superseded but not safe to delete.
- It describes an old migration, old design, or abandoned plan worth preserving.

### delete

Remove a document. This is the most conservative action.

Used only when:

- The report recommends deletion.
- The content is obsolete, misleading, or fully superseded.
- Useful unique content has been preserved elsewhere or is intentionally discarded.
- Inbound references have been removed or updated.

### reference repair

Update links and path references after other actions.

Used after:

- move
- merge
- split
- archive
- delete
- rename

Reference repair should include:

- Markdown links
- relative links
- plain-text path mentions
- docs indexes
- README references
- architecture index references
- agent/skill references when relevant

## Workflow

1. Parse the remediation input.
2. Build an action ledger.
3. Validate the report against the current checkout.
4. Normalize and order actions.
5. Apply updates.
6. Apply merges.
7. Apply splits.
8. Apply moves.
9. Apply archives.
10. Apply deletions.
11. Repair references.
12. Run validation.
13. Produce final remediation summary.

The ordering matters. Do not delete, archive, or move files before preserving content and determining final paths.

## Detailed Workflow

### Phase 0: Preflight

1. Confirm the current working tree state with `git status`. A dirty tree means existing uncommitted changes; surface them before remediating.
2. Identify the docs root:
   - `docs/`
   - `Docs/`
   - both, if present
3. Identify repo documentation conventions from `README.md`, `AGENTS.md`/`CLAUDE.md`, the constitution, and any docs indexes.
4. Confirm the report references documents that still exist in this checkout, and note any that have moved, been renamed, or been removed since the review.

### Phase 1: Parse the remediation input

- Read the report from the provided file path or pasted content.
- Extract each finding: target document path, recommended action type, severity, and rationale.
- Mark findings whose action type is missing or ambiguous as `needs_clarification` instead of guessing.

### Phase 2: Build an action ledger

- For every finding, record one ledger entry with: target path, action type, severity, source/target paths where relevant, preservation requirements, and reference-repair follow-ups.
- Apply scope limits, allowed/disallowed actions, and the destructive-actions flag now, marking filtered entries as `skipped` with the reason.

### Phase 3: Validate the report against the current checkout

- Re-read each target document and confirm the recommended action still applies.
- Mark entries whose evidence no longer holds (file changed, problem already fixed, document already moved) as `stale` and hold them back.
- Recompute `line_count` for any split candidate.

### Phase 4: Normalize and order actions

- Group the surviving entries by action type and order them so non-destructive, content-preserving work runs before destructive or path-changing work: update → merge → split → move → archive → delete → reference repair.
- Resolve final target paths for merges, splits, and moves before any source file is removed or relocated.

### Phase 5: Apply updates

- Edit documents in place to remove stale sections, correct inaccurate content, add missing current behavior, replace obsolete terminology, and fix cross-links.

### Phase 6: Apply merges

- Move useful unique content from the source document into the chosen canonical target.
- Run the preservation check, then delete or archive the source per the report.

### Phase 7: Apply splits

- Create the focused target documents, distribute content, and leave a pointer or index entry so readers can find the split pieces.

### Phase 8: Apply moves

- Relocate the document to its correct directory, creating new directories where the report calls for them.

### Phase 9: Apply archives

- Move the document into the archive directory (preferred archive directory when supplied) so it is preserved outside the active docs tree.

### Phase 10: Apply deletions

- Remove documents only after their unique content is preserved or intentionally discarded and their inbound references are updated.

### Phase 11: Repair references

- After all path-changing actions, update Markdown links, relative links, plain-text path mentions, docs indexes, README references, architecture indexes, and agent/skill references that point at moved, merged, split, archived, deleted, or renamed documents.

### Phase 12: Run validation

- Run any validation commands requested by the user and the repository-mandated documentation/link checks.
- Re-scan the docs tree for broken links and orphaned references introduced by the remediation.
- If validation cannot run, record the exact blocker.

### Phase 13: Produce final remediation summary

- Summarize exactly what changed, grouped by action type, including paths created, edited, moved, archived, and deleted.
- List held-back (`stale`), `skipped`, and `needs_clarification` entries with reasons.
- Include the validation commands run and their results, or the blocker if validation could not run.

## Outputs

- The list of applied actions grouped by type (update, merge, split, move, archive, delete) with affected paths.
- The action ledger, including entries held back as `stale`, `skipped`, or `needs_clarification`, each with a reason.
- Reference-repair results: which references were updated and any that still need manual attention.
- Validation commands run and their results, or the reason validation was not run.
- A concise final remediation summary of exactly what changed.

## Failure Modes

- Report cannot be located or parsed: stop and report the missing path or the unparseable content.
- A report finding no longer matches the current checkout: mark it `stale` and do not apply it.
- Preservation check fails for a merge or delete: do not remove the source; report the unique content at risk.
- A requested action is disallowed, or a destructive action is requested when destructive actions are not permitted: skip it and report why.
- Target or final path cannot be determined: skip the move/merge/split and keep the source intact.
- Reference repair leaves unresolved or ambiguous references: report them for manual follow-up.
- Required validation cannot run: keep the applied changes if they are safe and source-evident, but report the blocked command and reason.
- Secret-like content appears in report text, logs, or examples: redact it before writing or reporting.

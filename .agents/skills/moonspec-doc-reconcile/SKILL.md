---
name: moonspec-doc-reconcile
description: Reconcile a canonical declarative document under docs/ with verified implementation discoveries after a FULLY_IMPLEMENTED moonspec-verify verdict. Use when an orchestration run must decide whether implementation discoveries definitely require updating the source design document for function, consistency, or best practices, apply the smallest correct doc update, or escalate a misaligned update as a Jira issue instead of editing.
metadata:
  requiredCapabilities:
    - git
---

# MoonSpec Doc Reconcile

Use this skill as the final doc-reconciliation pass of a MoonSpec orchestration run. It decides whether verified implementation discoveries definitely require updating the canonical source document, applies the smallest correct update when they do, and reports a structured outcome either way.

This skill operationalizes the reconciliation expectation in `docs/Workflows/MoonSpecDocumentModel.md` and Constitution XI ("update the owning `docs/` files first").

## Preconditions

- The latest `moonspec-verify` verdict for the active feature is `FULLY_IMPLEMENTED`. If it is not, stop with `NO_UPDATE_REQUIRED` and report that reconciliation only runs after successful verification.
- A canonical source document exists: `spec.md` records a `**Source Document**` path under `docs/` (or the breakdown `sourceReference.path` points there). If no canonical document exists, stop immediately with `NO_UPDATE_REQUIRED` and the rationale `no canonical source document`.

## Inputs

- Required: canonical source document path(s) from `spec.md` `**Source Document**` or breakdown `sourceReference.path`.
- Required: the latest `moonspec-verify` report, including its Source Document Drift section.
- Optional: the discovery ledger at `artifacts/doc-discoveries/<feature>.json` written by `moonspec-implement`.
- Optional: doc-drift notes from a `story-reconcile-implementation` report.
- Optional: explicit scope limits from the orchestration step.

Discoveries are the only valid basis for edits. Do not re-derive drift by auditing the whole document; that is `document-update`'s job, not this skill's.

## Update Gate

Edit the canonical document only when at least one discovery **definitely requires** it, meaning the discovery has `definite` severity (or equivalent verified evidence in the verify report) and satisfies at least one of:

1. **Function**: the document as written describes behavior or contracts that are now factually wrong against the verified implementation.
2. **Consistency**: the implementation correctly resolved an internal contradiction or ambiguity in the document, and the document must record the resolution to stay coherent.
3. **Best practices**: the implementation deliberately and correctly diverged from a documented approach for a defensible reason validated by verification.

The following never pass the gate:

- `possible`-severity discoveries and unverified suspicions.
- Stylistic preferences, wording improvements, or speculative future work.
- Drift in temporary artifacts (`spec.md`, `plan.md`, `tasks.md`); those are disposable and are never reconciled into docs.

When no discovery passes the gate, report `NO_UPDATE_REQUIRED` with a one-line rationale per rejected discovery. A no-op outcome is a correct and common result.

## Editing Rules

Follow the editing doctrine of the `document-update` skill (`.agents/skills/document-update/SKILL.md`):

- Update only the sections the gated discoveries name. Make the smallest coherent edit.
- Preserve desired-state framing: never downgrade the documented desired state to match buggy or incomplete code.
- Never insert migration narratives, status checklists, phase plans, or implementation backlogs into canonical docs (Constitution XII).
- Remove superseded text outright instead of layering compatibility language (Constitution XIII).
- Preserve the document's voice, heading structure, and terminology where they remain accurate.
- Cite implementation evidence (file paths, tests) in the reconciliation report, not inside the canonical document.

## Escalation

If a required update would conflict with the constitution, `README.md`, or the declared architecture direction — or the correct desired state is genuinely uncertain — do not edit. Instead:

1. Read `.agents/skills/jira-issue-creator/SKILL.md` and follow its workflow.
2. Create a Jira issue containing the document path, the contradicted claim, the implementation evidence, and why the update may conflict with project direction.
3. Report `ESCALATED` with the issue key and URL.

Escalation does not retroactively fail verification or block the surrounding orchestration.

## Boundaries

- Read-only outside `docs/`: never edit source code, tests, `spec.md`, `plan.md`, `tasks.md`, or configuration.
- Never commit, push, or create pull requests; the orchestration's publication step owns git operations.
- Never delete or rewrite the discovery ledger; it is run evidence.
- Respect secret hygiene: redact secret-like content before writing or reporting.

## Output Contract

Write the structured result to the path provided by the orchestration step when one is given (for Jira Orchestrate runs: `artifacts/jira-orchestrate-doc-reconcile.json`), and include it in the response:

```json
{
  "action": "updated | no_update_required | escalated",
  "docPaths": ["docs/Workflows/Example.md"],
  "gateRationale": "which gate criterion each applied discovery met, or why each discovery was rejected",
  "evidence": ["file, test, or report references backing the decision"],
  "jiraIssue": {"key": "MM-000", "url": "https://..."}
}
```

`docPaths` lists edited documents for `updated`, the considered documents otherwise. Include `jiraIssue` only for `escalated`.

Also return a short markdown summary suitable for inclusion in a pull request body: outcome, edited paths or rejection rationale, and escalation issue key when present.

## Key Rules

- Run only after `FULLY_IMPLEMENTED` verification.
- No canonical source document means an immediate `NO_UPDATE_REQUIRED`.
- Only `definite`, evidence-backed discoveries that meet the function, consistency, or best-practices test justify edits.
- Smallest correct edit; desired-state framing preserved; no imperative content in canonical docs.
- Misaligned updates become Jira issues, never silent edits or silent drops.
- The structured outcome is mandatory in every run, including no-ops.

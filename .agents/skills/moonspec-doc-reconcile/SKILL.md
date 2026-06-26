---
name: moonspec-doc-reconcile
description: Reconcile canonical declarative documents under docs/ with verified implementation discoveries after a FULLY_IMPLEMENTED moonspec-verify verdict. Use when an orchestration run must decide whether implementation discoveries definitely require updating the owning canonical document for function, consistency, or best practices, apply the smallest correct doc update, or escalate ambiguous authority conflicts as Jira issues instead of editing.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Doc Reconcile

Use this skill as the final doc-reconciliation pass of a MoonSpec orchestration run. It decides whether verified implementation discoveries definitely require updating the canonical document that owns the affected claim, applies the smallest correct update when they do, and reports a structured outcome either way.

This skill operationalizes the reconciliation expectation in `docs/Workflows/MoonSpecDocumentModel.md`, the authority ladder and module-owned contract policy in `docs/DocumentationArchitecture.md`, and Constitution XI ("update the owning `docs/` files first").

## Preconditions

- The latest `moonspec-verify` verdict for the active feature is `FULLY_IMPLEMENTED`. If it is not, stop with `no_update_required` and report that reconciliation only runs after successful verification.
- At least one canonical source candidate exists: `spec.md` records a `**Source Document**` path under `docs/`, the breakdown `sourceReference.path` points there, or the orchestration step provides a source design path under `docs/`. If no canonical document candidate exists, stop immediately with `no_update_required` and the rationale `no canonical source document`.

## Inputs

- Required: canonical source document path(s) from `spec.md` `**Source Document**`, breakdown `sourceReference.path`, or the orchestration source design path. Treat these as starting candidates, not as the only documents that may be edited.
- Required: the latest `moonspec-verify` report, including its Source Document Drift section.
- Optional: the discovery ledger at `artifacts/doc-discoveries/<feature>.json` written by `moonspec-implement`.
- Optional: doc-drift notes from a `story-reconcile-implementation` report.
- Optional: explicit scope limits from the orchestration step.

Discoveries are the only valid basis for edits. Do not re-derive drift by auditing the whole document; this skill is intentionally limited to reconciling verified discoveries from the MoonSpec run.

## Authority-Scope Resolution

For each discovery that passes the update gate, identify the canonical document that owns the affected claim before editing. Use `docs/Workflows/MoonSpecDocumentModel.md` for document-class precedence and `docs/DocumentationArchitecture.md` for same-class authority, including the module-owned contract policy.

Apply this bounded procedure:

1. Classify the affected claim: system structure, cross-cutting semantic, module internals, module contract/API/payload shape, feature behavior, rationale, or migration/status text.
2. Map the claim to the owning authority scope in `docs/DocumentationArchitecture.md`:
   - system-wide structure and dependency direction: System Architecture View,
   - cross-module concern semantics: Cross-Cutting Concept View,
   - module internals: Module Architecture View,
   - interface, API, activity/signal/update, DTO, schema, or payload shape: Module Contract Specification owned by the providing module,
   - feature-level behavior: System/Feature Design View,
   - migration/status text: temporary artifact, never canonical desired state.
3. Use the source document path, discovery evidence, cited docs, and nearby module ownership to locate the owning canonical document. The owning document may be different from the original source document.
4. Edit the owning canonical document when the evidence shows that document is incomplete or wrong. Edit a non-owning canonical document only when it conflicts with the owner and must be reconciled to the owner's desired state.
5. Escalate instead of guessing when ownership is ambiguous, when two plausible owners claim the same contract, when the relevant module-owned contract cannot be identified, or when the required update would alter constitution/document-model/documentation-architecture policy or a published contract without clear authority.

Never downgrade a canonical document to match incomplete or buggy code. Verification must establish that the implementation is correct for the agreed story before this skill updates desired-state documentation.

## Update Gate

Edit the canonical document only when at least one discovery **definitely requires** it, meaning the discovery has `definite` severity (or equivalent verified evidence in the verify report) and satisfies at least one of:

1. **Function**: the document as written describes behavior or contracts that are now factually wrong against the verified implementation.
2. **Consistency**: the implementation correctly resolved an internal contradiction or ambiguity in the document, and the document must record the resolution to stay coherent.
3. **Best practices**: the implementation deliberately and correctly diverged from a documented approach for a defensible reason validated by verification.

The following never pass the gate:

- `possible`-severity discoveries and unverified suspicions.
- Stylistic preferences, wording improvements, or speculative future work.
- Drift in temporary artifacts (`spec.md`, `plan.md`, `tasks.md`); those are disposable and are never reconciled into docs.

When no discovery passes the gate, report `no_update_required` with a one-line rationale per rejected discovery. A no-op outcome is a correct and common result.

## Editing Rules

- Update only the sections the gated discoveries name. Make the smallest coherent edit.
- Preserve desired-state framing: never downgrade the documented desired state to match buggy or incomplete code.
- Never insert migration narratives, status checklists, phase plans, or implementation backlogs into canonical docs. Migration, rollout, and MoonSpec execution notes belong under `docs/tmp/` or local-only/gitignored artifact paths.
- Remove superseded text outright instead of layering compatibility language, in keeping with the pre-release compatibility policy.
- Preserve the document's voice, heading structure, and terminology where they remain accurate.
- Cite implementation evidence (file paths, tests) in the reconciliation report, not inside the canonical document.

## Escalation

If a required update would conflict with the constitution, `README.md`, the Document Model, `docs/DocumentationArchitecture.md`, a module-owned contract, or the declared architecture direction — or the owning document/correct desired state is genuinely uncertain — do not edit. Instead:

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
  "updated": [{"docPath": "docs/Workflows/Example.md", "reason": "definite function drift in owning module contract"}],
  "noUpdateRequired": [{"docPath": "docs/ExampleDesign.md", "reason": "possible drift only"}],
  "escalated": [{"docPath": "docs/Workflows/Example.md", "reason": "ambiguous module-owned contract authority"}],
  "gateRationale": "which gate criterion each applied discovery met, or why each discovery was rejected",
  "evidence": ["file, test, or report references backing the decision"],
  "jiraIssue": {"key": "MM-000", "url": "https://..."}
}
```

`docPaths` lists edited documents for `updated`, the considered documents otherwise. The `updated`, `noUpdateRequired`, and `escalated` lists are always present; use empty lists for categories that do not apply. Every updated/noUpdateRequired/escalated item must include a reason. Include `jiraIssue` only for `escalated`.

Also return a short markdown summary suitable for inclusion in a pull request body: outcome, canonical sources considered, owning canonical docs edited or rejected, temporary artifacts consulted, claim coverage, and escalation issue key when present.

## Key Rules

- Run only after `FULLY_IMPLEMENTED` verification.
- No canonical source document means an immediate `no_update_required`.
- Only `definite`, evidence-backed discoveries that meet the function, consistency, or best-practices test justify edits.
- Smallest correct edit; desired-state framing preserved; no imperative content in canonical docs.
- Misaligned updates become Jira issues, never silent edits or silent drops.
- The structured outcome is mandatory in every run, including no-ops.
- Use authority-scope ownership, not original-source-document convenience, to choose what canonical doc to update.

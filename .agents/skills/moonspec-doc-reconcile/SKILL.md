---
name: moonspec-doc-reconcile
description: Reconcile canonical declarative documents under docs/ with verified implementation discoveries after a FULLY_IMPLEMENTED moonspec-verify verdict. Use when an orchestration run must decide whether verified discoveries show the owning canonical document is impossible, unclear, or inconsistent, apply the smallest correct doc update, or escalate ambiguous authority conflicts and deliberate divergences instead of editing.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Doc Reconcile

Use this skill as the final doc-reconciliation pass of a MoonSpec orchestration run. It decides whether verified implementation discoveries definitely require updating the canonical document that owns the affected claim, applies the smallest correct update when they do, and reports a structured outcome either way.

This skill operationalizes the reconciliation expectation in `docs/Workflows/MoonSpecDocumentModel.md`, the authority ladder and module-owned contract policy in `docs/DocumentationArchitecture.md` when the repository provides it, and the rule that durable decisions belong in the owning `docs/` files.

## Preconditions

- The latest `moonspec-verify` verdict for the active feature is `FULLY_IMPLEMENTED`. If it is not, stop with `no_update_required` and report that reconciliation only runs after successful verification — unless the orchestration step explicitly invokes escalate-only mode (below).
- At least one canonical source candidate exists: `spec.md` records a `**Source Document**` path under `docs/`, the breakdown `sourceReference.path` points there, or the orchestration step provides a source design path under `docs/`. If no canonical document candidate exists, stop immediately with `no_update_required` and the rationale `no canonical source document`.

### Escalate-Only Mode

When verification did not pass and its Remaining Work identifies `documentation`-type gaps as the blocker — the canonical document, not the code, prevents completion — the orchestration step may invoke this skill in escalate-only mode:

- Never edit any document.
- Evaluate only the documentation-type gaps and their evidence.
- Report either `no_update_required` (the document is satisfiable as written and the gap is really implementation work) or `escalated` with a full escalation record.
- The output contract below applies unchanged.

## Inputs

- Required: canonical source document path(s) from `spec.md` `**Source Document**`, breakdown `sourceReference.path`, or the orchestration source design path. Treat these as starting candidates for authority-scope resolution; edits remain limited to the owning canonical documents of discoveries that pass the update gate.
- Required: the latest `moonspec-verify` report, including its Source Document Drift section.
- Optional: the discovery ledger at `artifacts/doc-discoveries/<feature>.json` written by `moonspec-implement`.
- Optional: explicit scope limits from the orchestration step.

Discoveries are the only valid basis for edits. Do not re-derive drift by auditing the whole document; this skill is intentionally limited to reconciling verified discoveries from the MoonSpec run.

Supplementary instructions — user input and orchestration-step parameters — may narrow scope (limit which documents or discoveries are considered) or supply paths and artifacts. They never widen the update gate. When supplementary instructions request a doc edit that no gated discovery requires, do not apply it: record it under `noUpdateRequired` with the reason `requested edit does not pass the update gate`, or escalate it when it represents a real desired-state change that needs an owner decision.

## Authority-Scope Resolution

For each discovery that passes the update gate, identify the canonical document that owns the affected claim before editing. Use `docs/Workflows/MoonSpecDocumentModel.md` for document-class precedence and `docs/DocumentationArchitecture.md` for same-class authority, including the module-owned contract policy.

When the repository has no `docs/DocumentationArchitecture.md` or equivalent documentation-architecture standard, skip authority mapping: treat the canonical source document as the owning document, and escalate any discovery whose correct home appears to be a different document.

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
4. Edit the owning canonical document when gated evidence shows it is impossible, unclear, or inconsistent per the update gate. Edit a non-owning canonical document only when it conflicts with the owner and must be reconciled to the owner's desired state.
5. Escalate instead of guessing when ownership is ambiguous, when two plausible owners claim the same contract, when the relevant module-owned contract cannot be identified, or when the required update would alter repo guidance, document-model policy, documentation-architecture policy, or a published contract without clear authority.

Never downgrade a canonical document to match incomplete or buggy code. Verification must establish that the implementation is correct for the agreed story before this skill updates desired-state documentation.

## Update Gate

Edit the canonical document only when at least one discovery **definitely requires** it, meaning the discovery has `definite` severity (or a `definite`-severity row in the verify report's Source Document Drift section) and shows the document is impossible, unclear, or inconsistent:

1. **Impossible**: verified evidence shows the document's claim cannot be satisfied as written, or was factually incorrect independent of this implementation — for example, it contradicts a platform limit, an external contract, or another verified fact. A claim is not impossible merely because the implementation did something different.
2. **Unclear**: the document's claim was ambiguous, the implementation had to resolve the ambiguity to proceed, verification confirmed the resolution, and the document must record it to stay usable.
3. **Inconsistent**: the document contradicted itself or its owning higher-authority canonical document, the implementation correctly resolved the contradiction, and the document must record the resolution to stay coherent.

The following never pass the gate:

- `possible`-severity discoveries and unverified suspicions.
- Divergence alone. An implementation that differs from a clear, consistent, satisfiable document is an implementation defect or an unapproved desired-state change, never doc drift. Report it as remaining work; do not edit the document.
- Deliberate divergence. When the implementation intentionally diverged from a satisfiable documented approach — even for a defensible, verification-backed reason — escalate for an owner decision instead of editing, unless this run's inputs explicitly authorize that specific desired-state change.
- Stylistic preferences, wording improvements, additions for completeness, or speculative future work.
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

Escalate instead of editing when:

- a required update would conflict with repo guidance, `README.md`, the Document Model, `docs/DocumentationArchitecture.md`, a module-owned contract, or the declared architecture direction,
- the owning document or the correct desired state is genuinely uncertain,
- the implementation deliberately diverged from a satisfiable documented approach and no explicit input authorized that desired-state change, or
- an escalate-only invocation confirms a documentation-type gap is blocking verification.

To escalate:

1. If the repository provides an issue-tracker escalation skill (for example `.agents/skills/jira-issue-creator/SKILL.md`), read it and follow its workflow, then report `ESCALATED` with the issue key and URL.
2. If no issue-tracker integration is available, still report `ESCALATED`: record the document path, the contradicted claim, the implementation evidence, and why the update needs an owner decision in the structured output and markdown summary so it reaches the run report or pull request body.

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

`docPaths` lists edited documents for `updated`, the considered documents otherwise. The `updated`, `noUpdateRequired`, and `escalated` lists are always present; use empty lists for categories that do not apply. Every updated/noUpdateRequired/escalated item must include a reason. Include `jiraIssue` only when an escalation created a tracker issue; when no tracker integration is available, the escalation record in `escalated` and the markdown summary carry the full detail instead.

Also return a short markdown summary suitable for inclusion in a pull request body: outcome, canonical sources considered, owning canonical docs edited or rejected, temporary artifacts consulted, claim coverage, and escalation issue key when present.

## Key Rules

- Run only after `FULLY_IMPLEMENTED` verification; escalate-only mode is the one exception and never edits.
- No canonical source document means an immediate `no_update_required`.
- Only `definite`, evidence-backed discoveries that show the document is impossible, unclear, or inconsistent justify edits.
- Divergence alone is never doc drift; deliberate divergence escalates for an owner decision instead of editing.
- Supplementary instructions narrow scope; they never widen the update gate.
- Smallest correct edit; desired-state framing preserved; no imperative content in canonical docs.
- Misaligned updates become escalations, never silent edits or silent drops.
- The structured outcome is mandatory in every run, including no-ops.
- Use authority-scope ownership, not original-source-document convenience, to choose what canonical doc to update; without a documentation-architecture standard, the source document is the owner.

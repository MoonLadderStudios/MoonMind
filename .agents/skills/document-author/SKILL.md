---
name: document-author
description: Write clear repository documentation for the requested topic, using relevant evidence and existing explanations without creating spec.md.
metadata:
  required-capabilities:
    - git
---

# Document Author

Write documentation directly for the requested audience and outcome. This is docs-native authoring, not MoonSpec `spec.md` generation.

## Procedure

1. Read the repository instructions and the request. Find existing explanations of the topic before creating a file; read related code and references only as needed.
2. Choose the smallest useful change. Extend an existing document when it already covers the topic, or place a new document near its subsystem. An authorized multi-document request remains multi-document work, not a reason to return only a plan.
3. Write the useful explanation. Distinguish verified implementation from proposed behavior, preserve the requested constraints, and explain non-obvious reasons close to the relevant behavior. Link to interface definitions instead of copying them.
4. Remove stale or duplicate text in the affected scope. Preserve working references and useful existing anchors. Use headings, metadata, or templates only where they help the reader; do not manufacture a claim ledger or taxonomy.
5. Re-read the result for accuracy, scope, and links. For documentation-only changes, do not add tests for prose or structure. If executable behavior also changes, follow the repository's behavior-testing instructions.

## Boundaries and result

Keep one-time plans, progress history, issue/PR references, and run evidence in issues, PRs, or gitignored artifacts. Repeatable operating instructions belong in docs. Do not create `spec.md` or a `specs/` feature directory for docs-native authoring.

Preserve unrelated edits and redact secrets. A requested design need not already be implemented, but must not be presented as implemented without evidence. If a requirement conflicts with current behavior, resolve it against the request and repository instructions or report the specific uncertainty.

Report changed paths, the important content added or simplified, evidence used, and any verification gaps. Commit, push, open a PR, or create tracker issues only when authorized; authoring alone does not authorize publication.

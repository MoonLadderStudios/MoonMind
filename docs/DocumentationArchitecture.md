# Documentation Architecture

Documentation helps readers understand and operate MoonMind. The operator's current request and root `AGENTS.md` set requirements; documentation explains behavior, designs, and reasons rather than establishing another authority hierarchy.

## Choose the useful home

Update an existing explanation before creating another. Put a new document near the subsystem it describes and use a descriptive filename. Keep interface details with the provider, and link from consumers instead of copying the definition. A directory can simply group related responsibilities; it need not represent a formal domain boundary.

Choose sections for the reader's question. Architecture, feature design, interface reference, troubleshooting, and operating instructions are useful forms, not compulsory classes. A document can combine them when that makes it easier to use. Existing filenames remain useful even when the explanation changes.

Keep durable behavior and non-obvious reasons in `docs/`. Put implementation plans, progress history, issue/PR references, and run evidence in issues, PRs, or gitignored artifacts. Repeatable operating procedures belong in docs; a one-time checklist for delivering a change does not. The [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md) explains how design input and temporary execution artifacts are used by MoonSpec.

## Keep explanations accurate

Read the affected document and the code, tests, or interfaces needed for the task. A factual reference describes verified behavior. A proposed design describes intended behavior and makes its proposal status clear; it does not claim implementation merely because it is documented.

When sources disagree, compare them with the current request, `AGENTS.md`, and implementation evidence. Do not select a winner by document type, title, date, or length. Correct stale explanations in place; if the intended behavior remains unclear, report the concrete conflict. Do not rewrite a supported requirement to excuse a defect in code.

Keep important rationale next to the behavior it explains. Remove duplication, obsolete detail, and explanations that merely restate obvious code. Preserve useful links when moving or consolidating content, and check inbound references before removing a file or heading. Do not split a document just to evade the size guidance in `AGENTS.md`.

## Optional starting points

These outlines are aids, not schemas. Use only the sections that help; no metadata header, viewpoint label, claim-ID ledger, or automatic design-promotion ceremony is required. Keep an existing anchor or identifier when a real consumer relies on it. Add context such as proposal status or related implementation only when it prevents confusion.

- [System architecture](./_viewpoints/SystemArchitectureView.template.md)
- [Module architecture](./_viewpoints/ModuleArchitectureView.template.md)
- [Feature design](./_viewpoints/SystemFeatureDesignView.template.md)
- [Interface contract](./_viewpoints/ModuleContractSpecification.template.md)

Review documentation changes for accuracy, useful scope, and working references. Do not add tests that enforce prose, headings, document counts, or these optional outlines.

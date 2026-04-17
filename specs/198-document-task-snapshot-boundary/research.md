# Research: Document Task Snapshot And Compilation Boundary

## Input Classification

Decision: Treat MM-385 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one user story, one documentation target, and a bounded acceptance set around control-plane preset compilation, task payload metadata, snapshot durability, and execution-plane separation. The selected mode is runtime, so the architecture document is treated as source requirements for product behavior rather than a docs-only preference.

Alternatives considered: Treating the input as a broad declarative design was rejected because the brief already selects one independently testable story. Treating it as an existing feature directory was rejected because no existing MM-385 spec directory was found.

## Source Document Availability

Decision: Use the preserved MM-385 Jira preset brief and current `docs/Tasks/TaskArchitecture.md` as the active sources.

Rationale: The brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout. The Jira response preserved the requirements needed for this story, and `docs/Tasks/TaskArchitecture.md` is present and is the canonical documentation target named by the source sections.

Alternatives considered: Blocking on the missing source document was rejected because the trusted Jira brief contains concrete acceptance criteria, coverage IDs, and implementation notes.

## Implementation Surface

Decision: Update only `docs/Tasks/TaskArchitecture.md` and MoonSpec artifacts unless verification discovers code drift.

Rationale: MM-385 asks for the architecture contract to document preset compilation and snapshot semantics. The current implementation task is to make the runtime contract explicit in canonical docs, not to add a new execution path.

Alternatives considered: Changing preset expansion services was rejected because MM-385 does not request executable expansion behavior changes and is blocked by/blocks adjacent documentation stories.

## Contract Shape

Decision: Extend the representative task contract with `authoredPresets` and `steps[].source`, then document their runtime semantics in prose.

Rationale: The existing `TaskPayload` and `TaskStepPayload` examples already describe task-shaped contracts. Adding optional metadata there makes the control-plane boundary visible without changing storage or runtime APIs in this story.

Alternatives considered: Creating a separate preset-specific task contract was rejected because it would fragment the canonical task-shaped contract and obscure snapshot reconstruction requirements.

## Snapshot Durability

Decision: Expand snapshot rules to preserve pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.

Rationale: These values are the minimum set named by MM-385 for reconstructing and auditing submitted work after catalog changes.

Alternatives considered: Preserving only flattened steps was rejected because it would keep execution possible but lose authored preset intent and rerun/audit semantics.

## Test Strategy

Decision: Use documentation contract checks and final MoonSpec verification rather than adding executable unit tests.

Rationale: The implementation changes canonical Markdown only. Focused `rg` checks can verify required contract terms, and final verification can compare the docs and artifacts against the preserved MM-385 source brief.

Alternatives considered: Adding synthetic code tests was rejected because no runtime code path changes in this story; such tests would not exercise a real boundary.

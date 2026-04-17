# Research: Document Flattened Plan Execution Contract

## Input Classification

Decision: Treat MM-386 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one user story, one documentation target, and a bounded acceptance set around flattened plan artifacts, invalid unresolved includes, optional provenance metadata, validation behavior, DAG semantics, and execution invariants. The selected mode is runtime, so the contract document is treated as source requirements for product behavior rather than a docs-only preference.

Alternatives considered: Treating the input as a broad declarative design was rejected because the brief already selects one independently testable story. Treating it as an existing feature directory was rejected until `specs/199-document-flattened-plan-contract` was created by the specify stage.

## Source Document Availability

Decision: Use the preserved MM-386 Jira preset brief and current `docs/Tasks/SkillAndPlanContracts.md` as the active sources.

Rationale: The brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout. The trusted Jira-derived brief preserves concrete acceptance criteria, coverage IDs, implementation notes, and verification expectations, while `docs/Tasks/SkillAndPlanContracts.md` is the canonical active documentation target named by the source sections.

Alternatives considered: Blocking on the missing source document was rejected because the preserved brief is sufficiently specific and already names the active target contract.

## Implementation Surface

Decision: Update only `docs/Tasks/SkillAndPlanContracts.md` and MoonSpec artifacts unless verification discovers executable validation drift.

Rationale: MM-386 asks for the canonical plan contract to document flattened execution semantics and validation rules. The current implementation task is to make the runtime contract explicit in canonical docs, not to add a new execution path.

Alternatives considered: Changing plan executor or validation code was rejected for planning because the story does not request executable behavior changes and the current evidence points to missing or incomplete contract documentation.

## Contract Shape

Decision: Extend the plan schema and validation contract with optional per-node source provenance and explicit invalid unresolved include behavior.

Rationale: The existing plan schema already defines nodes, edges, policy, and metadata. Adding source provenance as optional metadata on nodes keeps traceability close to the executable unit while preserving the flat runtime graph.

Alternatives considered: Adding task-level provenance only was rejected because MM-386 requires plan node examples with `binding_id`, `include_path`, `blueprint_step_slug`, and `detached` fields. Making provenance required was rejected because the Jira acceptance criteria explicitly allow absent provenance.

## Validation Boundary

Decision: Document that unresolved include objects and structurally invalid claimed preset provenance fail validation before execution, while absent provenance is valid.

Rationale: This preserves fail-fast behavior for unsupported runtime input values and avoids hidden fallback behavior. It also keeps provenance traceability from becoming executable logic.

Alternatives considered: Silently dropping malformed provenance was rejected because it would hide invalid authoring output. Treating absent provenance as invalid was rejected because manually authored plans and non-preset producers may have no provenance.

## Test Strategy

Decision: Use documentation contract checks and final MoonSpec verification rather than adding executable unit tests during planning.

Rationale: The planned implementation changes canonical Markdown only. Focused `rg` checks can verify required contract terms, and final verification can compare the docs and artifacts against the preserved MM-386 source brief. If implementation discovers code changes are needed, tasks must add appropriate unit and integration coverage at the real boundary before changing code.

Alternatives considered: Adding synthetic code tests up front was rejected because no runtime code path change is planned; such tests would not exercise a real boundary for this documentation-contract story.

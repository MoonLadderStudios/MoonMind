# Research: Proposal Promotion Preset Provenance

## Input Classification

Decision: Treat the MM-388 Jira preset brief as a single-story runtime feature request.
Rationale: The brief contains one actor, one outcome, one canonical documentation target, and one independently testable proposal-promotion behavior set.
Alternatives considered: Broad design breakdown was rejected because the request does not ask to split `PresetComposability`; docs-only mode was rejected because the user selected runtime mode.

## Source Artifacts

Decision: Use `docs/Tasks/TaskProposalSystem.md` and the preserved MM-388 orchestration input as active sources.
Rationale: The Jira brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout. The brief explicitly targets section 6, `docs/Tasks/TaskProposalSystem.md`, and the synthesized orchestration input preserves the source requirements.
Alternatives considered: Blocking on the missing source document was rejected because the Jira brief contains enough acceptance criteria and source IDs to proceed.

## Runtime Contract Strategy

Decision: Update the canonical Task Proposal System desired-state contract rather than backend code.
Rationale: Existing adjacent preset-composability stories use canonical docs as runtime contracts. MM-388 asks to document proposal promotion behavior, generator guidance, payload examples, and UI/observability treatment.
Alternatives considered: Adding code tests first was rejected because this story's current acceptance surface is contract/documentation verification; executable code changes can be introduced later if verification finds implementation drift.

## Test Strategy

Decision: Use focused `rg` contract checks as unit-style evidence, source traceability checks as integration-style evidence, full unit tests when feasible, and final MoonSpec verification.
Rationale: The implementation target is Markdown runtime contract text. Focused checks directly validate the contractual terms and source traceability without requiring external services.
Alternatives considered: Compose-backed integration tests were rejected because no runtime service behavior or API schema is changed in this story.

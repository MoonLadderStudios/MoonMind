# Research: Document Plans Overview Preset Boundary

## Input Classification

Decision: Treat MM-389 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one user story, one bounded documentation target, and specific acceptance criteria around a concise authoring/runtime boundary paragraph. The selected mode is runtime, so the documentation is treated as source requirements for product behavior rather than a docs-only preference.

Alternatives considered: Treating the input as a broad declarative design was rejected because the brief already selects one independently testable story. Treating it as an existing feature directory was rejected until `specs/203-document-plans-preset-boundary` was created by the specify stage.

## Source Document Availability

Decision: Use the preserved MM-389 Jira preset brief, `docs/MoonMindRoadmap.md`, `docs/Tasks/TaskPresetsSystem.md`, and `docs/Tasks/SkillAndPlanContracts.md` as active sources.

Rationale: The brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout. It also references `docs/Temporal/101-PlansOverview.md`, while the current repository exposes the plans overview at `docs/MoonMindRoadmap.md`. The target documents contain the required current semantics: `TaskPresetsSystem` describes preset expansion into `PlanDefinition`, and `SkillAndPlanContracts` describes flattened runtime plan semantics.

Alternatives considered: Blocking on missing source paths was rejected because the preserved brief is specific and the repository-current equivalent files contain the required semantics.

## Implementation Surface

Decision: Update `docs/MoonMindRoadmap.md` and MoonSpec artifacts only unless verification discovers executable drift.

Rationale: MM-389 asks for the plans overview or equivalent index to include a concise cross-document boundary clarification. The implementation target is a documentation index, not a runtime code path.

Alternatives considered: Updating `docs/Tasks/TaskPresetsSystem.md` or `docs/Tasks/SkillAndPlanContracts.md` was rejected because those documents already contain the source semantics and MM-389 is specifically about discoverability from the overview.

## Boundary Text Shape

Decision: Add one paragraph directly below the tasks, skills, presets, and plans table.

Rationale: This keeps the alignment near plan overview content and avoids adding a new migration checklist. The paragraph can link both source documents and state the control-plane/runtime boundary in one place.

Alternatives considered: Adding a new subsection was rejected as heavier than the requested concise clarification. Editing canonical docs was rejected because the repository-current overview is under `local-only handoffs` and the story asks not to add canonical migration checklist content.

## Test Strategy

Decision: Use focused documentation contract checks, source traceability checks, and final MoonSpec verification rather than adding executable unit tests.

Rationale: The planned implementation changes Markdown only. Focused `rg` checks can verify the required paragraph content and links. If implementation discovers code changes are needed, tasks must add appropriate unit and integration coverage at the real boundary before changing code.

Alternatives considered: Adding synthetic code tests was rejected because they would not exercise a real runtime boundary for this documentation-index story.

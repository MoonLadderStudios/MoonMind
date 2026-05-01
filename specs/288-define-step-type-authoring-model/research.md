# Research: Define Step Type Authoring Model

## Decision 1: Treat MM-575 as a single-story runtime feature

Decision: Classify the Jira preset brief as one independently testable runtime story because it asks for the selected Step Type to be explicit, type-specific controls to change with the selection, incompatible data to be handled explicitly, and terminology to remain consistent.

Rationale: The Jira issue includes one user story and one cohesive acceptance set. It references `docs/Steps/StepTypes.md` as source requirements rather than as documentation-only work.

Alternatives considered: Running `moonspec-breakdown` was rejected because the issue is already preselected to one story.

## Decision 2: Validate both authoring UI and runtime payload boundaries

Decision: Use Create page rendered tests for authoring behavior and Python task contract tests for executable payload validation.

Rationale: MM-575 includes both draft authoring semantics and invalid mixed-type draft detection. The UI test proves visible Step Type behavior; the runtime contract test proves invalid execution shapes fail before Temporal execution.

## Decision 3: Reuse existing Step Type implementation when evidence passes

Decision: Do not add code unless current tests or inspection reveal a gap.

Rationale: Related Step Type stories have already introduced the selector, separated draft state, discard notice, and mixed payload rejection. MM-575 still requires its own MoonSpec traceability and final verification evidence.

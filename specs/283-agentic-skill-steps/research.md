# Research: Agentic Skill Step Authoring

## Decision 1: Treat MM-564 as a runtime Create-page and validation story

Decision: Use the existing Step Type Create-page surface and executable-step validation boundaries rather than creating a new Skill catalog or workflow engine.

Rationale: `docs/Steps/StepTypes.md` describes Skill as the user-facing agentic Step Type. Existing code already stores `stepType`, Skill selector, JSON args, capabilities, and submitted `type: skill` payloads. The missing work is traceable verification for MM-564.

Alternatives considered: Add a new remote Skill picker/catalog. Rejected for this story because the Jira brief allows selected skill plus validated inputs, and the existing UI already supports selector entry and auto semantics.

## Decision 2: Keep direct task submission Skill controls narrow

Decision: Direct Create-page task submission will verify Skill id/auto, instructions, JSON args, and required capabilities. Broader metadata such as context, permissions, and autonomy remains preserved at the template Skill payload boundary where those fields already exist.

Rationale: Task step entries reject task-scoped runtime overrides, and adding new direct fields for every agentic control would exceed the single-story slice. The existing `skill.args` JSON object is the bounded extension point for contextual data.

## Decision 3: Verification can reuse existing production behavior with MM-564-focused regression

Decision: Add or retain focused frontend and backend tests proving Skill authoring, invalid Skill args rejection, explicit Skill discriminator acceptance, and mixed Tool/Skill rejection.

Rationale: Prior stories implemented shared Step Type contracts. MM-564 should not duplicate production logic but must provide traceable evidence that the agentic Skill story is covered.

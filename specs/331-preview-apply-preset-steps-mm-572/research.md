# Research: Preview and Apply Preset Steps

## Decision: Preserve a separate MM-572 feature directory

Decision: Create `specs/331-preview-apply-preset-steps-mm-572` rather than reusing the existing `MM-558`, `MM-565`, or `MM-578` feature directories.

Rationale: Existing related artifacts cover the same product area but preserve different Jira issue keys. The task instruction explicitly requires `MM-572`, `STORY-004`, and `manual-mm-569-mm-574` traceability.

Alternatives considered: Updating an existing related feature directory was rejected because it would blur source traceability across Jira issues.

## Decision: Treat docs/Steps/StepTypes.md as the source design

Decision: Use `docs/Steps/StepTypes.md` as the source design for Step Type and Preset behavior.

Rationale: The document is the canonical desired-state Step Type model and defines `tool`, `skill`, and `preset` semantics, including authoring-time expansion, validation, and runtime boundaries.

## Decision: Stop at handoff artifacts in this step

Decision: Do not implement, verify, transition Jira, create a pull request, or publish in this task creation step.

Rationale: The current managed step boundary and `MM-572` brief explicitly say not to run implementation inline inside the task creation step. The proper next runtime action is the existing Jira Orchestrate/MoonSpec workflow.

## Decision: Preserve prior related artifacts as evidence candidates

Decision: Record prior related specs as possible downstream evidence, not as substitutes for `MM-572`.

Rationale: `specs/291-preview-apply-preset-steps` contains verification-focused evidence for a closely related story, but `MM-572` still needs its own traceable handoff and downstream verification decision.

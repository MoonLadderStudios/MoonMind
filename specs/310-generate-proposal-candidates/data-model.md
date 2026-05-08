# Data Model: Generate and Validate Proposal Candidates

## Proposal Candidate

- `title`: Non-empty reviewer-facing title.
- `summary`: Non-empty reviewer-facing explanation.
- `category`: Optional normalized proposal category.
- `tags`: Optional normalized signal tags.
- `severity`: Optional signal severity used by policy routing.
- `taskCreateRequest`: Required canonical task creation envelope.

Validation rules:
- Candidate must be an object.
- `title`, `summary`, and `taskCreateRequest` are required before submission.
- `taskCreateRequest.type` must be `task`.
- `taskCreateRequest.payload` must validate as `CanonicalTaskPayload`.
- Candidate errors are redacted before returning to workflow history or logs.

## Task Create Request

- `type`: Must be `task`.
- `priority`: Integer, defaulting through existing proposal service behavior.
- `maxAttempts`: Integer greater than zero.
- `payload.repository`: Required repository target.
- `payload.task`: Canonical task execution payload.

Validation rules:
- Executable tool selectors use `tool.type = "skill"` when a tool selector is present.
- `tool.type = "agent_runtime"` is rejected.
- Runtime defaults may be stamped by proposal policy only when the candidate lacks a runtime.

## Preserved Intent Metadata

- `task.skill`, `task.skills`
- `task.steps[].skill`, `task.steps[].skills`
- `task.authoredPresets`
- `task.steps[].source`

Validation rules:
- Preserve only compact selector/provenance objects already present in canonical task metadata.
- Do not embed full skill bodies, mutable `.agents/skills` directory state, runtime materialization outputs, or unresolved preset include objects.
- Do not fabricate provenance when the parent task lacks reliable metadata.

## Proposal Submission Result

- `generated_count`: Total candidates inspected.
- `submitted_count`: Candidates accepted by validation and handed to the side-effect boundary.
- `errors`: Redacted visible errors for skipped candidates.

State transitions:
- Generated candidate -> validated candidate -> submitted proposal service call.
- Generated candidate -> validation error -> skipped with redacted error.

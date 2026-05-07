# Data Model: Proposal Candidate Validation

## Proposal Candidate

Represents one generated follow-up task suggestion before trusted submission side effects.

Fields:
- `title`: non-empty review title.
- `summary`: non-empty review summary.
- `category`: optional normalized proposal category such as `run_quality`.
- `tags`: optional normalized review tags.
- `severity` / `signal`: optional routing signal metadata.
- `taskCreateRequest`: canonical task create envelope or rejected validation input.

Validation rules:
- Must be an object.
- Must include non-empty `title`, `summary`, and object `taskCreateRequest` before it can be submitted.
- Must validate against the canonical task payload contract before delivery side effects.
- Must not use `tool.type=agent_runtime` for executable tool selectors.
- May use `tool.type=skill` for executable skill tools.
- Must not include skill bodies or runtime materialization state.

## Task Create Request

Executable task-shaped payload stored for proposal promotion.

Fields:
- `type`: must resolve to `task`.
- `priority`: integer, defaults to the established task default.
- `maxAttempts`: integer >= 1.
- `payload.repository`: required repository target.
- `payload.task`: task execution body, including instructions, runtime/git/publish selections, steps, skills, and provenance where valid.

Validation rules:
- The payload validates through the canonical task contract accepted by task submission.
- Runtime defaults may be applied only through existing service behavior and policy rules.
- Delivery metadata stays outside the executable payload unless explicitly part of the task contract.

## Skill Selector

A compact expression of selected agent skills.

Fields:
- `task.skills`: optional task-level selector set/include/exclude/materialization mode.
- `steps[].skills`: optional step-level selector set/include/exclude/materialization mode.
- `task.skill` / `steps[].skill`: optional explicit executable skill selection.

Validation rules:
- Selectors preserve intent by name/ref/version only.
- Skill bodies, resolved active skill snapshots, workspace materialization paths, and large skill content are not embedded.
- Malformed selectors reject the candidate before delivery side effects.

## Provenance Metadata

Reliable authored preset or step source evidence from the parent run.

Fields:
- `task.authoredPresets`: optional authored preset binding metadata.
- `steps[].source`: optional step source metadata.

Validation rules:
- Preserve when reliable parent-run evidence exists.
- Do not fabricate when source evidence is absent.
- Do not preserve unresolved preset execution steps as executable future work.

## Validation Error

Operator-visible rejection reason for an unsafe or malformed candidate.

Fields:
- `candidate_title`: bounded title or placeholder.
- `reason`: redacted bounded text.
- `stage`: generation, validation, or submission.

Validation rules:
- Must not expose secrets, credentials, raw auth headers, or large logs.
- Must not create or mutate delivery records for the rejected candidate.

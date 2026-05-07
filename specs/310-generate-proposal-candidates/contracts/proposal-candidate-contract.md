# Proposal Candidate Contract

## `proposal.generate` Activity

Input:
- `workflow_id`: workflow identifier used for traceability.
- `repo` or `parameters.repository`: repository target.
- `parameters.task`: canonical task context from the parent run.
- `result`, `proposalIdea`, `nextStep`, or `next_step`: durable finish-summary style evidence for a distinct follow-up idea.

Output:
- List of proposal candidate objects.
- Empty list when there is no distinct follow-up idea or when the proposed idea is malformed.

Rules:
- Must not call the proposal service or external provider.
- Must not create tasks, issues, commits, pushes, or delivery records.
- Must preserve compact task selector/provenance metadata only when present and reliable.

## `proposal.submit` Activity

Input:
- `candidates`: list of candidate objects.
- `policy`: optional proposal policy.
- `origin`: workflow/run origin metadata.

Output:
- `generated_count`
- `submitted_count`
- `errors`

Rules:
- Validate every candidate before service submission or no-service structural submission.
- Accept canonical executable Tool steps with `tool.type = "skill"`.
- Reject candidates with `tool.type = "agent_runtime"` or invalid canonical task payloads.
- Return redacted visible errors for rejected candidates.
- Call the proposal service only after validation and policy routing allow submission.

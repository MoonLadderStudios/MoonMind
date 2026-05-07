# Contract: Proposal Candidate Generation And Validation

## Generation Activity

Activity name: `proposal.generate`

Input:
- `principal`: actor/principal metadata.
- `workflow_id`: workflow identifier.
- `run_id`: Temporal run identifier.
- `repo`: trigger repository.
- `parameters`: original run parameters with canonical task payload and durable evidence references.

Output:
- A list of proposal candidate objects.

Required behavior:
- Generates candidates from durable run evidence and normalized outcomes only.
- Performs no commits, pushes, task creation, issue creation, proposal delivery-record mutation, or external tracker delivery.
- Preserves explicit skill selectors and reliable provenance from trusted parent task evidence.
- Does not fabricate authored preset or step source provenance when absent.
- Keeps large logs, artifacts, diagnostics, and skill bodies behind refs instead of embedding them.

## Submission Activity

Activity name: `proposal.submit`

Input:
- `candidates`: list of generated candidate objects.
- `policy`: validated proposal policy.
- `origin`: workflow origin metadata.
- `principal`: actor/principal metadata.

Output:
- `generated_count`: number of received candidates.
- `submitted_count`: number accepted for trusted submission.
- `errors`: bounded redacted rejection reasons.

Required behavior:
- Validates each candidate before service/repository/external side effects.
- Accepts candidate executable tool selectors with `tool.type=skill`.
- Rejects candidate executable tool selectors with `tool.type=agent_runtime`.
- Rejects malformed skill selectors, malformed task payloads, missing repository targets, and semantically ambiguous payloads.
- Calls trusted proposal service only for accepted candidates.
- Continues processing independent candidates after one candidate fails validation.

## Trusted Proposal Service Boundary

Service: `TaskProposalService.create_proposal()`

Required behavior:
- Normalizes and validates `taskCreateRequest` against the canonical task contract.
- Stores only scrubbed proposal payloads.
- Computes deduplication fields after validation.
- Emits notifications only after a proposal record has been created.
- Does not apply side effects for rejected candidates.

## Boundary Invariant

`proposal.generate` and `proposal.submit` remain distinct activity types. Generation is LLM-capable and side-effect-free; submission is trusted and side-effecting only after validation.

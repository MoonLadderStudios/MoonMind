# Research: Generate and Validate Proposal Candidates

## FR-001 / Durable Evidence Inputs

Decision: Treat the `proposal.generate` activity input as the durable evidence envelope for this story and preserve canonical metadata already present there.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` receives workflow id, parameters, result payloads, and task payloads in `proposal_generate`.
Rationale: The source design calls for durable evidence and artifact-backed refs, but no artifact fetch contract is currently passed into the activity. Using the activity envelope avoids adding hidden I/O and keeps generation side-effect-free.
Alternatives considered: Fetching artifact bodies inside generation was rejected because the current activity input does not expose a stable artifact service contract for this story.
Test implications: Unit tests should pass representative parameters/result payloads into `proposal_generate`.

## FR-003 / Candidate Validation Boundary

Decision: Validate `taskCreateRequest` payloads in `proposal_submit` before any proposal service call or no-service structural submission count.
Evidence: `TaskProposalService._prepare_task_create_request` validates through `CanonicalTaskPayload`, but `proposal_submit` can count candidates as submitted when no service is wired.
Rationale: MM-596 requires validation before delivery; activity-level validation makes the boundary deterministic even in worker tests without a service.
Alternatives considered: Relying only on `TaskProposalService` was rejected because no-service activity runs would not prove the boundary.
Test implications: Add tests for accepted `tool.type=skill`, rejected `tool.type=agent_runtime`, and redacted errors.

## FR-006 / Skill Selectors

Decision: Preserve only compact canonical selector fields (`skill`, `skills`, step `skill`, step `skills`) from the parent task payload when they are already present as mappings.
Evidence: `CanonicalTaskPayload` supports task/step skill selectors and rejects full runtime materialization state.
Rationale: This preserves execution intent without embedding large skill bodies or adapter-local state.
Alternatives considered: Embedding materialized `.agents/skills` content was rejected by the source design and AGENTS.md.
Test implications: Generated candidate tests should verify selector refs are copied and runtime materialization fields are absent.

## FR-007 / Provenance

Decision: Preserve `authoredPresets` and original step `source` metadata only when present in canonical task fields and attached to executable flat steps that remain valid under the task contract.
Evidence: `TaskExecutionSpec` supports `authoredPresets`, and `TaskStepSpec` supports `source`; service validation already rejects preset-derived steps that are not flat executable Tool or Skill steps.
Rationale: This satisfies provenance preservation without fabricating include paths or storing unresolved preset include objects.
Alternatives considered: Copying arbitrary original steps was rejected because it could change follow-up execution semantics or store invalid source-only steps.
Test implications: Add tests for preservation and absent-provenance non-fabrication.

## FR-010 / Activity Boundary

Decision: Keep generation and submission in `TemporalProposalActivities` as separate activity methods and verify no proposal service call occurs during generation.
Evidence: activity map includes `proposal.generate` and `proposal.submit`; existing tests cover proposal activity behavior.
Rationale: The current architecture already matches the source design. This story should preserve and verify that boundary rather than invent a new worker family.
Alternatives considered: Combining generation and submission was rejected because it would violate the source design and constitution resiliency boundaries.
Test implications: Add boundary test using a mock service factory that would fail if called by generation.

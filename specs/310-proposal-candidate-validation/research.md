# Research: Proposal Candidate Validation

## FR-001 / FR-007 / DESIGN-REQ-007 - Side-Effect-Free Generation

Decision: Treat proposal generation as implemented but unverified/partial; add explicit no-side-effect and durable-input tests before implementation changes.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` `proposal_generate()` only builds candidate dictionaries; `tests/unit/workflows/temporal/test_proposal_activities.py` covers idea extraction and empty inputs.
Rationale: The current generation activity has no service dependency, but the story requires objective proof that generation cannot commit, create tasks, create issues, or mutate delivery records.
Alternatives considered: Move generation into service layer; rejected because the source design requires generation and submission to stay separate.
Test implications: Unit tests around `TemporalProposalActivities.proposal_generate()` and workflow-boundary tests for activity names.

## FR-002 / DESIGN-REQ-008 / DESIGN-REQ-017 - Canonical Candidate Validation

Decision: Reuse `TaskProposalService._prepare_task_create_request()` and canonical task payload validation for candidate submission; add a lightweight activity-level validation path so no-service structural tests do not count invalid candidates as submitted.
Evidence: `moonmind/workflows/task_proposals/service.py` validates task envelopes and payloads; `proposal_submit()` currently counts structurally valid candidates when no service is wired.
Rationale: Validation must happen before storage or delivery side effects. The service boundary is authoritative when wired, but the activity should not report invalid candidates as submitted in structural/no-service mode.
Alternatives considered: Add a parallel proposal-specific schema; rejected because `TaskProposalSystem.md` requires the `/api/executions` task contract.
Test implications: Unit tests for activity no-service validation and service validation before repository calls.

## FR-003 / DESIGN-REQ-018 - Executable Tool Type Gate

Decision: Add explicit proposal candidate validation for executable tool selectors: accept `tool.type=skill`; reject `tool.type=agent_runtime`.
Evidence: `task_contract.py` validates step skill/tool combinations but no focused proposal candidate test currently proves top-level `task.tool.type` handling.
Rationale: The Jira brief explicitly requires skill tools to be accepted and agent runtime tools to be rejected for generated proposal payloads.
Alternatives considered: Rely on `extra="allow"` behavior in the canonical task model; rejected because it would not fail fast for unsafe proposal payloads.
Test implications: Unit tests with one accepted skill-tool candidate and one rejected agent-runtime candidate.

## FR-004 / FR-005 / FR-006 / DESIGN-REQ-019 - Skill And Provenance Preservation

Decision: Preserve explicit `task.skills`, `step.skills`, `task.authoredPresets`, and `steps[].source` from reliable parent-run task evidence into generated follow-up candidates; do not synthesize these fields when absent.
Evidence: `task_contract.py` defines `TaskSkillSelectors`, `AuthoredPresetBinding`, and `TaskStepSource`; `TaskProposalTaskPreview` already exposes skill and provenance preview fields.
Rationale: Follow-up work may depend on skill intent or reliable preset provenance, but skill bodies and materialization state must stay out of candidate payloads.
Alternatives considered: Drop skill/provenance metadata from generated candidates; rejected because this can make promoted follow-up work drift from the intended execution context.
Test implications: Unit tests for selector/provenance preservation and absent-provenance non-fabrication.

## FR-008 / DESIGN-REQ-032 - Worker Boundary Separation

Decision: Preserve existing separate `proposal.generate` and `proposal.submit` Temporal activity boundaries and add workflow tests that assert the stage invokes these activity names in order through catalog-resolved routes.
Evidence: `moonmind/workflows/temporal/workflows/run.py` schedules `proposal.generate` then `proposal.submit`; `activity_catalog.py` defines separate activity entries.
Rationale: The source design requires LLM-capable generation to be distinct from trusted submission/storage/delivery side effects.
Alternatives considered: Combine validation into generation; rejected because it would blur LLM-facing and trusted side-effect boundaries.
Test implications: Workflow-boundary unit test for the scheduled activity sequence and route metadata.

## FR-009 - Redacted Rejection Errors

Decision: Keep proposal submission best-effort and collect redacted, bounded validation errors; ensure unsafe candidates fail before side effects.
Evidence: `proposal_submit()` currently appends truncated exception text and continues processing remaining candidates.
Rationale: Operators need visible failures, but proposal-stage validation must not expose secrets or fail the parent run.
Alternatives considered: Raise on first invalid candidate; rejected because proposal generation is best-effort and independent candidates should continue.
Test implications: Unit tests asserting errors are returned and repository/service calls are not made for rejected candidates.

## FR-010 / SC-007 - Traceability

Decision: Preserve `MM-596` and source design IDs in all MoonSpec artifacts and final verification evidence.
Evidence: `spec.md` and `plan.md` preserve `MM-596`, source sections, and DESIGN-REQ IDs.
Rationale: Jira orchestration and PR metadata must remain traceable to the issue brief.
Alternatives considered: Use only the feature branch number; rejected because downstream Jira and PR workflows require the Jira key.
Test implications: Final verification grep for `MM-596` and design IDs across spec artifacts and changed code/tests where appropriate.

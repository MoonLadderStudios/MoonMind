# Research: Proposal Candidate Validation

## FR-001 / FR-007 / DESIGN-REQ-007 - Side-Effect-Free Generation

Decision: Treat proposal generation as implemented and verified by focused unit coverage.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` `proposal_generate()` builds candidate dictionaries from run input/evidence and does not depend on proposal service, repository, or external tracker clients; `tests/unit/workflows/temporal/test_proposal_activities.py` covers generation behavior, skill-body exclusion, and no submission side effects.
Rationale: The story requires generation to produce candidates without delivery side effects. Current implementation keeps generation separate from submission and preserves only bounded candidate fields.
Alternatives considered: Move generation into the proposal service layer; rejected because the source design requires generation and submission to stay separate.
Test implications: Focused unit tests remain required; integration strategy is final hermetic integration verification when Docker is available.

## FR-002 / DESIGN-REQ-008 / DESIGN-REQ-017 - Canonical Candidate Validation

Decision: Treat candidate validation as implemented and verified at both activity and trusted service boundaries.
Evidence: `TemporalProposalActivities._validate_candidate_task_create_request()` validates stamped candidate task requests before no-service counting or service calls; `TaskProposalService._prepare_task_create_request()` validates and normalizes proposal payloads through the canonical task payload model; `tests/unit/workflows/temporal/test_proposal_activities.py` and `tests/unit/workflows/task_proposals/test_service.py` cover invalid candidates before side effects.
Rationale: Validation must happen before storage or delivery side effects. Keeping validation at both boundaries prevents false submitted counts in structural mode and protects trusted service writes.
Alternatives considered: Add a parallel proposal-specific schema; rejected because `TaskProposalSystem.md` requires the `/api/executions` task contract.
Test implications: Unit tests cover activity and service paths; hermetic integration remains the broader final strategy when Docker is available.

## FR-003 / DESIGN-REQ-018 - Executable Tool Type Gate

Decision: Accept `tool.type=skill` and reject `tool.type=agent_runtime` for proposal candidate executable tool selectors.
Evidence: `moonmind/workflows/task_proposals/service.py` implements recursive `agent_runtime` rejection; `moonmind/workflows/temporal/activity_runtime.py` validates candidate task requests before submission; focused activity and service tests cover accepted skill-tool candidates and rejected agent-runtime candidates.
Rationale: The Jira brief explicitly requires skill tools to be accepted and agent runtime tools to be rejected for generated proposal payloads.
Alternatives considered: Rely on permissive model validation alone; rejected because unsafe proposal payloads must fail fast before side effects.
Test implications: Unit tests are sufficient for the selector gate; final full unit verification must include these paths.

## FR-004 / FR-005 / FR-006 / DESIGN-REQ-019 - Skill And Provenance Preservation

Decision: Preserve explicit `task.skills`, `step.skills`, `task.authoredPresets`, and `steps[].source` from reliable parent-run task evidence into generated follow-up candidates, while stripping materialization/body-like fields and avoiding fabricated provenance.
Evidence: `proposal_generate()` compacts task and step skill selectors and preserves reliable provenance; `tests/unit/workflows/temporal/test_proposal_activities.py` asserts preservation when evidence is present and non-fabrication when absent; service tests cover authored preset preview/promotion behavior.
Rationale: Follow-up work may depend on skill intent or reliable preset provenance, but skill bodies and materialization state must stay out of candidate payloads and workflow history.
Alternatives considered: Drop skill/provenance metadata from generated candidates; rejected because promoted follow-up work could drift from intended execution context. Embed resolved skill state; rejected because it violates skill-runtime boundaries and payload size constraints.
Test implications: Unit tests cover preservation, stripping, and non-fabrication; no additional implementation work is planned.

## FR-008 / DESIGN-REQ-032 - Worker Boundary Separation

Decision: Preserve separate `proposal.generate` and `proposal.submit` Temporal activity boundaries and verify the workflow schedules them distinctly and in order.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` maps `proposal.generate` and `proposal.submit` to separate activity handlers; `tests/unit/workflows/temporal/workflows/test_run_proposals.py` asserts the proposal stage schedules distinct activity names in order.
Rationale: The source design requires LLM-capable generation to remain distinct from trusted submission/storage/delivery side effects.
Alternatives considered: Combine validation into generation; rejected because it would blur LLM-facing and trusted side-effect boundaries.
Test implications: Workflow-boundary unit tests provide managed-agent-safe proof; hermetic integration remains a final environment-dependent verification layer.

## FR-009 - Redacted Rejection Errors

Decision: Keep proposal submission best-effort and collect redacted, bounded validation errors while ensuring unsafe candidates fail before side effects.
Evidence: `proposal_submit()` appends bounded error text and continues processing remaining candidates; focused tests cover malformed skill selectors, unsafe tool types, and service-call avoidance for rejected candidates.
Rationale: Operators need visible failures, but proposal-stage validation must not expose secrets or fail the parent run.
Alternatives considered: Raise on first invalid candidate; rejected because proposal generation is best-effort and independent candidates should continue.
Test implications: Unit tests verify returned errors and no service calls for rejected candidates.

## FR-010 / SC-007 - Traceability

Decision: Preserve `MM-596` and source design IDs in all MoonSpec artifacts and final verification evidence.
Evidence: `spec.md`, `plan.md`, `research.md`, `tasks.md`, `quickstart.md`, and `verification.md` preserve `MM-596`, source sections, and DESIGN-REQ IDs.
Rationale: Jira orchestration and PR metadata must remain traceable to the issue brief.
Alternatives considered: Use only the feature branch number; rejected because downstream Jira and PR workflows require the Jira key.
Test implications: Final verification should grep for `MM-596` and design IDs across spec artifacts and final implementation evidence.

# Research: Normalize Proposal Intent in Temporal Submissions

## FR-001 / DESIGN-REQ-003: Canonical nested proposal writes

Decision: Partial. Keep nested `task.proposeTasks` and `task.proposalPolicy`, but remove root-level `initial_parameters["proposeTasks"]` for new API submissions.

Evidence: `api_service/api/routers/executions.py` builds `normalized_task_for_planner["proposeTasks"]` and `["proposalPolicy"]`, then also writes root `initial_parameters["proposeTasks"]`; `tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_preserves_proposal_and_skill_intent` currently asserts both root and nested values.

Rationale: The spec requires new writes to persist proposal intent only in the canonical nested task payload. Keeping the root write would make the compatibility location a live write contract.

Alternatives considered: Preserve root field for convenience; rejected because it conflicts with MM-595 and makes future behavior depend on duplicated state.

Test implications: Unit API test must assert nested fields remain and root proposal intent is absent for new task-shaped submissions.

## FR-002: Raw proposal policy preservation

Decision: Implemented unverified. Preserve the existing nested task proposal policy behavior and add a regression that no parallel flattened proposal policy is written.

Evidence: `api_service/api/routers/executions.py` normalizes `task.proposalPolicy`; existing API unit test verifies nested policy values.

Rationale: Policy must remain durable for proposal generation and delivery explanation, but only through the canonical nested task payload.

Alternatives considered: Resolve policy at submission time; rejected because the source design says global defaults plus overrides are resolved in the proposal stage or proposal submission.

Test implications: Unit API coverage, with workflow submit payload tests retaining policy propagation from `parameters.task.proposalPolicy`.

## FR-003 / DESIGN-REQ-004: Cross-surface normalization

Decision: Partial. API and Codex worker paths have building blocks, but schedules/promotions need targeted proof during implementation.

Evidence: `moonmind/agents/codex_worker/worker.py::_task_proposals_requested` reads `canonical_payload["task"]["proposeTasks"]`; `tests/unit/agents/codex_worker/test_worker.py::test_task_proposal_request_uses_task_flag_with_config_gate` covers task-level opt-in. Proposal promotion tests exist under `tests/unit/api/routers/test_task_proposals.py` and `tests/unit/workflows/task_proposals/test_service.py`.

Rationale: MM-595 names all new task creation surfaces. Planning cannot mark this verified until each representative surface has explicit assertions for the canonical nested payload shape.

Alternatives considered: Limit scope to `/api/executions`; rejected because the Jira brief explicitly includes API submissions, schedules, promotions, and Codex managed sessions.

Test implications: Unit tests for API, Codex managed session task creation, and proposal promotion; integration or service-boundary coverage for scheduled execution if that surface creates task-shaped payloads separately.

## FR-004 / FR-005 / DESIGN-REQ-005: Compatibility reads versus new write contracts

Decision: Partial. Keep workflow compatibility reads for older payloads, but isolate them with explicit tests and ensure new submissions stop writing root-level flags.

Evidence: `moonmind/workflows/temporal/workflows/run.py::_proposal_generation_requested` reads nested `task.proposeTasks` first and falls back to root `parameters["proposeTasks"]` under the `run-workflow-nested-propose-tasks` patch. Existing tests cover nested opt-in, false opt-in, global disable, and flattened legacy policy ignoring.

Rationale: Constitution Principle IX requires in-flight workflow safety, while Principle XIII discourages live compatibility write paths. Compatibility reads can remain when bounded to replay/in-flight behavior.

Alternatives considered: Delete all root reads immediately; rejected because persisted workflow histories may still carry older shapes and Temporal payload changes are compatibility-sensitive.

Test implications: Workflow-boundary test for previous root-only payload behavior and a separate new-write test proving root proposal intent is not emitted.

## FR-006 / DESIGN-REQ-006: Proposal state vocabulary

Decision: Implemented unverified. Vocabulary appears in the major surfaces, but no single test proves consistency across workflow, API, UI mapping, finish summary, and touched docs.

Evidence: `moonmind/workflows/temporal/workflows/run.py` defines `STATE_PROPOSALS` and writes proposal summary fields; `api_service/db/models.py` includes `PROPOSALS`; `api_service/api/routers/executions.py` and `task_dashboard_view_model.py` include `proposals`; frontend status helpers exist under `frontend/src/utils`.

Rationale: The requirement is consistency across surfaces, so individual definitions are not enough without regression coverage.

Alternatives considered: Rely on current scattered tests; rejected because status vocabulary drift is exactly the risk called out by MM-595.

Test implications: Unit test for status map vocabulary plus finish summary assertion; UI/Vitest only if frontend status code changes.

## FR-007: Proposal stage gate

Decision: Implemented verified for current workflow behavior.

Evidence: `tests/unit/workflows/temporal/workflows/test_run_proposals.py::test_run_proposals_stage_honors_nested_task_propose_tasks`, `test_run_proposals_stage_skipped_when_proposeTasks_false`, and `test_run_proposals_stage_skipped_when_globally_disabled`.

Rationale: Existing tests prove the global and task-level gates. Implementation must preserve these while removing new root writes.

Alternatives considered: Move gate out of workflow; rejected because proposal generation remains a Temporal workflow stage.

Test implications: Existing workflow unit tests plus final verification.

## FR-008 / SC-006: Jira and source traceability

Decision: Implemented unverified. Preserve MM-595 and the source brief through all MoonSpec artifacts and final delivery metadata.

Evidence: `specs/309-normalize-proposal-intent/spec.md` preserves the canonical Jira preset brief and DESIGN-REQ-003 through DESIGN-REQ-006.

Rationale: Verification depends on comparing implementation against the original Jira preset brief.

Alternatives considered: Keep traceability only in the spec; rejected because tasks, verification, commits, and PR metadata also need to reference MM-595.

Test implications: Traceability review in `/speckit.verify`; no code test beyond artifact checks.

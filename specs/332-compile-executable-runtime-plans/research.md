# Research: Compile Executable Steps into Runtime Plans

## FR-001 / Durable Payload Uses Executable Steps

Decision: implemented_unverified; existing contract and submission boundaries reject unresolved preset work, but MM-573 should add or confirm focused executable-payload evidence.
Evidence: `moonmind/workflows/tasks/task_contract.py` rejects `task.steps[].type` values outside `tool` and `skill`; `tests/integration/temporal/test_task_shaped_submission_normalization.py` asserts compiled recursive preset submissions contain no `preset` steps.
Rationale: Current evidence is strong across adjacent stories, but MM-573 specifically needs runtime-plan traceability for flattened executable payloads.
Alternatives considered: Treat existing recursive-preset evidence as complete; rejected because final verification should compare directly against MM-573.
Test implications: unit plus hermetic integration when boundary behavior changes.

## FR-002 / Tool Step Runtime Plan Mapping

Decision: implemented_verified; no implementation expected unless final verification finds a drift.
Evidence: `moonmind/workflows/temporal/worker_runtime.py` selects explicit step type and tool payloads when building multi-step plan nodes; `tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_runtime_planner_preserves_authored_task_plan_tool_nodes` asserts Tool step identity, inputs, selected skill, type, and source are preserved in the generated plan node.
Rationale: The current test directly covers Tool step to typed plan-node mapping with provenance.
Alternatives considered: Rework runtime plan format; rejected because the existing plan node contract is already accepted by `MoonMind.Run`.
Test implications: none beyond final verify.

## FR-003 / Skill Step Runtime Materialization

Decision: implemented_unverified; add a focused unit test for explicit Skill step mapping.
Evidence: `moonmind/workflows/temporal/worker_runtime.py` builds multi-step plan nodes and `moonmind/workflows/temporal/workflows/run.py` dispatches `agent_runtime` nodes to child workflows and `skill` nodes to activities. Existing tests cover surrounding paths but MM-573 needs a direct Skill-step mapping assertion.
Rationale: Skill mapping can be satisfied by multiple runtime materializations, so test evidence should assert the contract without overfitting to one adapter.
Alternatives considered: Mark verified from workflow dispatch code alone; rejected because plan skill guidance requires tests for unverified behavior.
Test implications: unit.

## FR-004 / Preset Provenance Without Live Runtime Lookup

Decision: implemented_verified; no implementation expected.
Evidence: `TaskStepSource` and `AuthoredPresetBinding` models preserve preset provenance; API and integration tests preserve recursive preset metadata; worker-runtime child expansion writes flattened `steps`, `authoredPresets`, `appliedStepTemplates`, and `composition`; proposal tests preserve preset-derived source metadata.
Rationale: Provenance is structured metadata attached to executable steps and task payloads, not a runtime lookup dependency.
Alternatives considered: Re-expand from live catalog during runtime; rejected because source design forbids catalog drift from changing executable intent.
Test implications: none beyond final verify.

## FR-005 / Proposal Promotion Uses Reviewed Flat Payload

Decision: implemented_unverified; add a no-live-reexpansion regression around proposal promotion.
Evidence: `tests/unit/workflows/task_proposals/test_service.py` covers preserved preset provenance, rejection of preset-derived steps without flat type, and rejection of unresolved preset steps; `tests/integration/temporal/test_proposal_review_delivery.py` shows provider approval creates one run from the stored snapshot and ignores edited provider text.
Rationale: Existing tests cover most promotion semantics but should explicitly prove the promoted execution is not recomputed from a live preset catalog entry.
Alternatives considered: Trust stored snapshot tests only; rejected because MM-573 explicitly calls out silent live re-expansion.
Test implications: unit.

## FR-006 / Unresolved Preset Steps Are Rejected Or Expanded Before Execution

Decision: implemented_verified; no implementation expected.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects unresolved include work and non-executable `preset` step types; `tests/unit/workflows/test_skill_plan_runtime.py` rejects unresolved preset include nodes in plan definitions; `tests/unit/workflows/task_proposals/test_service.py` rejects unresolved preset promotion.
Rationale: The repository already fails explicitly before runtime plan execution for unresolved preset/include work.
Alternatives considered: Allow runtime linked-preset fallback; rejected because linked-preset execution is out of scope and would violate the source design.
Test implications: none beyond final verify.

## FR-007 / MM-573 Traceability

Decision: missing until all downstream artifacts and final delivery metadata preserve it.
Evidence: `specs/332-compile-executable-runtime-plans/spec.md` preserves MM-573, `manual-mm-569-mm-574`, and the original Jira preset brief; this plan and design artifacts continue that chain.
Rationale: Final verification and PR metadata must be able to compare the implementation against the original brief.
Alternatives considered: Preserve only the issue key; rejected because `/speckit.verify` compares against the original brief.
Test implications: final verify.

## Test Strategy

Decision: use unit tests for task contract, runtime planner, and proposal promotion; use hermetic integration tests only when API/execution boundary behavior changes.
Evidence: Existing suites already cover task contract validation, worker runtime planning, task proposal promotion, API task-shaped submission, and integration normalization.
Rationale: Most likely remaining work is verification-first unit coverage; integration is needed if new code changes API submission or worker execution boundaries.
Alternatives considered: Provider verification; rejected because MM-573 is local task/runtime contract behavior and needs no external credentials.
Test implications: `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` if boundary changes are made.

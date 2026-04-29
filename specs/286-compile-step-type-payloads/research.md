# Research: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

## FR-001 / DESIGN-REQ-013 Runtime materialization

Decision: Implemented and verified by existing runtime planner behavior.
Evidence: `moonmind/workflows/temporal/worker_runtime.py`; `tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_runtime_planner_maps_explicit_tool_step_to_typed_tool_node`; `tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_runtime_planner_maps_explicit_skill_step_to_agent_runtime_node`.
Rationale: Explicit Tool steps produce typed tool plan nodes and explicit Skill steps produce agent runtime nodes. The mapping does not require Preset runtime nodes.
Alternatives considered: Adding a new planner layer was rejected because current planner behavior already satisfies the contract and additional indirection would widen the runtime boundary unnecessarily.
Test implications: Unit tests are sufficient for deterministic planner output.

## FR-002 / DESIGN-REQ-016 Preset provenance metadata

Decision: Implemented and verified by task contract and runtime planner evidence.
Evidence: `moonmind/workflows/tasks/task_contract.py::TaskStepSource`; runtime planner test asserts preset source metadata is preserved in node inputs.
Rationale: Provenance is modeled as optional step metadata and is carried through validation/materialization without controlling executable mapping.
Alternatives considered: Requiring provenance for runtime correctness was rejected because the source design explicitly treats it as audit/reconstruction metadata.
Test implications: Unit tests cover preservation.

## FR-003 / SC-004 Proposal preview metadata

Decision: Implemented and verified by the proposal API preview path.
Evidence: `api_service/api/routers/task_proposals.py::_build_task_preview`; `tests/unit/api/routers/test_task_proposals.py::test_get_proposal_preview_includes_preset_provenance`.
Rationale: The API reports preserved-binding or flattened-only provenance from stored payload metadata, allowing review without re-expanding presets.
Alternatives considered: Looking up preset catalog entries for preview was rejected because MM-567 requires stored proposals to remain executable by default without live preset lookup.
Test implications: API unit coverage is sufficient for preview serialization.

## FR-004 / FR-006 Proposal promotion validation

Decision: Implemented and verified by `TaskProposalService.promote_proposal`.
Evidence: `moonmind/workflows/task_proposals/service.py::promote_proposal`; `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_preserves_preset_provenance`; `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_rejects_unresolved_preset_steps`.
Rationale: Promotion validates the stored `CanonicalTaskPayload`, preserves reviewed task fields, and rejects non-executable stored payloads. No live preset expansion call is present in the promotion flow.
Alternatives considered: Re-expanding presets during promotion was rejected because it would create drift between reviewed and executed work.
Test implications: Unit tests cover accepted flat payloads and rejected unresolved Preset payloads.

## FR-005 / FR-008 Activity and Preset rejection

Decision: Implemented and verified by task contract validation and canonical docs.
Evidence: `moonmind/workflows/tasks/task_contract.py::TaskStepSpec._reject_forbidden_step_overrides`; `tests/unit/workflows/tasks/test_task_contract.py`; `docs/Steps/StepTypes.md` sections 7, 10, 13, and 15.
Rationale: Executable validation accepts Tool/Skill Step Types only, rejects Preset and Activity labels, and keeps Activity in Temporal implementation terminology.
Alternatives considered: Compatibility aliases for Activity were rejected under the compatibility policy and source non-goals.
Test implications: Unit validation plus docs check.

## FR-007 Runtime override boundary

Decision: Implemented and verified by proposal service tests.
Evidence: `TaskProposalService.promote_proposal` runtime override branch; `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_applies_runtime_override`.
Rationale: Runtime override modifies only runtime selection fields in the final promoted request while leaving stored proposal payload, reviewed steps, and provenance unchanged.
Alternatives considered: Mutating stored proposals during promotion was rejected because promotion should execute the reviewed payload and preserve audit trail.
Test implications: Unit tests cover runtime override and preservation.

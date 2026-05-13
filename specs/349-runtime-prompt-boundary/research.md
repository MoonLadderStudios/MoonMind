# Research: Runtime Prompt Boundary

## FR-001 - Normalized Task Intent Plus Artifact References

Decision: implemented_unverified; verify the existing prepared-context request shape across runtime modes before changing code.
Evidence: `moonmind/workflows/tasks/prepared_context.py` defines `PreparedInputManifest`, `StepPreparedContext`, `rawInputRefs`, and `inputRefs`; `moonmind/workflows/temporal/workflows/run.py` merges selected prepared refs into agent requests.
Rationale: The primitives exist, but MM-650 requires proof that text-first and multimodal paths both preserve the same canonical task contract.
Alternatives considered: Marking implemented_verified was rejected because current tests emphasize Codex/text-first and selected-context behavior, not paired runtime-mode stability.
Test implications: unit + integration.

## FR-002 / SCN-001 - Text-First INPUT ATTACHMENTS Contract

Decision: implemented_verified; preserve current behavior.
Evidence: `moonmind/agents/codex_worker/worker.py` composes `INPUT ATTACHMENTS`; `tests/unit/agents/codex_worker/test_worker.py` verifies the block appears before `WORKSPACE`, contains manifest and vision context paths, omits non-current step attachments, and sanitizes unsafe metadata.
Rationale: Existing tests directly exercise the text-first prompt boundary named by MM-650.
Alternatives considered: Adding duplicate coverage was rejected; final verification should ensure the behavior remains intact.
Test implications: none beyond final verify unless later implementation changes touch this path.

## FR-003 / SCN-002 - Multimodal Raw Artifact References

Decision: partial; add explicit adapter-boundary tests for raw image artifact refs through a multimodal or external runtime path.
Evidence: `StepPreparedContext.raw_input_refs` and `input_refs` expose raw artifact refs; `BaseExternalAgentAdapter` and `OpenClawExternalAdapter` can forward request `input_refs`; current tests do not frame this as multimodal raw-image behavior or assert the canonical task contract is unchanged.
Rationale: MM-650 requires a clear contract surface for multimodal adapters, not only incidental external adapter behavior.
Alternatives considered: Treating external adapters as sufficient was rejected because provider-specific raw image semantics and no-contract-change guarantees need explicit proof.
Test implications: unit + integration, with implementation contingency if adapter inputs omit required raw refs or mutate target semantics.

## FR-004 / SCN-003 - Allowed Target Kinds

Decision: implemented_verified for model-level target kind restriction; add adapter-boundary guard coverage under FR-005.
Evidence: `PreparedInputEntry.target_kind` is `Literal["objective", "step"]`; `VisionService._target_context_path()` rejects unsupported target kinds; `tests/unit/workflows/tasks/test_prepared_context.py` covers binding validation and inline content rejection.
Rationale: The core prepared-context model already prevents non-canonical target kinds.
Alternatives considered: Replacing the model was rejected because existing strict Pydantic validation is the right boundary.
Test implications: final verify for FR-004; adapter-specific broadening covered by FR-005 tests.

## FR-005 - Adapter-Introduced Targeting Rules

Decision: partial; add tests proving runtime adapters cannot broaden selected attachment targets or introduce new target rules.
Evidence: `select_step_prepared_context()` includes objective and current-step refs only; `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` proves sibling step refs are excluded; adapter-level tests do not yet try to introduce a non-canonical target rule.
Rationale: MM-650 explicitly names adapter-introduced target rules, so coverage should sit at the adapter/workflow boundary as well as the prepared-context model.
Alternatives considered: Relying only on prepared-context tests was rejected because adapters are the named risk surface.
Test implications: unit + integration.

## FR-006 / SC-004 - Target Binding Preservation Across Runtime Paths

Decision: partial; existing text-first and prepared-context coverage is strong, but cross-runtime evidence is incomplete.
Evidence: `tests/unit/workflows/tasks/test_prepared_context.py` covers objective/step targets and reorder stability; `tests/unit/agents/codex_worker/test_attachment_materialization.py` verifies prepare diagnostics; `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` verifies selected step context and external child input scope.
Rationale: MM-650 requires both text-first and multimodal runtime paths to preserve target binding.
Alternatives considered: Marking implemented_verified was rejected because no paired test compares text-first and multimodal runtime preparation from the same canonical task payload.
Test implications: unit + integration.

## FR-007 - Explicit Missing Preparation Diagnostics

Decision: implemented_unverified; verify selected-runtime failure behavior before adding code.
Evidence: `PreparedContextFailure.from_exception()` produces bounded diagnostics; `test_prepare_boundary_reports_target_for_invalid_step_attachment` covers missing artifact IDs with target diagnostics; `VisionService` records disabled/provider-unavailable statuses.
Rationale: Existing diagnostics exist, but the story requires selected-runtime missing context/raw-ref behavior to fail or report explicitly.
Alternatives considered: Treating current diagnostics as complete was rejected until runtime-specific tests prove no silent drop.
Test implications: unit + integration, with fallback implementation if verification exposes silent omission.

## FR-008 / SC-005 - MM-650 Traceability

Decision: implemented_verified; preserve through all later artifacts.
Evidence: `specs/349-runtime-prompt-boundary/spec.md` preserves the original Jira preset brief, `MM-650`, and `DESIGN-REQ-026`; `plan.md` and this research preserve the same identifiers.
Rationale: Traceability is already present and must be maintained.
Alternatives considered: None.
Test implications: final verify.

## DESIGN-REQ-026

Decision: partial; complete explicit multimodal/raw-ref and adapter guardrail proof.
Evidence: `docs/Tasks/TaskArchitecture.md` lines 561-570 define the runtime/prompt boundary; current implementation covers target-aware prepared context, text-first prompt injection, and target validation, but multimodal raw-ref behavior remains under-specified in tests.
Rationale: The source requirement combines four rules, and only three are strongly verified today.
Alternatives considered: Splitting into multiple specs was rejected because the Jira brief selects one bounded runtime story.
Test implications: unit + integration.

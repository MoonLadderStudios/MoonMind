# Research: Show Attachment and Recovery Diagnostics By Target

## FR-001 Target-Grouped Attachment Metadata

Decision: partial; add target-grouped attachment diagnostics to the execution projection and render it in task detail.
Evidence: `api_service/api/routers/executions.py` snapshots compact `attachmentRefs`; `tests/unit/agents/codex_worker/test_attachment_materialization.py` verifies objective and step target collection; `frontend/src/entrypoints/task-detail.tsx` does not render attachment metadata by target.
Rationale: Target ownership exists before and during runtime preparation, but the operator-facing detail surface does not expose it in the requested grouped form.
Alternatives considered: Showing raw task input snapshots was rejected because the spec requires no raw workflow-history parsing and normal redaction/preview behavior.
Test implications: unit + integration.

## FR-002 Empty And Populated Target States

Decision: missing; add explicit display behavior for targets with and without attachment diagnostics.
Evidence: No task-detail component or test was found that distinguishes populated attachment targets from empty targets.
Rationale: Operators need to know whether a target has no attachments versus whether diagnostics are missing or degraded.
Alternatives considered: Hiding empty targets was rejected because it makes missing evidence ambiguous.
Test implications: unit + integration.

## FR-003 Manifest References

Decision: partial; surface compact manifest refs in target diagnostics where available.
Evidence: `docs/Tasks/ImageSystem.md` lines 438-441 require manifest paths discoverable from task diagnostics; `frontend/src/entrypoints/task-detail.tsx` renders generic step artifact refs and raw diagnostics panels but not target-owned attachment manifest refs.
Rationale: Manifest refs are existing evidence, but operators need target context to interpret them safely.
Alternatives considered: Embedding manifest contents was rejected because refs preserve artifact authorization and avoid large projection payloads.
Test implications: unit + integration.

## FR-004 Generated Context References

Decision: partial; expose generated context refs from the target-aware vision context index.
Evidence: `tests/integration/vision/test_context_artifacts.py` verifies `.moonmind/vision/image_context_index.json` with objective and step targets; task detail does not display these refs by target.
Rationale: The generation layer already knows target ownership; task detail needs a compact projection of those refs.
Alternatives considered: Recomputing context in the UI was rejected because generated context is an artifact-backed runtime output.
Test implications: unit + integration.

## FR-005 Target Failure Ownership

Decision: partial; project the affected objective or step target for attachment failures.
Evidence: `tests/unit/agents/codex_worker/test_attachment_materialization.py` records prepare diagnostics; `docs/Tasks/TaskArchitecture.md` lines 666-670 require target and phase; task detail currently shows raw diagnostics text.
Rationale: Existing diagnostics can describe failures, but not in a structured operator-facing field.
Alternatives considered: Requiring operators to inspect diagnostic JSON was rejected by FR-012.
Test implications: unit + integration.

## FR-006 Attachment Failure Phase

Decision: partial; normalize attachment failure phase labels to upload, validation, materialization, and context generation.
Evidence: `docs/Tasks/ImageSystem.md` lines 426-436 defines recommended event classes; task detail diagnostics panel is raw text.
Rationale: A bounded phase taxonomy is needed for consistent UI and tests.
Alternatives considered: Reusing arbitrary event names directly was rejected because it would make user-visible labels unstable.
Test implications: unit + integration.

## FR-007 Current Step Attachment Context

Decision: partial; add a current-step target diagnostics section distinct from objective and unrelated step inputs.
Evidence: `frontend/src/entrypoints/task-detail.tsx` renders step ledger rows and artifacts; there is no current-step attachment context display.
Rationale: Step-aware surfaces already exist but need attachment-specific target context.
Alternatives considered: Listing all step attachments together was rejected because it hides current-step ownership.
Test implications: unit + integration.

## FR-008 Resume Source Provenance

Decision: implemented_unverified; keep existing projection and add targeted verification in the new diagnostics context.
Evidence: `api_service/api/routers/executions.py` builds `resume` and `relatedRuns`; `frontend/src/entrypoints/task-detail.test.tsx` covers failed-step Resume action and related-run link.
Rationale: The base Resume source fields exist, but this story needs them verified alongside diagnostics presentation.
Alternatives considered: Reworking Resume semantics was rejected because subsystem contracts own Resume execution behavior.
Test implications: unit + integration.

## FR-009 Preserved Prior Steps

Decision: implemented_unverified; use existing step ledger `preservedFrom` and add targeted task-detail coverage.
Evidence: `frontend/src/entrypoints/task-detail.tsx` renders `preservedFrom` workflow/run/attempt; `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` asserts preserved rows in workflow execution.
Rationale: Preserved-step data is present, but planning should require operator-facing verification under this feature.
Alternatives considered: Duplicating preserved-step state into a new model was rejected because the ledger is the existing source of truth.
Test implications: unit + integration.

## FR-010 Failed Resume Phase

Decision: partial; add display of bounded failed Resume phase labels.
Evidence: `api_service/api/routers/executions.py` exposes disabled reasons such as `resume_checkpoint_missing`, `workspace_checkpoint_missing`, and `plan_identity_missing`; the spec requires checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution phase labels.
Rationale: Existing reasons are eligibility-focused and need mapping to operator-facing failed Resume phases.
Alternatives considered: Showing internal disabled reason codes only was rejected because the spec asks for phase identification.
Test implications: unit + integration.

## FR-011 Boundary-Preserving Contract

Decision: partial; define a task-detail diagnostics contract that references subsystem evidence without redefining subsystem internals.
Evidence: `docs/Tasks/TaskArchitecture.md` lines 677-689 lists related docs for detailed behavior; current task detail has generic artifacts and diagnostics panels.
Rationale: The feature should expose compact summaries and refs while keeping upload, vision, Temporal, ledger, and rerun behavior owned by their documents.
Alternatives considered: Inlining full subsystem details into task detail was rejected because it would drift from canonical docs.
Test implications: unit.

## FR-012 Structured Diagnostics Before Raw History

Decision: partial; add structured diagnostics summary above or beside raw diagnostics panels.
Evidence: `frontend/src/entrypoints/task-detail.tsx` has copy/download raw diagnostics panels; no structured target-aware summary was found.
Rationale: Raw diagnostics remain useful, but normal inspection should not require parsing them.
Alternatives considered: Removing raw diagnostics was rejected because detailed troubleshooting still needs raw artifacts.
Test implications: unit + integration.

## FR-013 Traceability

Decision: implemented_unverified; preserve MM-635 and the original preset brief through all artifacts and final evidence.
Evidence: `spec.md` preserves the original Jira preset brief; `plan.md` and this research preserve MM-635.
Rationale: Final verification depends on comparing implementation evidence against the original issue brief.
Alternatives considered: Issue-key-only traceability was rejected because the original brief contains source mappings and acceptance criteria.
Test implications: final verify.

## Test Strategy

Decision: use separate unit and integration strategies.
Evidence: Repo instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration CI; existing frontend tests use Vitest/Testing Library through `--ui-args`.
Rationale: The story crosses backend projection, UI rendering, attachment preparation, vision context artifacts, and Resume ledger evidence.
Alternatives considered: Frontend-only testing was rejected because projection shape and artifact refs are part of the operator contract.
Test implications: unit + integration.

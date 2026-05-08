# Research: Execute Resume From the Failed Step Only

## Classification

Decision: Single-story runtime feature request.
Evidence: `specs/328-execute-resume-failed-step/spec.md` preserves MM-634 and defines exactly one `## User Story - Failed-Step Resume Execution`.
Rationale: The Jira brief selects one operator outcome: Resume should preserve completed work, retry the failed step first, and continue later steps without input mutation or full-rerun fallback.
Alternatives considered: Running `moonspec-breakdown` was rejected because the brief is already a bounded story.
Test implications: Unit and integration tests are required.

## Current Resume Execution Surface

Decision: Reuse the existing Resume API, checkpoint schema, Temporal service, step ledger, and Task Detail surfaces.
Evidence: `moonmind/schemas/temporal_models.py` defines `ResumeCheckpointModel`, `ResumeSourceModel`, and preserved-step models; `moonmind/workflows/temporal/service.py` creates linked resumed executions; `moonmind/workflows/temporal/step_ledger.py` materializes preserved rows; `moonmind/workflows/temporal/workflows/run.py` initializes preserved rows from `resumeSource`; `frontend/src/entrypoints/task-detail.tsx` renders `preservedFrom`.
Rationale: The story is an execution correctness layer over already-introduced failed-step Resume contracts.
Alternatives considered: Creating a new recovery workflow was rejected because `MoonMind.Run` is the canonical workflow for failed-step Resume.
Test implications: Focus tests on existing boundaries instead of adding new public surfaces.

## FR-001 Original Input And No Authoring Form

Decision: Implemented but unverified.
Evidence: `create_failed_step_resume_execution()` derives `resumeSource.sourceTaskInputSnapshotRef` from the failed source memo and creates a linked execution from existing source parameters; Task Detail submits Resume from the failed task surface instead of opening Create.
Rationale: The behavior appears present, but the story needs explicit proof that no editable task payload is accepted or submitted.
Alternatives considered: Marking verified was rejected because current evidence is scattered across service and UI tests.
Test implications: Unit and UI tests.

## FR-002 FR-003 Identity Validation And Pre-Execution Failure

Decision: Implemented but unverified.
Evidence: Service validation rejects checkpoint ref mismatch, workflow/run mismatch, snapshot mismatch, plan ref mismatch, plan digest mismatch, and invalid payloads before creating a new execution.
Rationale: Validation is present, but integration coverage should prove these failures happen before any workflow step executes.
Alternatives considered: Treating MM-633 eligibility tests as enough was rejected because MM-634 covers resumed execution behavior after eligibility.
Test implications: Unit plus integration boundary tests.

## FR-004 Workspace Restoration

Decision: Missing.
Evidence: `ResumeCheckpointModel.resume_workspace` requires compact workspace evidence, but current search found no tested `MoonMind.Run` restoration action before the failed step.
Rationale: Validating evidence is not the same as restoring runtime state immediately before new work.
Alternatives considered: Treating checkpoint presence as restoration was rejected because the acceptance criteria require runtime state restoration before retrying the failed step.
Test implications: Unit/integration tests must fail first until restoration behavior is implemented or exposed.

## FR-005 FR-006 Preserved Progress Provenance

Decision: Partial.
Evidence: `materialize_preserved_steps()` marks source rows preserved with source workflow ID, run ID, and attempt; current `preservedFrom` does not include the preserved logical step ID required by MM-634.
Rationale: Preserved progress exists but provenance is incomplete for final traceability.
Alternatives considered: Relying on the row's own `logicalStepId` was rejected because the provenance object should remain self-describing when rendered or exported.
Test implications: Unit, integration, and possible UI type/schema tests.

## FR-007 Preserved Output Injection

Decision: Partial.
Evidence: Preserved artifact refs are copied into ledger row artifacts, and dependency refresh can unblock the failed step. No current boundary test proves those artifacts are consumed by failed/downstream step context as continuous-run outputs.
Rationale: Ledger representation alone does not prove runtime context delivery.
Alternatives considered: Assuming context generation reads ledger artifacts was rejected because this story is explicitly about continuous-run contracts.
Test implications: Unit/integration tests around context preparation or workflow step inputs.

## FR-008 FR-009 Failed Step First And Downstream Progression

Decision: Partial.
Evidence: Helper tests show a two-step graph where a preserved first step unblocks the failed step; normal workflow progression should run later steps, but no full workflow test asserts execution order for resumed runs.
Rationale: The story requires failed step as the first newly executed step and later steps as fresh resumed-run work.
Alternatives considered: Treating `ready` status as enough was rejected because execution order must be proven at the workflow boundary.
Test implications: Hermetic integration test.

## FR-010 FR-011 No Full Rerun And No Preserved Re-Execution

Decision: Partial.
Evidence: Service invalid evidence paths do not call `create_execution()`, and preserved rows are marked with attempt `0`; however, workflow-level tests do not yet prove preserved rows never enter running/executed states.
Rationale: No-fallback and no-reexecution are compatibility-sensitive recovery guarantees.
Alternatives considered: Relying on code inspection was rejected because failures here can be expensive and destructive.
Test implications: Unit and integration tests.

## FR-012 Operator Progress Display

Decision: Implemented but unverified.
Evidence: Task Detail renders `Preserved from source run` when row `preservedFrom` is present.
Rationale: The display exists, but should be refreshed if provenance shape gains logical step identity.
Alternatives considered: No UI changes were planned unless data shape changes; this remains verification-first.
Test implications: UI unit test if schema/display changes.

## Traceability

Decision: Preserve MM-634 and source design coverage through all artifacts.
Evidence: `spec.md` and this research preserve the issue key and canonical Jira preset brief.
Rationale: Final verification must compare implementation against the original Jira source.
Alternatives considered: Storing only the issue key was rejected because `/speckit.verify` needs the preserved brief.
Test implications: Final verification.

# Research: Step Ledger Checkpoint Durability

## FR-001 / DESIGN-REQ-002 - Prepared Input Refs

Decision: partial; add durable parent-owned prepared refs to Resume checkpoint evidence.
Evidence: `moonmind/workflows/tasks/prepared_context.py` builds `PreparedInputManifest`; `moonmind/workflows/temporal/workflows/run.py` selects prepared context per step and injects it into request metadata around lines 5131-5154; `tests/unit/workflows/tasks/test_prepared_context.py` covers target-aware refs and bounded metadata.
Rationale: The helper can derive prepared refs, but the run currently uses them for step dispatch metadata rather than publishing a durable prepared-ref set after prepare succeeds.
Alternatives considered: Treat step request metadata as durable enough; rejected because Resume eligibility needs source-run evidence independent of child request payloads and logs.
Test implications: unit tests for durable prepared ref extraction plus integration coverage that a run checkpoint contains prepared refs after successful preparation.

## FR-002 / DESIGN-REQ-003 - Semantic Step Output Refs

Decision: partial; extend existing step output evidence into checkpoint/preservation evidence.
Evidence: `MoonMindRunWorkflow._record_step_result_evidence()` records output summary, primary, runtime logs, diagnostics, provider snapshot, child ids, task run id, and workload metadata; `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covers output projection and fallback primary selection.
Rationale: Output artifact projection exists, but MM-646 requires those refs to become durable evidence for downstream Resume preservation, not only live ledger display.
Alternatives considered: Reuse existing artifacts fields without new checkpoint projection; rejected because Resume checkpoint validation expects explicit preserved-step evidence and state checkpoint refs.
Test implications: unit tests for mapping step artifacts into preserved-step checkpoint evidence; integration test that successful step refs are available before a later failed step.

## FR-003 / DESIGN-REQ-004 - Workspace or Branch Checkpoints

Decision: missing; add parent-owned state checkpoint ref emission for mutating step boundaries.
Evidence: managed-session/runtime supervisors publish `session.step_checkpoint.json`, and resume models require `stateCheckpointRef`, but `MoonMind.Run` does not currently appear to attach a parent-owned `stateCheckpointRef` to each completed source step.
Rationale: Resume preservation requires state evidence in addition to output artifacts. The parent workflow must own the checkpoint decision even when checkpoint content is produced by a child runtime.
Alternatives considered: Depend on managed session latest checkpoint only; rejected because non-managed steps and child delegation need a common parent-owned evidence contract.
Test implications: unit tests for checkpoint helper behavior and integration tests for workspace-mutating step completion.

## FR-004 - Idempotent Checkpoint Writes

Decision: missing; introduce deterministic idempotency for step-boundary checkpoint artifacts.
Evidence: no parent-level helper keyed by workflow/run/logical step/attempt was found. Existing artifact writes are generic and managed-session checkpoint publication may overwrite session-local artifact names.
Rationale: Temporal activities and workflow tasks can retry. The same logical boundary must resolve to one durable checkpoint identity or one stable idempotent write result.
Alternatives considered: Rely on artifact store overwrites by name; rejected because the contract needs explicit retry-safe behavior and tests.
Test implications: unit test repeated calls for the same boundary and attempt; verify no duplicate logical checkpoint is produced.

## FR-005 - Ref-Only Large Payload Handling

Decision: partial; add checkpoint-specific compactness validation.
Evidence: `prepared_context.py` rejects inline attachment content; step ledger models keep refs and bounded metadata; `docs/Temporal/TemporalArchitecture.md` forbids raw step ledger rows and provider payloads in Memo.
Rationale: Existing patterns are aligned, but MM-646 needs proof specifically for resume checkpoint payloads and eligibility summaries.
Alternatives considered: Trust generic artifact conventions; rejected because this story explicitly names large/binary checkpoint payloads.
Test implications: unit and integration tests should fail if binary-ish or large payload bodies appear in checkpoint/eligibility structures.

## FR-006 / DESIGN-REQ-007 - Eligibility From Durable Evidence

Decision: partial; connect produced checkpoint evidence to Resume eligibility.
Evidence: `ResumeCheckpointModel` validates plan identity, workspace evidence, prepared refs, preserved steps, and state checkpoint refs; `TemporalExecutionService` has tests for hydrated checkpoint validation and mismatch rejection.
Rationale: The consumer side is stronger than the producer side. The run must produce the evidence that eligibility and Resume submission validation already expect.
Alternatives considered: Let recovery service infer eligibility from old execution parameters; rejected because the spec forbids logs/UI reconstruction and requires durable refs.
Test implications: integration tests should build a source run with complete evidence and one with missing evidence, then verify availability/ineligibility outcomes.

## FR-007 / FR-008 - Step-Level Preservation Eligibility

Decision: missing/partial; add explicit eligibility marker and bounded ineligible reason to completed source steps.
Evidence: `StepLedgerRowModel` has artifacts, preserved provenance, workload metadata, and last error, but no explicit Resume eligibility state. `materialize_preserved_steps()` requires state checkpoint refs when consuming preserved steps.
Rationale: A completed step needs to say whether it can be preserved and why not, without forcing downstream code to reverse-engineer the reason from absent fields.
Alternatives considered: Use absence of `stateCheckpointRef` as the only signal; rejected because operators and verification need bounded reasons.
Test implications: unit tests for rows with complete evidence, missing output refs, missing checkpoint refs, and skipped/non-mutating cases.

## FR-009 / DESIGN-REQ-006 - Parent Ownership With Delegated Steps

Decision: implemented_unverified; add boundary proof.
Evidence: `MoonMindRunWorkflow` owns `_step_ledger_rows`; child workflow ids and task run ids are projected into row refs; `materialize_preserved_steps()` records parent-visible `preservedFrom` provenance; managed session supervisors publish checkpoint artifacts.
Rationale: Ownership shape exists, but there is no focused MM-646 proof that child-produced checkpoint refs are projected into parent-owned checkpoint evidence.
Alternatives considered: Count existing child-ref tests as sufficient; rejected because they cover lineage but not state checkpoint evidence.
Test implications: integration test for a delegated step returning checkpoint/output refs and the parent ledger/checkpoint recording them.

## Test Tooling

Decision: use pytest through repo runners, with unit and integration strategies separated.
Evidence: AGENTS.md requires `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic `integration_ci`; existing related tests are pytest-based.
Rationale: The story touches Temporal workflow/activity boundaries, so isolated helper tests are not enough.
Alternatives considered: Manual inspection only; rejected by constitution and repo testing policy.
Test implications: unit tests for helpers/models/workflow methods; integration tests for `MoonMind.Run`/TemporalExecutionService behavior.

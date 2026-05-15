# Research: Preserve Slash Command Fidelity Across Edit, Rerun, Details, and Audit

## Scope Decision

Decision: Single runtime story; continue with planning artifacts, not breakdown.
Evidence: `specs/357-preserve-slash-command-fidelity/spec.md` defines one `## User Story - Audit Historical Slash Command Meaning` and preserves MM-687.
Rationale: The Jira preset brief targets one independently testable operator workflow across existing edit, rerun, detail, and audit surfaces.
Alternatives considered: Running `moonspec-breakdown` was rejected because the brief is not a broad multi-story design.
Test implications: One cohesive unit/integration test set should cover all surfaces.

## FR-001

Decision: partial; verify snapshot field preservation and add implementation only where durable execution artifacts drop metadata.
Evidence: `moonmind/workflows/tasks/task_contract.py` builds `runtimeCommand` metadata with recognition mode, runtime capability version, hint catalog version, source path, command, args, instruction body, and detection phase. `moonmind/schemas/agent_runtime_models.py` accepts these fields.
Rationale: Backend metadata construction is present, but MM-687 requires historical preservation across downstream surfaces, not just creation.
Alternatives considered: Marking implemented_verified was rejected because current tests do not prove full edit/rerun/detail/audit preservation.
Test implications: Unit tests for snapshot building; integration test for artifact-backed execution retrieval.

## FR-002

Decision: partial; preserve separation between authored instructions and interpretation through every surface.
Evidence: `build_authoritative_task_input_snapshot()` stores `instructions` and `runtimeCommand` as separate fields; `frontend/src/lib/temporalTaskEditing.ts` reconstructs both.
Rationale: Data separation exists, but task detail and audit surfaces do not yet prove side-by-side display.
Alternatives considered: Treating snapshot separation as sufficient was rejected because operator-visible surfaces are part of the story.
Test implications: Unit tests for Create/Edit/Rerun reconstruction and Task Detail rendering.

## FR-003

Decision: implemented_unverified; add slash-command-specific edit-mode verification first.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` reconstructs `taskInstructions` from snapshot artifacts and task payloads; `frontend/src/entrypoints/task-create.test.tsx` has existing edit/rerun reconstruction tests.
Rationale: The mechanism exists, but the story requires explicit slash-command historical behavior.
Alternatives considered: Marking implemented_verified was rejected because existing tests are not specific to slash command metadata fidelity.
Test implications: Focused Vitest unit test for edit mode loading `/review` authored instructions from snapshot.

## FR-004

Decision: partial; add no-metadata historical behavior and preview-only re-detection.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` preserves `runtimeCommand` when present; `frontend/src/lib/temporalTaskEditing.test.ts` verifies basic runtime command draft reconstruction.
Rationale: Present-metadata behavior exists, but the spec also requires absent metadata not to mutate historical raw instructions.
Alternatives considered: Using current Create preview tests alone was rejected because they do not simulate historical snapshots.
Test implications: Vitest unit tests for both present and absent `runtimeCommand` edit-mode snapshots.

## FR-005

Decision: partial; add exact rerun preservation tests for runtime command metadata and catalog versions.
Evidence: `moonmind/workflows/temporal/service.py` implements exact rerun recovery and rerun parameters; `frontend/src/entrypoints/task-create.test.tsx` has exact rerun payload tests.
Rationale: Rerun mechanics exist, but metadata/version preservation is not proven.
Alternatives considered: Trusting generic rerun tests was rejected because MM-687 is specifically about command metadata fidelity.
Test implications: Unit tests for exact rerun payloads plus integration coverage for artifact-backed rerun source data.

## FR-006

Decision: missing; add version drift warning that does not replace source-run metadata.
Evidence: No current code search result showed warning behavior tied to differing `runtimeCapabilityVersion` or `hintCatalogVersion`.
Rationale: The spec explicitly requires warnings to be distinguishable from preserved evidence.
Alternatives considered: Omitting drift warnings was rejected because operators need to understand why current previews differ from historical runs.
Test implications: Unit tests for warning model/state and UI rendering; integration test if warning depends on API payload.

## FR-007

Decision: partial; verify edit-for-rerun recomputation does not mutate source run.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` supports `edit-for-rerun`; `moonmind/workflows/temporal/service.py` records `edited_full_retry` provenance from patch payloads.
Rationale: Mode/provenance exists, but runtime command warning recomputation and source immutability are not covered.
Alternatives considered: Treating edit-for-rerun provenance as enough was rejected because MM-687 requires source-run command metadata immutability.
Test implications: Unit tests for source metadata preservation and integration test for request payload/provenance.

## FR-008

Decision: partial; ensure Task Detail shows original authored instructions for slash-command tasks.
Evidence: Task detail surfaces task data, but search did not find a slash-specific original-instructions presentation tied to runtime commands.
Rationale: Operators need the original text alongside interpretation; existing generic detail rendering may not be enough.
Alternatives considered: Relying on raw task data display was rejected because the spec requires a clear operator-facing surface.
Test implications: Task Detail Vitest rendering test.

## FR-009

Decision: missing; add Task Detail runtime command interpretation display.
Evidence: `rg` found no `runtimeCommand` handling in `frontend/src/entrypoints/task-detail.tsx`.
Rationale: The command, runtime, render mode, and status are required on task details when available.
Alternatives considered: Showing only original instructions was rejected because it does not explain runtime interpretation.
Test implications: Task Detail Vitest rendering test for command, runtime, render mode, and status.

## FR-010

Decision: partial; add named command audit events and/or expose existing render metadata through observability.
Evidence: `moonmind/workflows/temporal/runtime/launcher.py` writes `runtimeCommandRender` metadata; integration tests inspect render metadata. No current evidence found for `runtime_command.detected`, `runtime_command.rendered`, or `runtime_command.passthrough` audit events.
Rationale: Render metadata exists but operator audit events need stable names and secret-safe content.
Alternatives considered: Reusing raw runtime metadata only was rejected because Mission Control audit surfaces should not require raw workflow internals.
Test implications: Unit tests for event construction and integration test for observability/API exposure.

## FR-011

Decision: partial; extend secret-safe handling for audit/detail command metadata.
Evidence: Runtime rendering failure tests use redacted diagnostics; renderer code treats command text as untrusted. Detail/audit sanitization for MM-687 fields is not proven.
Rationale: Command names and bodies are user-authored and must be handled as untrusted display data.
Alternatives considered: Assuming existing UI escaping is enough was rejected because audit output also needs coverage.
Test implications: Unit tests for sanitization/redaction and integration event payload assertions.

## FR-012

Decision: missing; add end-to-end validation across edit, exact rerun, edit-for-rerun, task details, and audit.
Evidence: Existing tests are split across snapshot, preview, rendering, and rerun mechanics but do not cover the full historical-fidelity story.
Rationale: The story is cross-surface; isolated tests are not enough to prevent regressions.
Alternatives considered: Only adding unit tests was rejected because Temporal/artifact boundaries are part of the behavior.
Test implications: Focused Vitest and pytest unit tests plus hermetic integration coverage.

## FR-013

Decision: implemented_unverified; preserve MM-687 traceability through all artifacts and final evidence.
Evidence: `spec.md`, `plan.md`, and this research preserve MM-687 and the original Jira preset brief reference.
Rationale: Traceability is currently present but must continue through tasks, implementation, verification, commit, and PR metadata.
Alternatives considered: No further traceability work was rejected because downstream artifacts are not generated yet.
Test implications: Final `/moonspec-verify` traceability check.

## Source Design Coverage

Decision: DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-018 remain in scope.
Evidence: `docs/Steps/SlashCommands.md` lines 160-197, 540-553, 718-790, and 917-923 define metadata, edit/rerun behavior, audit/detail expectations, security requirements, and tests.
Rationale: These source requirements directly match MM-687 acceptance criteria.
Alternatives considered: Treating source docs as fully implemented was rejected because repo evidence shows gaps in detail and audit surfaces.
Test implications: Requirement-specific tests should cite these IDs in task traceability.

## Test Strategy

Decision: Use both unit and hermetic integration tests.
Evidence: Repo instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for integration_ci. Existing relevant tests are Vitest frontend tests, Python unit tests for task contracts/workflows, and integration tests around managed runtime metadata.
Rationale: UI reconstruction/display can be validated in unit tests, but artifact-backed execution/rerun and observability boundaries need integration coverage.
Alternatives considered: Running only frontend tests was rejected because backend snapshot and Temporal boundaries are part of the feature.
Test implications: Focused iteration may use `npm run ui:test -- frontend/src/...`, targeted `pytest`, and final `./tools/test_unit.sh`; integration evidence should use focused integration tests and final `./tools/test_integration.sh` when environment permits.

## Storage Decision

Decision: Reuse existing task input snapshots, execution parameters, Temporal metadata, and artifact-backed observability/control-event surfaces; no new persistent table.
Evidence: Existing snapshot refs, execution records, and observability event loading already support operator-facing data.
Rationale: MM-687 is preservation and presentation of metadata already attached to tasks/runs.
Alternatives considered: Adding a dedicated audit table was rejected because no requirement needs new query semantics beyond existing execution/detail/audit surfaces.
Test implications: Integration tests should assert data flows through existing artifacts/API payloads.

## Contract Decision

Decision: Add a feature-local contract for historical slash command fidelity.
Evidence: The feature exposes behavior through Create/Edit/Rerun UI, Task Detail UI, and audit/observability event payloads.
Rationale: A contract keeps UI and backend expectations aligned without adding implementation details to `spec.md`.
Alternatives considered: Only relying on `spec.md` was rejected because tasks need concrete interface expectations for payloads and event fields.
Test implications: Unit and integration tests should map assertions to the contract fields.

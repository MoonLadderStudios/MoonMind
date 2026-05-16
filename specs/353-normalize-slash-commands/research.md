# Research: Normalize Slash-Leading Instructions

Source traceability: MM-684 canonical Jira preset brief and `docs/Steps/SlashCommands.md`.

## FR-001 / DESIGN-REQ-001

Decision: Partial existing behavior. Preserve current instruction fields and add metadata alongside them.
Evidence: `moonmind/workflows/tasks/task_contract.py` `build_authoritative_task_input_snapshot()` copies `objective.instructions` and step `instructions`; `tests/unit/workflows/tasks/test_task_contract.py` covers snapshot preservation for existing fields.
Rationale: The feature must not replace or mutate authored instructions. Adding sibling metadata fields keeps audit and edit/rerun behavior intact.
Alternatives considered: Rewriting instructions into rendered runtime input during task submission was rejected because runtime rendering belongs to adapters and later preparation stages.
Test implications: Unit and boundary tests must assert exact instruction preservation for detected, escaped, unknown, malformed, and unsupported-runtime cases.

## FR-002 / FR-003 / FR-004 / DESIGN-REQ-003 / DESIGN-REQ-004 / DESIGN-REQ-005

Decision: Missing. Add runtime command parsing and metadata generation at snapshot construction time for objective and step instruction fields.
Evidence: Repository search found no existing `runtimeCommand` or `RuntimeCommandInvocation` model. `TaskExecutionSpec` and `TaskStepSpec` currently allow extra fields but do not validate command metadata.
Rationale: The authoritative snapshot is the durable reconstruction boundary for task execution, edit, rerun, and audit. This is the correct backend authority point for command metadata.
Alternatives considered: Adding metadata only in frontend state was rejected because backend normalization must remain authoritative.
Test implications: Unit tests must cover objective and step metadata shape, source paths, target runtime, and target step id.

## FR-005 / DESIGN-REQ-006

Decision: Missing. Add a conservative parser for first-character slash-leading instructions.
Evidence: No parser matching the documented grammar exists in task contract code.
Rationale: The parser must distinguish valid command tokens, opaque provider command lines, escaped slash text, and ordinary path-like text without rewriting input.
Alternatives considered: A provider allowlist was rejected because unknown valid commands must pass through for slash-capable runtimes.
Test implications: Unit tests must cover `/review`, `/review args`, `/future-command now`, `/provider.command now`, leading whitespace, `/src/app.ts is broken`, and empty instructions.

## FR-006 / FR-010 / DESIGN-REQ-007 / DESIGN-REQ-019

Decision: Missing. Use hint status as enrichment metadata only.
Evidence: No command hint catalog is currently present in the task contract layer.
Rationale: The story requires unknown valid commands to be accepted as opaque runtime invocations. Known hints can be introduced as a compact built-in seed only for status classification, but absence of a hint cannot reject.
Alternatives considered: Deferring hint status entirely was rejected because the Jira brief requires deterministic `hintStatus`.
Test implications: Unit tests must assert unknown valid commands receive opaque hint status and are accepted.

## FR-007 / DESIGN-REQ-008

Decision: Missing. Record escaped literal metadata with non-executable recognition mode.
Evidence: Current instruction normalization strips surrounding whitespace but does not identify escaped leading slash text.
Rationale: Escaped text must remain auditable as a deliberate literal and must not later be treated as an executable command by default.
Alternatives considered: Treat escaped slash text as no metadata was rejected because downstream renderers need an explicit safety signal.
Test implications: Unit tests must assert `\\/review` creates escaped metadata and `requiresRuntimeRecognition=false`.

## FR-008 / DESIGN-REQ-010

Decision: Missing. Reject malformed or conflicting frontend-supplied command metadata at the backend contract boundary.
Evidence: `TaskExecutionSpec` and `TaskStepSpec` allow extra fields today, so supplied `runtimeCommand` would currently pass through without validation in model dumps but is not included in the authoritative snapshot.
Rationale: The backend must be authoritative. Supplied metadata that conflicts with parsed instructions creates audit and execution risk.
Alternatives considered: Silently dropping supplied metadata was rejected because malformed command metadata should fail fast when clients send inconsistent authoritative-looking fields.
Test implications: Unit tests must cover conflicting command metadata and malformed metadata for objective and step payloads.

## FR-009

Decision: Missing. Add default policy-derived recognition states for unsupported runtimes and malformed ordinary path-like input without changing accepted authored text.
Evidence: Runtime modes are normalized in `TaskRuntimeSelection`, but no runtime slash capability or policy exists.
Rationale: The source design calls for warning/reject policy. For this story, compact deterministic metadata can record warning literal states while preserving text for accepted snapshots.
Alternatives considered: Rejecting all unsupported runtime slash text was rejected because the recommended default is warning literal handling.
Test implications: Unit tests must assert malformed path-like and unsupported-runtime inputs do not become executable runtime commands.

## Integration Boundary

Decision: Use existing task contract tests as the primary boundary and add integration-shaped assertions around `CanonicalTaskPayload` plus `build_authoritative_task_input_snapshot()`.
Evidence: The task contract tests already validate canonical payload parsing and snapshot output for task-shaped submissions.
Rationale: This catches the real payload shape used before workflow submission without requiring Docker-backed integration for a pure contract change.
Alternatives considered: Compose-backed integration tests were rejected for this story unless API/workflow submission code changes become necessary.
Test implications: Run targeted pytest during development and full `./tools/test_unit.sh` before final verification.

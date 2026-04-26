# Research: Serialized Compose Desired-State Execution

## FR-001 / FR-002 / DESIGN-REQ-002 Per-Stack Serialization

Decision: Add a small per-stack lock manager used by the deployment update executor. Lock acquisition is nonblocking; contention raises non-retryable `ToolFailure` with `DEPLOYMENT_LOCKED` before side effects.
Evidence: `api_service/services/deployment_operations.py` queues a typed plan but has no runtime lock. `moonmind/workflows/skills/tool_dispatcher.py` supports non-retryable `ToolFailure`.
Rationale: The lock belongs at execution time because API validation cannot prevent two already-queued or retried tool invocations from racing.
Alternatives considered: Queueing same-stack updates was rejected for this story because the Jira acceptance criterion permits rejection or explicit queueing policy, and no queueing policy exists yet.
Test implications: Unit and hermetic integration tests must prove lock contention happens before runner/store calls.

## FR-003 / FR-004 / FR-005 / DESIGN-REQ-001 / DESIGN-REQ-003 Desired-State Persistence

Decision: Add an injectable desired-state store boundary. The executor captures before state first, then persists a structured desired-state payload containing stack, repository, requested reference, optional resolved digest, reason, timestamp, and source run ID before any Compose `up`.
Evidence: `docs/Tools/DockerComposeUpdateSystem.md` sections 9.2, 10.3, and 10.4 define the required order. No current production module persists this state.
Rationale: A boundary allows a later deployment-control worker to write an allowlisted env file or durable state store while unit tests use an in-memory implementation.
Alternatives considered: Writing directly to an env file in this story was rejected because the allowlisted deployment path is operator-specific and must not be guessed in tests.
Test implications: Unit tests assert ordered fake events and exact desired-state payload fields.

## FR-006 / FR-007 / FR-008 / DESIGN-REQ-004 Command Construction

Decision: Implement command construction as typed argument vectors, not shell strings. Pull command is always built before up. `changed_services` omits `--force-recreate`; `force_recreate` includes it. `removeOrphans` and `wait` only control `--remove-orphans` and `--wait`.
Evidence: Existing API tests reject arbitrary `command` and `composeFile` fields. `deployment_tools.py` schema defines the closed modes and booleans.
Rationale: Argument vectors keep the execution boundary auditable and prevent hidden shell expansion.
Alternatives considered: Reusing user-provided command text is forbidden by MM-518/MM-519 and the source design.
Test implications: Unit tests compare exact command argument lists for all mode/flag combinations.

## FR-009 / FR-010 / DESIGN-REQ-005 Evidence And Verification Result

Decision: Add an injectable evidence writer and runner verification result. The executor returns `SUCCEEDED` only when verification succeeds; otherwise it returns `FAILED` with before, after, command log, and verification refs when available.
Evidence: `deployment_tools.py` output schema already allows artifact refs and status values `SUCCEEDED`, `FAILED`, and `PARTIALLY_VERIFIED`.
Rationale: Verification must be authoritative for final status and produce evidence even on failure.
Alternatives considered: Raising on verification failure was rejected because the source design requires a structured result with verification evidence, not only an exception.
Test implications: Unit tests prove failed verification does not report `SUCCEEDED`; integration test validates tool-dispatch output shape.

## FR-011 / DESIGN-REQ-006 Runner Modes

Decision: Model runner mode as a closed execution-boundary value: `privileged_worker` or `ephemeral_updater_container`. Runner image is not part of tool inputs and cannot be supplied by callers.
Evidence: `deployment_tools.py` has no runner image input and rejects additional properties; API tests reject arbitrary path/command fields.
Rationale: The runner mode must be deployment-controlled so privileged Docker access is never operator-provided through the task payload.
Alternatives considered: Exposing runner image in tool inputs was rejected by source section 11.3.
Test implications: Unit tests validate accepted runner modes and fail-fast unsupported modes.

## FR-012 Traceability

Decision: Preserve `MM-520`, source sections, and source IDs in all artifacts and verification evidence.
Evidence: `spec.md` preserves the full normalized Jira preset brief.
Rationale: Final verification and pull-request metadata need the Jira key and source mapping.
Alternatives considered: Summary-only traceability was rejected because existing MoonSpec verification depends on preserved input.
Test implications: Traceability grep and final verification.

# Research: Deployment Verification, Artifacts, and Progress

## FR-001 / FR-002 Verification Success Gate

Decision: Preserve the existing verification-gates-success shape and make status derivation explicit.
Evidence: `DeploymentUpdateExecutor.execute()` now derives terminal status through `_verification_final_status()`, returning `SUCCEEDED` only for proven success and returning `FAILED` or `PARTIALLY_VERIFIED` for unproven desired state.
Rationale: The runner remains the boundary that proves Compose/smoke-check state; the executor should classify the result consistently and never infer success from command completion alone.
Alternatives considered: Re-run verification inside the executor; rejected because runner implementations own environment-specific checks.
Test implications: Unit tests for succeeded, failed, and partial status classification; integration dispatch test for structured output.

## FR-003 Partial Verification

Decision: Extend `ComposeVerification` with an optional `status` field constrained to `SUCCEEDED`, `FAILED`, or `PARTIALLY_VERIFIED`; `succeeded=True` still maps to `SUCCEEDED` and unsupported status values fail closed.
Evidence: `ComposeVerification.status` now carries explicit terminal status values, the tool output schema accepts the audit-enriched result shape, and invalid statuses raise `DEPLOYMENT_VERIFICATION_INVALID`.
Rationale: This is the narrowest change that represents partial verification explicitly without changing the public tool contract.
Alternatives considered: Use ad hoc `details["status"]`; rejected because it hides a billing/operationally relevant status in untyped detail data.
Test implications: Unit test partial verification result and invalid status fail-fast behavior.

## FR-004 / FR-005 Artifact Completeness

Decision: Keep existing fail-closed artifact checks and add status coverage proving all final statuses include artifact refs.
Evidence: Executor raises `DEPLOYMENT_EVIDENCE_INCOMPLETE` when after, command, or verification refs are missing, and partial-status coverage verifies before, command, verification, and after refs remain present.
Rationale: Behavior is present but needs MM-521-specific coverage for partial verification.
Alternatives considered: Make evidence refs optional on failures; rejected by source design.
Test implications: Unit and integration tests assert refs on successful and partial outcomes.

## FR-006 Audit Metadata

Decision: Build compact audit metadata from executor context and typed inputs, include it in evidence payloads and final outputs.
Evidence: Audit metadata is now constructed from context and typed inputs, attached to verification evidence and final outputs, and covered by unit tests for run/workflow/task IDs, operator role, mode, options, status, timestamps, and failure reason.
Rationale: Audit metadata belongs at the executor boundary because it knows lifecycle timing, final status, requested image, and context identifiers.
Alternatives considered: Leave audit to the API queue record; rejected because MM-521 requires audit output for every run and artifacts.
Test implications: Unit tests assert audit fields on verification evidence and final outputs.

## FR-007 Redaction

Decision: Apply recursive redaction immediately before evidence writer calls.
Evidence: `_redact_sensitive()` now recursively processes evidence payloads before publication, and unit tests cover nested secret-like keys and values in state and command output.
Rationale: Redacting at publication boundary protects every artifact kind while preserving runner internals for hermetic tests.
Alternatives considered: Require every runner to redact; rejected because it is easy to miss and less enforceable.
Test implications: Unit test nested secret-like keys and values in state and command output.

## FR-008 Progress Lifecycle

Decision: Record lifecycle progress events as compact `{state, message}` entries in `ToolResult.progress`, using documented states for phases reached by this executor.
Evidence: `ToolResult.progress` now contains bounded lifecycle events, current state, percent, and short messages; tests verify raw command output is absent from progress.
Rationale: The executor already knows phase order; compact state/message events satisfy Mission Control needs without embedding command output.
Alternatives considered: Stream live progress externally only; rejected because final output still needs deterministic evidence in tests.
Test implications: Unit and integration tests assert lifecycle states and absence of raw command output in progress.

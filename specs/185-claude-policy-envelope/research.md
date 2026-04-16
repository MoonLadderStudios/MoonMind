# Research: Claude Policy Envelope

## Runtime Boundary Placement

Decision: Implement MM-343 as Pydantic contracts and deterministic policy-resolution helpers in `moonmind/schemas/managed_session_models.py`.

Rationale: MM-342 already established Claude managed-session records in this module, and this story depends on those records without requiring live provider calls or storage. Keeping policy envelopes at the schema boundary gives workflows and adapters compact typed payloads without embedding large source material in workflow history.

Alternatives considered: A new service module was considered, but the current story is focused on fixture-backed contract behavior and has no persistence or adapter I/O. A separate service can consume these contracts later when provider-specific fetching is introduced.

## Source Precedence

Decision: Model server-managed and endpoint-managed sources explicitly and provide a resolver that chooses server-managed when non-empty and supported, otherwise endpoint-managed when non-empty and supported.

Rationale: The source design and Jira brief both require server-managed settings to win when present, endpoint-managed settings to apply only when server-managed settings are empty or unsupported, and lower-scope settings to be observability-only.

Alternatives considered: Merging server and endpoint settings was rejected because it would hide precedence evidence. Treating local settings as fallback enforcement was rejected because managed settings cannot be overridden by user or project sources.

## Fetch And Failure States

Decision: Preserve fetch states as explicit enum values and make `fail_closed` produce a blocked handshake with no permissive effective settings.

Rationale: Governance and startup behavior depend on distinguishing cache hits, successful fetches, non-fatal fetch failures, and fail-closed failures. A fail-closed refresh failure must not silently produce a permissive policy.

Alternatives considered: Collapsing fetch failures into a generic error was rejected because it would lose audit value and weaken startup decisions.

## BootstrapPreferences Semantics

Decision: Represent Claude BootstrapPreferences as `bootstrap_template` entries on the envelope rather than as managed defaults.

Rationale: The design calls out a semantic mismatch with Codex managed defaults. Labeling BootstrapPreferences as templates keeps the shared abstraction honest and avoids implying user-overridable native managed settings.

Alternatives considered: Mapping BootstrapPreferences into managed defaults was rejected because it would create misleading governance semantics.

## Visibility Model

Decision: Expose detailed fetch state and trust evidence through administrator/operator visibility while non-admin/user-facing output receives coarse status unless an authorization boundary permits detail.

Rationale: The Jira brief includes a clarification about visibility. This default preserves administrator audit needs without leaking unnecessary policy details to end users.

Alternatives considered: Showing all fetch details to all users was rejected as unnecessarily noisy and potentially sensitive. Hiding details entirely was rejected because administrators need audit evidence.

## Testing Strategy

Decision: Use focused pytest unit tests for resolver precedence, validation, fail-closed behavior, and bootstrap semantics; use integration-style boundary tests marked `integration_ci` for fixture scenarios crossing the public schema boundary.

Rationale: The story is a runtime contract boundary with no live provider dependency. Unit tests give fast TDD feedback, while integration-style boundary tests prove the payload shape and scenario matrix expected by workflows/adapters.

Alternatives considered: Provider verification tests were rejected because no live Claude provider behavior is in scope. Docker-backed tests are only needed for the full integration runner, not for focused schema validation.

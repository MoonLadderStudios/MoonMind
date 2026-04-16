# Research: OAuth Runner Bootstrap PTY

## Auth Runner Boundary

Decision: Implement MM-361 at the existing OAuth session activity/runtime boundary by having `oauth_session.start_auth_runner` resolve the provider bootstrap command from the OAuth provider registry and pass it into the terminal bridge startup helper.
Rationale: The existing workflow already invokes `oauth_session.start_auth_runner` with `session_id`, `runtime_id`, volume refs, and TTL. Resolving bootstrap details inside the activity keeps workflow history compact and preserves the worker-bound invocation shape for in-flight compatibility while still replacing placeholder runner behavior.
Alternatives considered: Adding bootstrap command fields to the workflow input or Temporal activity payload was rejected because it would spread provider configuration through workflow history and create unnecessary compatibility-sensitive payload churn.

## Provider Bootstrap Command Source

Decision: Treat the OAuth provider registry as the source of provider bootstrap commands for this story, and fail fast when a supported runtime has no non-empty bootstrap command.
Rationale: The registry already contains provider defaults such as runtime ID, session transport, auth volume name, mount path, and `bootstrap_command`; keeping command selection there preserves provider ownership boundaries and avoids hard-coded command behavior in the workflow.
Alternatives considered: Reading commands from environment variables or per-request fields was rejected for this story because it would introduce unvalidated runtime input and obscure the provider boundary that the source design requires.

## PTY Runner Startup

Decision: Replace placeholder `sleep` runner startup with a runner command path that starts the configured runner image with the selected auth volume mounted and the provider bootstrap command as the terminal-owned process.
Rationale: MM-361 specifically exists because the current runner starts an image with `sleep`, which does not exercise provider login behavior or own the terminal session. The planned boundary should make command execution explicit and testable without exposing generic exec.
Alternatives considered: Keeping `sleep` plus separate exec into the container was rejected because generic exec is explicitly out of scope and forbidden by the OAuth terminal boundary.

## Failure Redaction

Decision: Normalize runner startup and bootstrap failures into actionable, redacted reasons that identify missing Docker, mount/startup failure, missing provider command, command failure, or timeout without echoing full command output or credential-like values.
Rationale: The constitution and OAuth terminal design both prohibit raw credentials in workflow history, logs, artifacts, or browser responses. Startup errors still need operator value, so the reason must be categorical and bounded.
Alternatives considered: Returning raw stderr was rejected because provider login output can contain credential-like strings or environment details.

## Cleanup Semantics

Decision: Keep cleanup best-effort and idempotent through the existing stop/remove helper, while adding tests for no-container, already-stopped, partial-start, and failure cases.
Rationale: OAuth sessions can finish through success, failure, cancellation, expiry, or API-finalize paths; cleanup must be retry-safe and should not turn an already-finished terminal session into a hard failure.
Alternatives considered: Making stop failures terminal workflow errors was rejected because cleanup retry noise should not obscure the final OAuth session outcome.

## Unit Test Strategy

Decision: Use focused Python unit tests for provider registry validation, activity-to-runtime payload construction, terminal bridge runner startup command construction, failure redaction, generic exec rejection, and cleanup idempotency.
Rationale: These tests can be deterministic with mocked process creation and do not require real credentials, live providers, or Docker socket access.
Alternatives considered: Provider verification tests were rejected for this planning phase because MM-361 requires hermetic evidence first.

## Integration Test Strategy

Decision: Use `tests/integration/temporal/test_oauth_session.py` for hermetic Temporal boundary coverage of the existing OAuth workflow invocation shape and runner lifecycle terminal paths.
Rationale: MM-361 touches Temporal workflow/activity boundaries and must prove the workflow can still start, finalize, fail, cancel, expire, and stop the runner through the real worker-bound activity names.
Alternatives considered: Relying only on unit tests was rejected because the workflow/activity boundary is compatibility-sensitive and requires boundary evidence.

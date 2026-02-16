# Research: Unified CLI Single Queue Worker Runtime

## Decision 1: Keep one shared tooling image and extend `api_service/Dockerfile`

- Decision: Extend existing `api_service/Dockerfile` to install Claude CLI in the same builder stage that already installs `codex`, `gemini`, and `speckit`.
- Rationale: The repository already uses one shared image for API and workers; extending this path minimizes operational divergence and preserves existing fallback/stub install behavior.
- Alternatives considered:
  - Separate runtime image for Claude: rejected because it violates single-image goal and increases drift.
  - Install Claude at container startup: rejected because startup becomes network-dependent and non-reproducible.

## Decision 2: Move to one queue default with compatibility fallback

- Decision: Make `moonmind.jobs` the default queue and allow compatibility fallback behavior in settings for legacy queue environment variables during migration.
- Rationale: Meets single-queue requirement while reducing rollout risk for environments still exporting legacy variables.
- Alternatives considered:
  - Immediate hard cutover with no fallback: rejected because it raises outage risk during staged deploy.
  - Keep multiple queues permanently: rejected because it conflicts with source requirements.

## Decision 3: Enforce runtime mode validation in worker startup

- Decision: Validate `MOONMIND_WORKER_RUNTIME` at worker startup against `codex|gemini|claude|universal` and fail on invalid values.
- Rationale: Prevents silent misconfiguration and aligns runtime behavior to explicit operator intent.
- Alternatives considered:
  - Allow arbitrary runtime strings: rejected because it can silently degrade execution policy.
  - Infer runtime from queue name: rejected because queue names are no longer runtime-specific.

## Decision 4: Runtime-neutral payload baseline with optional targeting

- Decision: Keep the default job schema runtime-neutral and reserve targeted runtime requests for universal mode using optional metadata.
- Rationale: Avoids requeue hot-potato behavior and keeps single queue effective for heterogeneous workers.
- Alternatives considered:
  - Runtime-specific payload schemas by queue: rejected because this recreates coupling and queue partitioning.

## Decision 5: Health checks include all bundled CLIs

- Decision: Worker startup checks for `codex`, `gemini`, `claude`, and `speckit` availability and blocks consumption on failure.
- Rationale: Ensures image/tooling integrity before jobs are claimed.
- Alternatives considered:
  - Check only active runtime CLI: rejected because bundled image correctness could still be broken for other runtime modes.

## Repository Constraints Observed

- `.specify/scripts/bash/validate-implementation-scope.sh` is not present in this repository; runtime scope gates must be validated manually and reported explicitly.
- `.specify/scripts/bash/*.sh` currently use CRLF endings in this environment; direct execution fails without normalization wrappers.

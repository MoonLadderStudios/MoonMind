# Research: Canonical Create Page Shell

## Route Hosting

Decision: Preserve the existing FastAPI `/tasks/new` route and `/tasks/create` redirect alias.

Rationale: The current router already serves `task-create` with `build_runtime_config("/tasks/new")` and redirects `/tasks/create` to `/tasks/new`. The story needs validation and explicit shell behavior, not a new route.

Alternatives considered: Adding another route or client-side redirect was rejected because the source design requires `/tasks/new` as canonical and compatibility aliases to redirect rather than define product behavior.

## Canonical Section Exposure

Decision: Add stable `data-canonical-create-section` metadata and accessible labels to existing Create page regions.

Rationale: The current form already contains the required controls, but the section model is not explicit enough for tests or downstream tooling to assert the desired order. Metadata preserves visual behavior while making the shell contract deterministic.

Alternatives considered: Adding large visible headings was rejected because it would change product copy and layout more than needed for the story. Snapshot-style DOM tests were rejected because they are brittle.

## Optional Integration Behavior

Decision: Validate manual authoring with default boot payload settings that omit Jira and attachment policy while keeping task template behavior optional through the existing runtime config.

Rationale: Manual steps and submission already exist independently of Jira and image attachments. Tests should prove this contract without requiring disabled integrations to be simulated by external services.

Alternatives considered: Adding new feature flags was rejected because optional integration availability already flows through server runtime configuration.

## Test Strategy

Decision: Use focused Vitest tests for shell section order, edit/rerun reuse, REST submission endpoint, and optional-integration absence; use existing pytest route tests for server hosting and redirect behavior.

Rationale: These tests cover the browser and server boundaries named by the story without requiring Docker-backed services.

Alternatives considered: Playwright end-to-end coverage was rejected for this story because the existing unit/UI harness already exercises the required shell and request-shape behavior hermetically.

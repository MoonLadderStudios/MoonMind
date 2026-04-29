# Feature Specification: Managed Session Report Body

**Feature Branch**: `276-managed-session-report-body`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User request: "I want to be able to check Report for a task, provide instructions like the ones provided with this MoonMind task, and then get a report artifact which contains meaningful text (NOT \"workflow completed successfully\" with no detail). Task mm:e5fc42e2-de42-497a-bc90-67075dc5f1cc. Investigate what is missing for this to work properly and then implement a plan to fix it. Create a non-draft PR when done and tests are passing."

## User Story - Preserve Managed Session Final Text in Reports

**Summary**: As a MoonMind operator, I want a report-enabled Codex managed-session task to publish a `report.primary` artifact containing the agent's final answer, so report views are meaningful and not just generic workflow completion text.

**Goal**: When report output is enabled and a managed session records final assistant text, the fetch-result and report-publication boundary carries that text into the report artifact body.

**Independent Test**: Run the fetch-result and publish-artifacts activities with a completed managed-session record whose terminal result is generic but whose session summary contains `lastAssistantText`; verify the resulting metadata and `report.primary` body use the assistant text instead of generic completion wording.

## Investigation Evidence

- Task `mm:e5fc42e2-de42-497a-bc90-67075dc5f1cc` completed with `reportProjection.hasReport=true`, so report artifact publication and projection are wired.
- Its primary report preview contains only `# Final report` and `Completed with status completed`.
- Its `output.agent_result` artifact includes stdout/stderr/diagnostics/session refs, but no `operator_summary`, `assistantText`, or `lastAssistantText`.
- The Codex managed-session runtime records `lastAssistantText` in session summary metadata, and the report publisher already knows how to use `assistantText` / `lastAssistantText`; the missing bridge is fetch-result metadata enrichment for managed sessions.

## Requirements

- **FR-001**: `agent_runtime.fetch_result` MUST enrich completed managed-session results with the session summary's `lastAssistantText` when available.
- **FR-002**: Generic completion summaries such as `Completed with status completed` MUST NOT replace meaningful managed-session assistant text in report-producing paths.
- **FR-003**: `agent_runtime.publish_artifacts` MUST publish `report.primary` content from `assistantText`, `lastAssistantText`, or a non-generic operator summary before falling back to generic summaries.
- **FR-004**: Enrichment MUST remain best-effort and must not fail a completed task when session summary metadata cannot be loaded.
- **FR-005**: The final text carried through metadata MUST remain bounded and sanitized by existing summary redaction behavior.

## Success Criteria

- **SC-001**: Unit tests prove fetch-result metadata includes `lastAssistantText` and `operator_summary` when the managed session summary has meaningful assistant text and the terminal result is generic.
- **SC-002**: Unit tests prove report publication writes a `report.primary` body containing the assistant text and not only generic completion text.
- **SC-003**: Existing report projection and artifact contract tests continue to pass.

# Data Model: Simplify Orchestrate Summary

## Workflow Finish Summary

- **Purpose**: System-owned terminal summary contract for a `MoonMind.Run`.
- **Storage**: Existing `reports/run_summary.json` artifact plus workflow result/projection fields where available.
- **Fields in scope**:
  - `finishOutcome`: terminal outcome code, stage, and reason.
  - `publish`: publish mode, status, and reason.
  - `operatorSummary`: optional structured operator-facing summary.
  - `publishContext`: optional structured publish facts such as pull request context.
  - `lastStep`: optional final executed step metadata and diagnostics reference.
- **Validation rules**:
  - Must be produced by workflow finalization, not by a preset-authored final narrative step.
  - Must remain available on success, failure, cancellation, and no-change paths.
  - Must not include raw secrets or credential material.

## Orchestration Preset Step

- **Purpose**: Seeded task step that performs a bounded orchestration action.
- **Storage**: YAML seed files loaded into task template rows.
- **Fields in scope**:
  - `title`
  - `instructions`
  - `skill.id`
  - optional `requiredCapabilities`
- **Validation rules**:
  - Steps may perform state changes, validation, structured handoffs, or implementation work.
  - Steps must not exist solely to narrate generic workflow completion when the workflow finish summary owns that contract.

## Preset Structured Handoff

- **Purpose**: Domain-specific data needed after execution or before downstream gates.
- **Storage**: Existing step results or local handoff artifacts such as `artifacts/jira-orchestrate-pr.json`.
- **Fields in scope**:
  - Jira issue key.
  - Pull request URL.
  - Verification verdict.
  - Publish handoff or outcome data.
- **Validation rules**:
  - Must remain available when report-only steps are removed.
  - Must stay separate from generic final narration.
  - Must preserve MM-366 traceability in spec and verification artifacts.

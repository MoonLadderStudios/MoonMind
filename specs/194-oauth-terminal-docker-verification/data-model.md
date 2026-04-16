# Data Model: OAuth Terminal Docker Verification

## Docker Verification Attempt

- `command`: the exact verification command, normally `./tools/test_integration.sh`
- `environment_status`: whether Docker socket and daemon access are available
- `started_at`: timestamp when verification was attempted
- `result`: `pass`, `fail`, or `blocked`
- `summary`: short secret-free result summary
- `blocker`: exact blocker when result is `blocked`

Validation rules:
- A `pass` result requires Docker-backed integration execution, not unit-only evidence.
- A `blocked` result must include the exact missing prerequisite or environment failure.
- Summaries must not include tokens, credential file contents, auth headers, private keys, or raw environment dumps.

## Runtime Evidence Item

- `area`: managed Codex launch, Codex runtime startup, OAuth terminal runner, or PTY bridge
- `requirement_ids`: related `FR-*` and `DESIGN-REQ-*` IDs
- `evidence_source`: test name, command, or inspected runtime boundary
- `status`: `verified`, `partial`, `missing`, or `blocked`
- `notes`: secret-free explanation

Validation rules:
- Each in-scope source requirement must map to at least one evidence item.
- Evidence for report closure must include integration coverage where the acceptance scenario crosses Docker or Temporal runtime boundaries.

## Verification Report Update

- `report_path`: affected verification report
- `previous_verdict`: existing report verdict
- `new_verdict`: updated verdict after evidence review
- `evidence_refs`: related Docker verification attempts and runtime evidence items
- `remaining_work`: exact remaining work when verdict is not fully closed

Validation rules:
- `new_verdict` must remain ADDITIONAL_WORK_NEEDED unless Docker-backed evidence covers the report's stated gap.
- Report updates must preserve Jira issue key `MM-363` when referencing this closure attempt.

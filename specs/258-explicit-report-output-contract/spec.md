# Feature Specification: Explicit Report Output Contract

**Feature Branch**: `258-explicit-report-output-contract`  
**Created**: 2026-04-24  
**Status**: Draft  
**Input**: User request: "Implement your recommended path" for reliable report generation and artifact publication.

## User Story

As an operator, I want tasks to explicitly request a final report artifact so MoonMind can publish a canonical `report.primary` output reliably instead of depending on prompt wording or generic output artifacts.

## Requirements

- **FR-001**: Task creation MAY include `reportOutput.enabled=true` with bounded report metadata such as `reportType`, `title`, `primaryPath`, and `required`.
- **FR-002**: Workflow and agent-runtime boundaries MUST carry report-output intent as compact metadata only.
- **FR-003**: When report output is enabled, MoonMind MUST publish a report bundle through the existing artifact publication boundary using `report.primary` and final-report metadata.
- **FR-004**: Existing generic `output.primary`, `output.summary`, and `output.agent_result` behavior MUST remain unchanged when report output is not enabled.
- **FR-005**: If a required report cannot be published, the run MUST fail visibly instead of silently completing without a canonical report.

## Source Design

- `docs/Artifacts/ReportArtifacts.md` sections 7, 8, 9, 11, and 16.
- Existing report bundle publication service in `moonmind/workflows/temporal/artifacts.py`.
- Existing execution projection in `api_service/api/routers/executions.py`.

## Validation

- Unit tests cover explicit report-output propagation, report bundle publication, generic-output compatibility, and fail-closed behavior.
- Targeted unit runs verify the changed Temporal activity and workflow boundaries.

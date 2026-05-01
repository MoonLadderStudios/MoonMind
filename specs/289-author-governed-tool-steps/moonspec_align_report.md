# MoonSpec Alignment Report: Author Governed Tool Steps

## Updated

- `specs/289-author-governed-tool-steps/spec.md`: Preserves the MM-576 Jira preset brief, defines exactly one runtime user story, and maps DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, and DESIGN-REQ-020 to testable requirements.
- `specs/289-author-governed-tool-steps/plan.md`: Records repo gap analysis, constitution checks, and test strategy.
- `specs/289-author-governed-tool-steps/tasks.md`: Provides TDD-first frontend and unit validation tasks with MM-576 traceability.
- `specs/289-author-governed-tool-steps/research.md`, `data-model.md`, `quickstart.md`, and `contracts/governed-tool-authoring-ui.md`: Capture trusted tool discovery and dynamic Jira transition contracts.

## Key Decisions

- Dynamic Jira statuses use the trusted `/mcp/tools/call` `jira.get_transitions` path because source requirements forbid guessed statuses and raw Jira access.
- Tool grouping derives from the tool id namespace because the current trusted discovery payload exposes stable names before richer domain metadata.
- Existing manual Tool authoring remains the fallback because resilience and operator continuity are explicit story requirements.

## Validation

- Manual gate check confirmed spec, plan, research, data model, quickstart, tasks, checklist, and contract artifacts exist.
- Traceability check confirmed MM-576 and DESIGN-REQ-007/008/019/020 are preserved across feature artifacts.
- Standard prerequisite script was blocked by managed branch naming, not artifact content.

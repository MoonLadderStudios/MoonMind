# Requirements Traceability — Task Proposal Targeting Policy

| DOC-REQ | Functional Requirement(s) | Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/task_proposals/repositories.py`, API router | Regression tests confirm persistence keeps the canonical `taskCreateRequest` and promotion still uses `taskCreateRequest.payload.repository`. |
| DOC-REQ-002 | FR-001 | `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/task_proposals/repositories.py`, notification pipeline | Unit tests verify dedup remains `repository + normalized title`, repository-aware notifications stay intact, and human-review gating is still required before promotion. |
| DOC-REQ-003 | FR-002 | `moonmind/config/settings.py`, `CodexWorkerConfig.from_env`, env docs | Config tests assert `MOONMIND_PROPOSAL_TARGETS` parsing + fallback, worker tests confirm default-only/project-only routing. |
| DOC-REQ-004 | FR-003 | Worker `_maybe_submit_task_proposals`, config defaults | Worker tests rewrite MoonMind proposals to `MOONMIND_CI_REPOSITORY` and API tests reject mismatched repos. |
| DOC-REQ-005 | FR-004 | `moonmind/workflows/agent_queue/task_contract.py`, `moonmind/schemas/agent_queue_models.py` | Contract tests covering valid/invalid `proposalPolicy` payloads and end-to-end job normalization. |
| DOC-REQ-006 | FR-006 | Worker normalization + `TaskProposalService.create_proposal`, dashboard view | Worker/API tests enforce `[run_quality]` titles, category, tags from approved set; UI tests confirm filter chips. |
| DOC-REQ-007 | FR-008 | `TaskProposalService` priority mapping | Unit tests feed signal permutations and assert derived `reviewPriority` overrides when necessary. |
| DOC-REQ-008 | FR-007 | Worker metadata enrichment + API validation layer | Router + service tests require `triggerRepo`, `triggerJobId`, `signal` before persisting; worker tests guarantee metadata injection for CI proposals. |
| DOC-REQ-009 | FR-005 | Worker policy evaluator + severity gating helper | Tests cover `targets` precedence, `maxItems` ceilings, and skip logging when severity too low. |
| DOC-REQ-010 | FR-009 | Shared schemas + router/service compatibility | Schema regression tests ensure existing payloads still validate; new fixtures cover `proposalPolicy` + CI metadata. |
| DOC-REQ-011 | FR-010 | `dashboard.js` filter controls + view model | Front-end tests verify filtering by repository/category/tag plus display of origin metadata. |
| DOC-REQ-012 | FR-011 & FR-012 | Worker + API integration, task generation/test suites | End-to-end unit tests simulate project-only, MoonMind-only, and dual-target runs; tasks include runtime + validation subtasks proving metadata + tag presence. |

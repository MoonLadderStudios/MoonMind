# Implementation Plan: Jira Chain Blockers

**Branch**: `177-jira-chain-blockers` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/177-jira-chain-blockers/spec.md`

## Summary

Implement MM-339 from the TOOL board by extending the existing Jira Breakdown export path so ordered story output can request a dependency mode. The plan adds a narrow trusted Jira issue-link action, carries `none` and `linear_blocker_chain` through the seeded preset, agent-skill instructions, and deterministic `story.create_jira_issues` tool, and verifies issue creation, link creation, fallback, retry/reuse, and validation behavior with focused unit tests.

## Technical Context

**Language/Version**: Python 3.12 + YAML seed templates  
**Primary Dependencies**: Pydantic v2, FastAPI/MCP tool registry, `httpx`, existing Jira integration service, Temporal story-output tool handlers, task preset catalog  
**Storage**: No new persistent storage; deterministic outputs carry issue mappings and link results  
**Unit Testing**: `pytest` via `./tools/test_unit.sh`  
**Integration Testing**: Hermetic pytest integration tier via `./tools/test_integration.sh` when required; this story's high-risk boundaries are covered by focused unit tests with stubbed Jira services plus existing task-template expansion tests  
**Target Platform**: MoonMind API/worker runtime on Linux containers  
**Project Type**: Python backend service and workflow runtime with seeded task presets and agent skill instructions  
**Performance Goals**: One Jira link operation per adjacent story pair; no additional unbounded scans beyond existing issue reuse checks  
**Constraints**: Use MoonMind's trusted Jira boundary only; preserve fallback behavior; fail fast for unsupported dependency modes; do not add raw Jira mutation from agent shells; keep Jira provider errors sanitized  
**Scale/Scope**: Ordered Jira export for one breakdown result, typically tens of stories; supports `none` and linear adjacent blocker chains only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The implementation extends existing Jira integration and runtime tool contracts rather than creating a new agent execution path.
- **II. One-Click Agent Deployment**: PASS. No new required external services or deployment prerequisites.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior remains behind the Jira integration module and MCP/tool boundary.
- **IV. Own Your Data**: PASS. Outputs are local run results and artifacts; no new SaaS persistence.
- **V. Skills Are First-Class and Easy to Add**: PASS. Agent skill instructions stay aligned with deterministic tool contracts.
- **VI. Design for Deletion / Thick Contracts**: PASS. Dependency mode and link results are explicit contracts that can outlive the current preset implementation.
- **VII. Powerful Runtime Configurability**: PASS. Behavior is selected by explicit runtime input, not hardcoded globally.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within preset seed, agent skill docs, story-output tool, Jira models/service/client, and tests.
- **IX. Resilient by Default**: PASS. Partial failures and retry/reuse outcomes are explicit; idempotency is part of the story.
- **X. Facilitate Continuous Improvement**: PASS. Export results report created/reused issues and link outcomes for later diagnosis.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan follows `spec.md` and will produce TDD tasks before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. In-flight planning remains under `specs/177-jira-chain-blockers`; no canonical doc migration checklist is added.
- **XIII. Pre-Release Compatibility Policy**: PASS. Internal contracts will be updated in one change without compatibility aliases; Temporal-facing workflow payload compatibility is not changed.

## Project Structure

### Documentation (this feature)

```text
specs/177-jira-chain-blockers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-dependency-links.md
│   └── story-output-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── data/task_step_templates/jira-breakdown.yaml

.agents/
└── skills/
    ├── jira-issue-creator/SKILL.md
    └── moonspec-breakdown/SKILL.md

moonmind/
├── integrations/jira/
│   ├── client.py
│   ├── models.py
│   └── tool.py
└── workflows/temporal/
    └── story_output_tools.py

tests/
└── unit/
    ├── api/test_task_step_templates_service.py
    ├── integrations/test_jira_tool_service.py
    └── workflows/temporal/test_story_output_tools.py
```

**Structure Decision**: Extend the existing backend/runtime modules that already own Jira creation and preset expansion. No frontend, database, or new service module is required.

## Complexity Tracking

No constitution violations.

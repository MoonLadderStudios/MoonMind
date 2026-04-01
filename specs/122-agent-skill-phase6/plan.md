# Implementation Plan: Agent Skill System Phase 6

**Input**: Implementation of the specification for Agent Skill System Phase 6 (`spec.md`).

## 1. Context and Goals
Phase 6 spans API modeling, backend execution endpoint hardening, and frontend Mission Control UI changes. The goal is to surface the logic generated in phases 3, 4, and 5 into the Task Dashboard using Test-Driven Development (TDD). 

## 2. Technical Design

### API Layer
- **Executions Router**: Update `GET /tasks/{task_id}` and `GET /proposals/{proposal_id}` to serialize skill datasets.
- **Metadata RBAC**: Implement a `prune_debug_skill_metadata` utility that removes raw prompt indexes, internal paths, and materialization manifest details from unprivileged access requests to prevent unauthorized inspection.

### UI Layer (React)
- **Submit Task (`tasks-list.tsx` / `dashboard.js`)**: Hook into the existing `/api/tasks/skills` endpoint to populate a multiselect component detailing include/exclude skillsets without overcrowding existing modal parameters.
- **Detail View (`task-detail.tsx`)**: Implement a new component array returning snapshot ID, chosen versions, and the source precedence.
- **Skill Provenance Component**: A new component returning an aesthetic badge styled corresponding to the provenance (e.g. built-in vs repo vs local-only).
- **Proposal Review (`proposals.tsx`)**: Insert read-only variants indicating defaults versus active selection.

## 3. Test Strategy
We will abide strictly to TDD:
- Add a new integration test ensuring executions metadata filters unprivileged queries.
- Develop tests in `task-detail.test.tsx` verifying provenance conditional rendering.
- Complete `test_task_create_submit_browser.py` asserting selection functionality.

## 4. Dependencies
No novel external dependencies needed. Relies heavily on endpoints built in Phase 5 and standard React/Tailwind.

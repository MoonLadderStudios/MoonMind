# Implementation Plan: Generate and Validate Proposal Candidates

**Branch**: `change-jira-issue-mm-596-to-status-in-pr-32216b84` | **Date**: 2026-05-07 | **Spec**: specs/310-generate-proposal-candidates/spec.md
**Input**: Single-story feature specification from `specs/310-generate-proposal-candidates/spec.md`

## Summary

Implement MM-596 by strengthening the existing Temporal proposal activities so generation derives follow-up candidates from durable activity input without side effects, preserves compact skill/preset provenance when reliable, and validates candidate `taskCreateRequest` payloads before trusted submission or delivery. The implementation will focus on `TemporalProposalActivities` and existing task proposal service contract validation, with unit and boundary tests covering canonical task payload acceptance, `agent_runtime` rejection, provenance behavior, and generation/submission separation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `proposal_generate` consumes workflow payload and next-step fields but ignores several durable metadata fields | preserve compact canonical task metadata and validate generated payload shape | unit + boundary |
| FR-002 | implemented_unverified | `proposal_generate` currently returns data and has no service dependency | add boundary assertion that generation never calls proposal service | boundary |
| FR-003 | partial | `TaskProposalService` validates canonical payloads when service is present; `proposal_submit` can count invalid candidates as submitted when no service is wired | add pre-delivery validation in `proposal_submit` | unit + boundary |
| FR-004 | implemented_unverified | `CanonicalTaskPayload` and task contract allow `tool.type = skill` for executable Tool steps | add focused proposal submission test | unit |
| FR-005 | partial | `CanonicalTaskPayload` rejects unsupported tool types, but `proposal_submit` does not always validate before no-service delivery | add validation and focused rejection test | unit |
| FR-006 | partial | proposal generation preserves runtime/git/publish only | preserve task/step `skills` selectors and explicit skill selector metadata when present | unit |
| FR-007 | partial | proposal generation currently does not preserve `authoredPresets` or reliable `steps[].source` | copy canonical authored preset bindings and reliable step source metadata without unresolved includes | unit |
| FR-008 | implemented_unverified | current generation fabricates no provenance because it preserves none | add absent-provenance regression coverage | unit |
| FR-009 | partial | malformed candidate errors exist; canonical task validation errors may only surface through service | validate before delivery and redact errors | unit + boundary |
| FR-010 | implemented_unverified | activity catalog has `proposal.generate` and `proposal.submit` as separate activities | add topology/boundary assertion in focused tests | boundary |
| FR-011 | missing | new feature artifacts required | preserve MM-596 in artifacts, verification, and commit text | final verify |
| DESIGN-REQ-001 | partial | see FR-001 | same as FR-001 | unit + boundary |
| DESIGN-REQ-002 | partial | see FR-002, FR-006, FR-007, FR-008, FR-009 | same as mapped FRs | unit + boundary |
| DESIGN-REQ-003 | partial | see FR-003 and FR-009 | same as FR-003 and FR-009 | unit + boundary |
| DESIGN-REQ-004 | partial | see FR-003 | same as FR-003 | unit |
| DESIGN-REQ-005 | partial | see FR-004 and FR-005 | same as FR-004 and FR-005 | unit |
| DESIGN-REQ-006 | partial | see FR-006, FR-007, FR-008 | same as mapped FRs | unit |
| DESIGN-REQ-007 | implemented_unverified | separate activity names exist in `activity_runtime.py` and worker topology tests cover proposal activity family routing | add focused boundary assertion if missing | boundary |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK activity boundary helpers, Pydantic v2 canonical task contract, pytest/unittest async tests  
**Storage**: Existing `task_proposals` persistence only; no new tables or migrations  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused iteration with `python -m pytest` allowed before final full suite  
**Integration Testing**: pytest boundary/integration tests; no Docker-backed integration expected for this story  
**Target Platform**: MoonMind worker/control-plane runtime on Linux  
**Project Type**: Python backend workflow/runtime service  
**Performance Goals**: Proposal validation remains in-memory and bounded to the candidate list; no new network calls during generation  
**Constraints**: Generation has no side effects; submission is the first trusted side-effect boundary; large skill content and runtime materialization state stay out of candidate payloads  
**Scale/Scope**: One Temporal proposal activity slice for generated follow-up candidates

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The change strengthens orchestration contracts around existing agents and tasks.
- II. One-Click Agent Deployment: PASS. No new external service or deployment prerequisite.
- III. Avoid Vendor Lock-In: PASS. Uses canonical task payloads and Temporal activity boundaries, not provider-specific behavior.
- IV. Own Your Data: PASS. Uses durable run evidence and artifact refs/metadata; no external SaaS dependency.
- V. Skills Are First-Class and Easy to Add: PASS. Skill selectors remain compact refs and follow the canonical contract.
- VI. Design for Deletion / Thick Contracts: PASS. Validation lives at explicit contract boundaries.
- VII. Runtime Configurability: PASS. Existing proposal policy defaults remain configurable.
- VIII. Modular and Extensible Architecture: PASS. Work is scoped to proposal activity/service boundaries.
- IX. Resilient by Default: PASS. Invalid candidates fail before side effects with visible redacted errors.
- X. Facilitate Continuous Improvement: PASS. Feature directly improves follow-up proposal quality.
- XI. Spec-Driven Development: PASS. This feature has spec, plan, tasks, tests, implementation, and verification artifacts.
- XII. Canonical Documentation Separation: PASS. Runtime work is tracked under `specs/310-*`; canonical docs are not rewritten.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden translation layers are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/310-generate-proposal-candidates/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-candidate-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── temporal/
│   │   └── activity_runtime.py
│   └── task_proposals/
│       └── service.py
└── workflows/tasks/
    └── task_contract.py

tests/
└── unit/
    └── workflows/
        ├── temporal/
        │   └── test_proposal_activities.py
        └── task_proposals/
            └── test_service.py
```

**Structure Decision**: Reuse the existing proposal activity and service tests. No new package or persistence layer is required.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

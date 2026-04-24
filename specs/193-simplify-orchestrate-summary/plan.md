# Implementation Plan: Simplify Orchestrate Summary

**Branch**: `193-simplify-orchestrate-summary` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/193-simplify-orchestrate-summary/spec.md`

## Summary

Deliver MM-366 by removing generic final narrative report steps from the seeded Jira and MoonSpec orchestration presets while preserving the structured handoff data those presets need. Existing `MoonMind.Run` finalization already produces the canonical `reports/run_summary.json` finish summary, so the implementation will keep summary ownership there and add regression coverage proving the presets no longer depend on report-only steps.

## Technical Context

**Language/Version**: Python 3.12 backend tests plus YAML seed templates 
**Primary Dependencies**: Existing task template catalog service, PyYAML seed loading, pytest, Temporal workflow finish summary implementation 
**Storage**: Existing seeded task template rows and artifact-backed `reports/run_summary.json`; no new persistent storage 
**Unit Testing**: Pytest through `./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` during iteration and `./tools/test_unit.sh` for final verification 
**Integration Testing**: Existing template expansion unit boundary covers seed catalog behavior; required compose-backed integration suite remains `./tools/test_integration.sh` when workflow runtime behavior changes beyond seeds 
**Target Platform**: MoonMind API service and Temporal-backed task execution 
**Project Type**: Backend control-plane seed configuration with workflow contract verification 
**Performance Goals**: No extra runtime steps for generic orchestration completion summaries 
**Constraints**: Preserve MM-366 traceability; keep pull request and Jira Code Review gates intact for `jira-orchestrate`; keep MoonSpec publish handoff facts available without a final narrative report step; do not redesign finish summary system 
**Scale/Scope**: Two global task presets and their seed expansion tests, plus contract documentation for summary ownership

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The change simplifies MoonMind orchestration around existing agent steps and finalization.
- II. One-Click Agent Deployment: PASS. Seed template changes require no new services or prerequisites.
- III. Avoid Vendor Lock-In: PASS. The summary ownership rule applies to presets and workflow finalization, not a provider-specific runtime.
- IV. Own Your Data: PASS. Structured outputs and final summary artifacts remain MoonMind-owned.
- V. Skills Are First-Class and Easy to Add: PASS. Skill steps remain explicit and only report-only narration steps are removed.
- VI. Scientific Method/Test Anchor: PASS. Regression tests cover preset expansion before implementation is accepted.
- VII. Runtime Configurability: PASS. No runtime configuration is hardcoded or removed.
- VIII. Modular and Extensible Architecture: PASS. Preset definitions and workflow finalization keep separate ownership.
- IX. Resilient by Default: PASS. Failure and cancellation summaries rely on finalization, which still runs when late preset steps do not.
- X. Facilitate Continuous Improvement: PASS. Finish summary remains the consistent outcome surface.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, implementation, and verification are produced for MM-366.
- XII. Canonical Documentation Separation: PASS. Runtime implementation notes live under `specs/` and transient Jira input under `local-only handoffs`; canonical docs only need targeted contract clarification if touched.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/193-simplify-orchestrate-summary/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── preset-summary-ownership.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/data/task_step_templates/
├── jira-orchestrate.yaml
└── moonspec-orchestrate.yaml

tests/unit/api/
└── test_task_step_templates_service.py

docs/Tasks/
└── TaskFinishSummarySystem.md
```

**Structure Decision**: Keep the runtime behavior change in seeded preset YAML and prove it at the task-template catalog boundary. Workflow finalization code is read as an existing contract and should only change if tests show it cannot carry the required summary facts.

## Complexity Tracking

No constitution violations.
